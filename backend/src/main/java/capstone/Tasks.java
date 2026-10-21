package capstone;

import java.time.Instant;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.springframework.amqp.core.AmqpTemplate;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import static capstone.InternalApi.m;

/**
 * task 모듈 — AI 작업 접수, 진행 이벤트 저장·중계(SSE), 승인 결정 후 재개 (아키텍처 4.3·4.4·9장).
 *
 * <p><b>agent에 바로 보내지 않고 대기열을 거친다</b> ({@link TaskQueue}). 접수·재개 요청을 {@code ai.tasks}에 넣고,
 * {@link #deliver}가 꺼내 agent에 보낸다. agent가 죽어 있어도 작업이 사라지지 않고 30초·2분 뒤 다시 보낸다.
 *
 * <p><b>저장은 DB, 연결만 메모리.</b> 작업(ai_task)과 이벤트(task_event)는 DB에 두어 backend를 재시작해도 남는다.
 * 지금 화면에 열려 있는 SSE 연결(emitter)만 메모리에 둔다 — 연결은 재시작하면 어차피 끊기고, 화면이 다시 붙으면
 * DB에서 처음부터 다시 보낸다.
 *
 * <p><b>금지</b>: LLM을 직접 부르지 않는다 (교재 5장). AI 호출은 전부 agent가 한다.
 */
// ponytail: seq를 매기는 자물쇠가 프로세스 안에만 있다. backend를 여러 대 띄우면 DB 시퀀스나 행 잠금으로 바꾼다
@RestController
@EnableScheduling
class Tasks {

    /** 작업 하나를 다시 볼 때 필요한 것. 이벤트는 DB에서 따로 읽는다 */
    record Task(String id, String userId, String role, String status) {
        boolean finished() {
            return "DONE".equals(status) || "FAILED".equals(status);
        }
    }

    /** 지금 이 작업을 보고 있는 화면들. 같은 작업을 두 탭에서 열 수도 있어서 목록이다 */
    private final Map<String, List<SseEmitter>> emitters = new ConcurrentHashMap<>();
    /** 작업별 자물쇠. "쌓인 이벤트 재전송"과 "새 이벤트 도착"이 겹쳐 중복·누락되지 않게 */
    private final Map<String, Object> locks = new ConcurrentHashMap<>();

    private final JdbcTemplate db;
    private final ObjectMapper json;
    private final Approvals approvals;
    /** agent 호출용. compose 안에서는 서비스 이름이 그대로 주소가 된다 (http://agent:8000) */
    private final RestClient agent;
    private final AmqpTemplate queue;

    Tasks(JdbcTemplate db, ObjectMapper json, Approvals approvals, @Value("${AGENT_URL:http://agent:8000}") String agentUrl,
          AmqpTemplate queue) {
        this.db = db;
        this.json = json;
        this.approvals = approvals;
        this.queue = queue;
        // 3초 제한 (아키텍처 4.2 "백엔드 → AI 서버 실행 요청"). agent는 202를 바로 준다. 제한이 없으면
        // agent가 멈췄을 때 대기열 소비자가 같이 멈춰 뒤의 작업이 전부 밀린다
        SimpleClientHttpRequestFactory http = new SimpleClientHttpRequestFactory();
        http.setConnectTimeout(3_000);
        http.setReadTimeout(3_000);
        this.agent = RestClient.builder().baseUrl(agentUrl).requestFactory(http).build();
    }

    // ------------------------------------------------------------------
    // 화면이 부르는 API
    // ------------------------------------------------------------------

    /**
     * ① 질문을 작업으로 접수한다 (아키텍처 9.1). 작업을 저장하고 202를 바로 돌려준 뒤 agent 호출은 뒤로 넘긴다.
     *
     * <p>요청자(JWT의 sub·role)를 작업에 저장하고 agent에 넘긴다. agent는 이 값을 {@code X-User-Id}·{@code X-Role}
     * 헤더로 도구 호출에 실어 보낸다 (contracts.md 1.1). 역할을 질문이나 도구 인자에서 받지 않는 이유:
     * 그건 사용자나 모델이 바꿀 수 있는 값이다.
     */
    @PostMapping("/api/ai/tasks")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, String> createTask(@RequestBody Map<String, String> body, @AuthenticationPrincipal Jwt user) {
        return Map.of("taskId", start(user.getSubject(), user.getClaimAsString("role"), body.getOrDefault("question", ""), "USER", null));
    }

    /**
     * 작업을 만들고 agent에 실행을 요청한다. 화면 질문(USER)과 자동 분석(SYSTEM, 9단계)이 같이 쓴다.
     *
     * @param whatif 사람이 물은 작업이면 null — 질문에서 뽑는 건 agent의 classify_intent가 한다.
     *               시스템이 만든 작업이면 backend가 이미 아는 변경을 채운다 (contracts.md 2.6)
     */
    String start(String userId, String role, String question, String trigger, List<Map<String, Object>> whatif) {
        String id = UUID.randomUUID().toString();
        // threadId는 taskId와 같게 쓴다. 승인 후 재개 때 agent checkpointer에서 같은 대화를 꺼내는 열쇠다
        db.update("insert into ai_task (task_id, thread_id, user_id, role, question, trigger) values (?, ?, ?, ?, ?, ?)",
                id, id, userId, role, question, trigger);
        Map<String, Object> req = new HashMap<>();
        req.put("taskId", id);
        req.put("threadId", id);
        req.put("userId", userId);
        req.put("role", role);
        req.put("question", question);
        req.put("whatif", whatif);
        enqueue(id, "start", req);
        return id;
    }

    /**
     * GET /api/ai/tasks/{id} — 작업 상태와 결과 (contracts.md 3.1). 이벤트를 구독하지 않고 결과만 볼 때.
     * 질문한 사람, 그리고 구매·팀장이 볼 수 있다 (기획안 2.3 "작업 기록 보기": 생산관리는 내 것만).
     */
    @GetMapping("/api/ai/tasks/{id}")
    Map<String, Object> get(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        Map<String, Object> t = row(id);
        // 자동 분석(SYSTEM)은 생산관리도 봐야 하는 결과다 (아키텍처 4.6). 그래서 PLANNER 역할로 만들어 금액이 없다
        if (!t.get("userId").equals(user.getSubject()) && !"SYSTEM".equals(t.get("trigger"))
                && !Auth.allows(user.getClaimAsString("role"), "BUYER"))
            throw new ApiError("ROLE_NOT_ALLOWED", "다른 사람의 작업입니다.", "생산관리 담당자는 자기 작업만 볼 수 있습니다.");
        // 제안별 최신 진행 상태 (agent의 PROPOSAL_PROGRESS). 여러 카드 중 일부가 아직 대기 중이라 작업이 끝나지 않아도
        // 승인 대기함이 거절 카드의 재검토 결과를 바로 보여 줄 수 있다 (decisions 6.30). 목록 API에는 넣지 않는다 — 50건마다 읽게 된다
        t.put("progress", db.queryForList("""
                select distinct on (payload->>'proposalId') payload::text from task_event
                where task_id = ? and type = 'PROPOSAL_PROGRESS'
                order by payload->>'proposalId', seq desc""", String.class, id).stream().map(this::read).toList());
        if (user.getSubject().equals(t.get("userId"))) t.put("reviewReady", approvals.reviewReady(id));
        return t;
    }

    /**
     * GET /api/ai/tasks?mine=true — 작업 목록, 최근 것부터 50개 (contracts.md 3.1).
     * 생산관리는 항상 자기 것만, 구매·팀장은 mine을 빼면 전체 (기획안 2.3).
     */
    @GetMapping("/api/ai/tasks")
    List<Map<String, Object>> list(@RequestParam(defaultValue = "false") boolean mine, @AuthenticationPrincipal Jwt user) {
        boolean onlyMine = mine || !Auth.allows(user.getClaimAsString("role"), "BUYER");
        // 생산관리의 "내 것"에는 자동 분석(SYSTEM)도 들어간다 — 생산관리가 꼭 알아야 하는 결과라서
        return db.queryForList("""
                select task_id from ai_task
                where (? = false or user_id = ? or (trigger = 'SYSTEM' and ? = 'PLANNER'))
                order by created_at desc limit 50""",
                String.class, onlyMine, user.getSubject(), user.getClaimAsString("role")).stream().map(this::row).toList();
    }

    /**
     * ⑦ 진행 이벤트 구독 (아키텍처 9.1·9.3).
     *
     * <p><b>지난 이벤트를 DB에서 처음부터 다시 보낸다.</b> 화면이 202를 받고 구독하기까지 몇 ms 사이에 이벤트가
     * 먼저 왔을 수 있고, 새로고침·재연결·backend 재시작 뒤에도 같은 화면을 다시 그릴 수 있어야 한다.
     * 화면은 seq로 중복만 거른다 (contracts.md 3.2). 이미 끝난 작업이면 다 보내고 바로 닫는다.
     *
     * <p>질문한 사람만 구독할 수 있다. 팀장은 승인 대기함으로 카드를 받는다.
     * 토큰은 헤더 또는 {@code ?access_token=}({@link Auth}).
     */
    @GetMapping(path = "/api/ai/tasks/{id}/stream", produces = "text/event-stream")
    SseEmitter stream(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        Task task = find(id);
        // 자동 분석은 누구나 따라볼 수 있다. PLANNER 역할로 돌아 금액이 없다 (ScreenApi.startDelayAnalysis)
        boolean system = "SYSTEM".equals(db.queryForObject("select trigger from ai_task where task_id = ?", String.class, id));
        if (!system && !task.userId().equals(user.getSubject()))
            throw new org.springframework.web.server.ResponseStatusException(HttpStatus.FORBIDDEN, "질문한 사람만 볼 수 있습니다");
        SseEmitter emitter = new SseEmitter(120_000L);  // 작업 전체 제한 120초 (아키텍처 4.2)
        synchronized (lock(id)) {
            for (Map<String, Object> e : events(id)) send(emitter, e);
            if (find(id).finished()) emitter.complete();
            else {
                List<SseEmitter> list = emitters.computeIfAbsent(id, k -> new ArrayList<>());
                list.add(emitter);
                emitter.onCompletion(() -> { synchronized (lock(id)) { list.remove(emitter); } });
            }
        }
        return emitter;
    }

    /**
     * POST /api/ai/tasks/{id}/approval {@code {approvalId, decision, reason}} — 팀장의 승인·거절 (contracts.md 3.1·3.3).
     *
     * <p>결정을 저장한 뒤 재개 요청을 대기열에 넣고 202를 바로 돌려준다. 결정 저장과 agent 호출을 묶지 않는다 —
     * agent가 죽어 있어도 팀장의 결정은 남아야 한다. 재개도 접수와 같은 대기열이라 agent가 살아나면 이어진다.
     */
    @PostMapping("/api/ai/tasks/{id}/approval")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, Object> decide(@PathVariable String id, @RequestBody Map<String, String> body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "APPROVER");
        find(id);
        Map<String, Object> card = approvals.decide(id, body.get("approvalId"), body.get("decision"), body.get("reason"), user.getSubject());
        runningIfNoneWaiting(id);
        Map<String, Object> req = new HashMap<>();
        req.put("threadId", id);
        req.put("approvalId", card.get("approval_id"));
        req.put("decision", card.get("status"));
        req.put("reason", card.get("reason"));
        enqueue(id, "resume", req);
        return Map.of("approvalId", card.get("approval_id"), "status", card.get("status"));
    }

    /** 구매 담당자의 작업 단위 검토. 과거 카드별 API로 일부만 보내는 것은 허용하지 않는다. */
    @PostMapping({"/api/approvals/{approvalId}/submit", "/api/approvals/{approvalId}/withdraw"})
    Map<String, Object> obsoleteReview(@PathVariable String approvalId) {
        throw ApiError.invalid("이제 작업 단위로 검토합니다.", "최신 화면에서 작업 전체를 보내거나 보내지 않음으로 처리하세요.");
    }

    @PostMapping("/api/ai/tasks/{id}/review/submit")
    Map<String, Object> submit(@PathVariable String id, @RequestBody Approvals.TaskReview body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "BUYER");
        return Map.of("cards", approvals.reviewTask(id, user.getSubject(), true, body));
    }

    @PostMapping("/api/ai/tasks/{id}/review/withdraw")
    Map<String, Object> withdraw(@PathVariable String id, @RequestBody Approvals.TaskReview body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "BUYER");
        List<Map<String, Object>> cards = approvals.reviewTask(id, user.getSubject(), false, body);
        runningIfNoneWaiting(id);
        for (Map<String, Object> card : cards) notifyWithdrawal(card);
        return Map.of("cards", cards);
    }

    private void notifyWithdrawal(Map<String, Object> card) {
        String id = (String) card.get("task_id");
        try {
            queue.convertAndSend(TaskQueue.TASKS, m("kind", "resume", "taskId", id, "attempt", 1,
                    "body", m("threadId", id, "approvalId", card.get("approval_id"), "decision", "WITHDRAWN", "reason", card.get("memo"))));
        } catch (Exception ignored) {
            // WITHDRAWN은 이미 DB에 영속화됐다. 아래 스캔이 재전송하므로 FAILED로 만들지 않는다.
        }
    }

    /** 새 테이블 없이 WITHDRAWN 기록을 복구 기준으로 삼는다. agent가 진행 확인을 보내면 재전송을 멈춘다. */
    @Scheduled(fixedDelay = 10000)
    void recoverWithdrawals() {
        List<String> ids = db.queryForList("""
                select a.approval_id from approval a join ai_task t on t.task_id = a.task_id
                where a.status = 'WITHDRAWN' and t.status not in ('DONE', 'FAILED')
                  and not exists (select 1 from task_event e where e.task_id = a.task_id
                    and e.type = 'PROPOSAL_PROGRESS' and e.payload->>'approvalId' = a.approval_id
                    and e.payload->>'proposalStatus' = 'WITHDRAWN')
                order by a.requested_at limit 100""", String.class);
        for (String id : ids) {
            Map<String, Object> card = approvals.card(id);
            runningIfNoneWaiting((String) card.get("task_id"));
            notifyWithdrawal(card);
        }
    }

    /**
     * 카드가 여러 장이면(decisions 6.29) 하나를 결정해도 나머지가 사람을 기다린다 — 팀장의 결정(PENDING)이든
     * 요청자의 검토(DRAFT)든. 그때는 "승인 대기"를 유지하고, 기다리는 카드가 없을 때만 AI 차례(RUNNING)로 넘긴다 (6.31·6.33)
     */
    private void runningIfNoneWaiting(String id) {
        db.update("""
                update ai_task set status = 'RUNNING' where task_id = ? and status = 'WAITING_APPROVAL'
                and not exists (select 1 from approval where task_id = ? and status in ('DRAFT', 'PENDING'))""", id, id);
    }

    // ------------------------------------------------------------------
    // 대기열 (TaskQueue)
    // ------------------------------------------------------------------

    /** 대기열에 넣는다. RabbitMQ에도 못 넣으면 바로 FAILED — 안 남기면 화면이 영원히 기다린다 */
    private void enqueue(String id, String kind, Map<String, Object> body) {
        try {
            queue.convertAndSend(TaskQueue.TASKS, m("kind", kind, "taskId", id, "attempt", 1, "body", body));
        } catch (Exception e) {
            addEvent(id, Map.of("type", "FAILED", "errorCode", "QUEUE_UNAVAILABLE",
                    "message", "작업 대기열에 넣지 못했습니다: " + e.getMessage()));
        }
    }

    /**
     * 대기열에서 하나 꺼내 agent에 보낸다. 실패하면 다음 재시도 대기열로, 마지막 시도까지 실패하면 DLQ로 보내고 FAILED.
     *
     * <p><b>이미 시작했거나 끝난 작업은 보내지 않는다.</b> backend가 처리 도중 죽으면 RabbitMQ가 같은 메시지를
     * 다시 준다. 그때 agent가 같은 작업을 두 번 돌리지 않게 한다 (아키텍처 7.1 "중복 방지"를 backend 쪽에서).
     * 접수는 agent가 첫 이벤트를 보내면 QUEUED가 아니게 되고, 재개는 작업이 끝나면 더 보낼 이유가 없다.
     */
    @RabbitListener(queues = TaskQueue.TASKS)
    void deliver(Map<String, Object> msg) {
        String id = (String) msg.get("taskId");
        boolean resume = "resume".equals(msg.get("kind"));
        List<String> status = db.queryForList("select status from ai_task where task_id = ?", String.class, id);
        if (status.isEmpty()) return;  // DB를 지운 뒤 남은 메시지. 다시 넣으면 영원히 돈다
        if (resume ? List.of("DONE", "FAILED").contains(status.getFirst()) : !"QUEUED".equals(status.getFirst())) return;
        try {
            agent.post().uri(resume ? "/agent/tasks/{id}/resume" : "/agent/tasks", id).body(msg.get("body")).retrieve().toBodilessEntity();
        } catch (Exception e) {
            int attempt = ((Number) msg.get("attempt")).intValue();
            Map<String, Object> next = new LinkedHashMap<>(msg);
            next.put("error", String.valueOf(e.getMessage()));
            if (attempt <= TaskQueue.RETRIES.size()) {
                next.put("attempt", attempt + 1);
                queue.convertAndSend(TaskQueue.RETRIES.get(attempt - 1), next);
                return;
            }
            if (msg.get("body") instanceof Map<?, ?> body && "WITHDRAWN".equals(body.get("decision")))
                return; // DB에 남은 보내지 않음 기록을 정기 스캔이 재전송한다.
            queue.convertAndSend(TaskQueue.DLQ, next);  // 사람이 원인을 볼 수 있게 마지막 오류와 함께 남긴다
            addEvent(id, Map.of("type", "FAILED", "errorCode", "AGENT_UNAVAILABLE",
                    "message", (resume ? "승인은 저장됐지만 " : "") + "AI 서버에 " + attempt + "번 보냈지만 닿지 않았습니다: " + e.getMessage()));
        }
    }

    // ------------------------------------------------------------------
    // agent → backend
    // ------------------------------------------------------------------

    /**
     * ⑦ agent → backend 진행 이벤트 콜백 (아키텍처 9.2). 계층상 아래에서 위로 가는 호출이지만,
     * 이벤트를 화면에 중계할 수 있는 건 backend뿐이라 아키텍처 3장이 허용한 예외다.
     *
     * <p>아직 없는 것: 서비스 토큰 확인 (9단계).
     */
    @PostMapping("/internal/tasks/{id}/events")
    Map<String, Object> receiveEvent(@PathVariable String id, @RequestBody Map<String, Object> event) {
        Task task = find(id);  // 없는 작업이면 404
        if ("APPROVAL_REQUIRED".equals(event.get("type"))) return approvalRequired(task, event);
        addEvent(id, event);
        return Map.of();
    }

    /**
     * APPROVAL_REQUIRED: 승인 카드를 저장하고 approvalId를 붙여 화면에 보낸다 (contracts.md 3.3).
     * 요청자가 BUYER·APPROVER가 아니면 403. 같은 이벤트 재전송이면 같은 카드를 주고 화면에는 다시 안 보낸다.
     * 금액은 질문한 사람의 역할대로 가려서 저장·전송한다 — 이 스트림은 질문한 사람만 본다.
     */
    private Map<String, Object> approvalRequired(Task task, Map<String, Object> event) {
        Map<String, Object> card = approvals.create(task.id(), task.userId(), task.role(), event);
        if ((boolean) card.get("created")) {
            Map<String, Object> e = new LinkedHashMap<>(event);
            e.put("approvalId", card.get("approval_id"));
            e.put("before", card.get("before"));
            e.put("after", card.get("after"));
            Masking.mask(e, task.role());
            addEvent(task.id(), e);
        }
        return Map.of("approvalId", card.get("approval_id"));
    }

    // ------------------------------------------------------------------
    // 이벤트 저장·전송
    // ------------------------------------------------------------------

    /**
     * 이벤트에 taskId·seq·ts를 붙여 저장하고 작업 상태를 바꾼 뒤, 보고 있는 화면 모두에게 보낸다.
     * seq를 backend가 매기는 이유: 순서를 아는 쪽이 이벤트를 모아 두는 쪽이다 (아키텍처 9.3).
     */
    void addEvent(String id, Map<String, Object> event) {
        synchronized (lock(id)) {
            Integer last = db.queryForObject("select max(seq) from task_event where task_id = ?", Integer.class, id);
            Map<String, Object> e = new LinkedHashMap<>(event);
            e.put("taskId", id);
            e.put("seq", last == null ? 1 : last + 1);
            e.put("ts", Instant.now().toString());
            String type = String.valueOf(e.get("type"));
            db.update("insert into task_event (task_id, seq, type, payload) values (?, ?, ?, ?::jsonb)", id, e.get("seq"), type, write(e));
            updateStatus(id, type, e);
            boolean finished = "DONE".equals(type) || "FAILED".equals(type);
            for (SseEmitter emitter : List.copyOf(emitters.getOrDefault(id, List.of()))) {
                send(emitter, e);
                if (finished) emitter.complete();
            }
        }
    }

    /** 이벤트 종류로 작업 상태를 정한다. 끝난 작업은 다시 바꾸지 않는다 */
    private void updateStatus(String id, String type, Map<String, Object> e) {
        switch (type) {
            case "DONE" -> db.update("""
                    update ai_task set status = 'DONE', answer = ?, result = ?::jsonb, sources = ?::jsonb, finished_at = now()
                    where task_id = ? and status not in ('DONE', 'FAILED')""",
                    e.get("answer"), write(e.get("result")), write(e.get("sources")), id);
            case "FAILED" -> db.update("""
                    update ai_task set status = 'FAILED', answer = ?, finished_at = now()
                    where task_id = ? and status not in ('DONE', 'FAILED')""", e.get("message"), id);
            case "APPROVAL_REQUIRED" -> db.update(
                    "update ai_task set status = 'WAITING_APPROVAL' where task_id = ? and status not in ('DONE', 'FAILED')", id);
            default -> db.update("update ai_task set status = 'RUNNING' where task_id = ? and status = 'QUEUED'", id);
        }
    }

    private List<Map<String, Object>> events(String id) {
        return db.queryForList("select payload::text from task_event where task_id = ? order by seq", String.class, id)
                .stream().map(s -> this.<Map<String, Object>>read(s)).toList();
    }

    /**
     * SSE 한 건 전송. 실패는 삼킨다 — 화면이 탭을 닫거나 새로고침하면 전송이 터지는데 그건 정상이다.
     * 여기서 예외를 올리면 다른 화면까지 못 받는다. 끊긴 화면은 다시 구독하면 처음부터 받는다.
     */
    private static void send(SseEmitter emitter, Map<String, Object> event) {
        try {
            emitter.send(SseEmitter.event().id(String.valueOf(event.get("seq"))).data(event));
        } catch (Exception ignored) {
        }
    }

    // ------------------------------------------------------------------
    // 작은 도우미
    // ------------------------------------------------------------------

    private Object lock(String id) {
        return locks.computeIfAbsent(id, k -> new Object());
    }

    Task find(String id) {
        List<Map<String, Object>> rows = db.queryForList("select task_id, user_id, role, status from ai_task where task_id = ?", id);
        if (rows.isEmpty()) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.NOT_FOUND);
        Map<String, Object> r = rows.getFirst();
        return new Task(id, (String) r.get("user_id"), (String) r.get("role"), (String) r.get("status"));
    }

    /** 화면에 내보내는 작업 한 건. 작업 API는 처음부터 camelCase(taskId)라 그대로 맞춘다 */
    private Map<String, Object> row(String id) {
        List<Map<String, Object>> rows = db.queryForList("""
                select task_id, user_id, role, question, trigger, status, answer, result::text as result, sources::text as sources,
                       created_at::text as created_at, finished_at::text as finished_at
                from ai_task where task_id = ?""", id);
        if (rows.isEmpty()) throw new ApiError("NOT_FOUND", "작업 " + id + "을(를) 찾을 수 없습니다.", "작업 목록(GET /api/ai/tasks)에서 ID를 확인하세요.");
        Map<String, Object> r = rows.getFirst();
        return m("taskId", r.get("task_id"), "userId", r.get("user_id"), "role", r.get("role"),
                "question", r.get("question"), "trigger", r.get("trigger"), "status", r.get("status"),
                "answer", r.get("answer"),
                "result", r.get("result") == null ? null : read((String) r.get("result")),
                "sources", r.get("sources") == null ? null : read((String) r.get("sources")),
                "createdAt", r.get("created_at"), "finishedAt", r.get("finished_at"));
    }

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @SuppressWarnings("unchecked")
    private <T> T read(String s) {
        try {
            return (T) json.readValue(s, new TypeReference<Object>() {});
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
