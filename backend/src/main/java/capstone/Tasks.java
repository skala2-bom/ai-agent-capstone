package capstone;

import java.time.Instant;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import static capstone.InternalApi.m;

/**
 * task 모듈 — AI 작업 접수, 진행 이벤트 저장·중계(SSE), 승인 결정 후 재개 (아키텍처 4.3·4.4·9장).
 *
 * <p><b>저장은 DB, 연결만 메모리.</b> 작업(ai_task)과 이벤트(task_event)는 DB에 두어 backend를 재시작해도 남는다.
 * 지금 화면에 열려 있는 SSE 연결(emitter)만 메모리에 둔다 — 연결은 재시작하면 어차피 끊기고, 화면이 다시 붙으면
 * DB에서 처음부터 다시 보낸다.
 *
 * <p><b>금지</b>: LLM을 직접 부르지 않는다 (교재 5장). AI 호출은 전부 agent가 한다.
 */
// ponytail: seq를 매기는 자물쇠가 프로세스 안에만 있다. backend를 여러 대 띄우면 DB 시퀀스나 행 잠금으로 바꾼다
@RestController
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

    Tasks(JdbcTemplate db, ObjectMapper json, Approvals approvals, @Value("${AGENT_URL:http://agent:8000}") String agentUrl) {
        this.db = db;
        this.json = json;
        this.approvals = approvals;
        this.agent = RestClient.create(agentUrl);
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
        CompletableFuture.runAsync(() -> {
            try {
                Map<String, Object> req = new HashMap<>();
                req.put("taskId", id);
                req.put("threadId", id);
                req.put("userId", userId);
                req.put("role", role);
                req.put("question", question);
                req.put("whatif", whatif);
                agent.post().uri("/agent/tasks").body(req).retrieve().toBodilessEntity();
            } catch (Exception e) {
                // 여기서 FAILED를 안 남기면 화면이 영원히 기다린다
                addEvent(id, Map.of("type", "FAILED", "errorCode", "AGENT_UNAVAILABLE", "message", String.valueOf(e.getMessage())));
            }
        });
        return id;
    }

    /**
     * GET /api/ai/tasks/{id} — 작업 상태와 결과 (contracts.md 3.1). 이벤트를 구독하지 않고 결과만 볼 때.
     * 질문한 사람, 그리고 구매·팀장이 볼 수 있다 (기획안 2.3 "작업 기록 보기": 생산관리는 내 것만).
     */
    @GetMapping("/api/ai/tasks/{id}")
    Map<String, Object> get(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        Map<String, Object> t = row(id);
        if (!t.get("userId").equals(user.getSubject()) && !Auth.allows(user.getClaimAsString("role"), "BUYER"))
            throw new ApiError("ROLE_NOT_ALLOWED", "다른 사람의 작업입니다.", "생산관리 담당자는 자기 작업만 볼 수 있습니다.");
        return t;
    }

    /**
     * GET /api/ai/tasks?mine=true — 작업 목록, 최근 것부터 50개 (contracts.md 3.1).
     * 생산관리는 항상 자기 것만, 구매·팀장은 mine을 빼면 전체 (기획안 2.3).
     */
    @GetMapping("/api/ai/tasks")
    List<Map<String, Object>> list(@RequestParam(defaultValue = "false") boolean mine, @AuthenticationPrincipal Jwt user) {
        boolean onlyMine = mine || !Auth.allows(user.getClaimAsString("role"), "BUYER");
        return db.queryForList("select task_id from ai_task where (? = false or user_id = ?) order by created_at desc limit 50",
                String.class, onlyMine, user.getSubject()).stream().map(this::row).toList();
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
        if (!task.userId().equals(user.getSubject()))
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
     * <p>결정을 저장한 뒤 agent에 재개를 요청하고 202를 바로 돌려준다. 결정 저장과 agent 호출을 묶지 않는다 —
     * agent가 죽어 있어도 팀장의 결정은 남아야 한다.
     */
    @PostMapping("/api/ai/tasks/{id}/approval")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, Object> decide(@PathVariable String id, @RequestBody Map<String, String> body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "APPROVER");
        find(id);
        Map<String, Object> card = approvals.decide(id, body.get("approvalId"), body.get("decision"), body.get("reason"), user.getSubject());
        db.update("update ai_task set status = 'RUNNING' where task_id = ? and status = 'WAITING_APPROVAL'", id);
        CompletableFuture.runAsync(() -> {
            try {
                Map<String, Object> req = new HashMap<>();
                req.put("threadId", id);
                req.put("approvalId", card.get("approval_id"));
                req.put("decision", card.get("status"));
                req.put("reason", card.get("reason"));
                agent.post().uri("/agent/tasks/{id}/resume", id).body(req).retrieve().toBodilessEntity();
            } catch (Exception e) {
                addEvent(id, Map.of("type", "FAILED", "errorCode", "AGENT_UNAVAILABLE",
                        "message", "승인은 저장됐지만 AI 작업을 이어 가지 못했습니다: " + e.getMessage()));
            }
        });
        return Map.of("approvalId", card.get("approval_id"), "status", card.get("status"));
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
