package capstone;

import java.time.Instant;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * backend — Application 계층 (아키텍처 3장).
 *
 * <p>이 서비스가 맡는 것
 * <ul>
 *   <li>화면이 부르는 작업 API: 질문을 작업으로 접수하고 202를 바로 돌려준다</li>
 *   <li>SSE 중계: agent가 보내온 진행 이벤트를 화면으로 흘려보낸다</li>
 *   <li>내부 API {@code /internal/**}: business-mcp가 업무 데이터를 받아 가는 창구 ({@link InternalApi})</li>
 * </ul>
 *
 * <p><b>금지</b>: LLM을 직접 부르지 않는다 (교재 5장 금지 사항). AI 호출은 전부 agent가 한다.
 * 그래서 이 프로젝트의 backend 의존성에는 LLM 라이브러리가 아예 없다.
 *
 * <p><b>지금은 뼈대</b>라서 한 파일에 다 있다. 실제로는 아키텍처 3.1의 모듈
 * (auth / master / plan / purchase / inventory / analysis / task / approval / masking)로 나뉜다.
 * 나눌 때 이 파일의 메서드가 어느 모듈로 가는지는 각 메서드 주석에 적어 두었다.
 *
 * <p>설계 배경: docs/walking-skeleton.md · 결정 근거: docs/decisions.md
 */
// ponytail: 작업·이벤트를 메모리에만 둔다. 재시작하면 사라짐 → task 모듈 담당이 ai_task/task_event 테이블로 교체
@SpringBootApplication
@RestController
public class BackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(BackendApplication.class, args);
    }

    /**
     * 작업 하나. (→ task 모듈, 나중에 ai_task / task_event 테이블로)
     *
     * @param events   이 작업에서 지금까지 나온 모든 이벤트. 화면이 늦게 구독해도
     *                 처음부터 다시 보내 주려고 통째로 들고 있는다.
     * @param emitters 이 작업을 지금 보고 있는 화면들. 보통 1개지만, 같은 작업을
     *                 두 탭에서 열 수도 있어서 목록이다.
     */
    record Task(String id, String userId, String role, List<Map<String, Object>> events, List<SseEmitter> emitters) {}

    private final Map<String, Task> tasks = new ConcurrentHashMap<>();

    /** agent 호출용. compose 안에서는 서비스 이름이 그대로 주소가 된다 (http://agent:8000). */
    private final RestClient agent;

    BackendApplication(@Value("${AGENT_URL:http://agent:8000}") String agentUrl) {
        this.agent = RestClient.create(agentUrl);
    }

    // ------------------------------------------------------------------
    // 화면이 부르는 API  (→ task 모듈)
    // ------------------------------------------------------------------

    /**
     * ① 질문을 작업으로 접수한다 (아키텍처 9.1).
     *
     * <p>핵심은 <b>agent를 기다리지 않는 것</b>이다. AI 응답은 수 초~수십 초 걸리므로
     * 작업만 만들어 두고 202를 먼저 돌려준 뒤, agent 호출은 다른 스레드로 넘긴다.
     * 화면은 이 202로 받은 taskId로 곧바로 SSE를 구독한다.
     *
     * <p>agent 호출이 실패해도 화면이 영원히 기다리지 않도록 FAILED 이벤트를 남긴다.
     *
     * <p>요청자(JWT의 sub·role)를 작업에 저장하고 agent에 넘긴다. agent는 이 값을 {@code X-User-Id}·{@code X-Role}
     * 헤더로 도구 호출에 실어 보낸다 (contracts.md 1.1). 역할을 질문 내용이나 도구 인자에서 받지 않는 이유:
     * 그건 사용자나 모델이 바꿀 수 있는 값이다.
     *
     * <p>아직 없는 것: 작업 DB 저장, 대기열(P2).
     */
    @PostMapping("/api/ai/tasks")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, String> createTask(@RequestBody Map<String, String> body, @AuthenticationPrincipal Jwt user) {
        String id = UUID.randomUUID().toString();
        String userId = user.getSubject(), role = user.getClaimAsString("role");
        // threadId는 지금 taskId와 같게 쓴다. 승인 후 재개(interrupt/resume)를 붙일 때
        // 같은 대화를 이어가는 열쇠가 되는 값이라, 그때 별도로 관리해야 할 수도 있다.
        tasks.put(id, new Task(id, userId, role, new ArrayList<>(), new ArrayList<>()));
        CompletableFuture.runAsync(() -> {
            try {
                // whatif는 사람이 물은 작업이라 null이다. 질문에서 뽑는 건 agent의 classify_intent가 한다 (contracts.md 2.6)
                Map<String, Object> req = new HashMap<>();
                req.put("taskId", id);
                req.put("threadId", id);
                req.put("userId", userId);
                req.put("role", role);
                req.put("question", body.getOrDefault("question", ""));
                req.put("whatif", null);
                agent.post().uri("/agent/tasks").body(req).retrieve().toBodilessEntity();
            } catch (Exception e) {
                addEvent(id, Map.of("type", "FAILED", "errorCode", "AGENT_UNAVAILABLE", "message", e.getMessage()));
            }
        });
        return Map.of("taskId", id);
    }

    /**
     * ⑦ 화면이 진행 이벤트를 구독한다 (아키텍처 9.1, 9.3).
     *
     * <p><b>왜 지난 이벤트를 다시 보내나</b>: 화면이 202를 받고 구독을 시작하기까지
     * 몇 밀리초가 걸리는데, 그 사이에 agent가 이미 TOOL_START를 보냈을 수 있다.
     * 쌓인 것부터 전부 보내 주면 화면은 순서대로 다 받는다.
     * (아키텍처 9.3의 "마지막 seq 이후 이벤트 재전송"을 단순하게 구현한 것)
     *
     * <p>이미 끝난 작업을 구독하면 쌓인 이벤트를 다 보낸 뒤 바로 스트림을 닫는다.
     *
     * <p>질문한 사람만 구독할 수 있다 (contracts.md 3.1 "요청자"). 팀장은 승인 대기함으로 카드를 받는다.
     * 토큰은 {@code ?access_token=}으로 받는다 — 브라우저 EventSource는 헤더를 못 붙인다 ({@link Auth}).
     *
     * <p>아직 없는 것: 15초 heartbeat.
     */
    @GetMapping(path = "/api/ai/tasks/{id}/stream", produces = "text/event-stream")
    SseEmitter stream(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        Task task = find(id);
        if (!task.userId().equals(user.getSubject()))
            throw new org.springframework.web.server.ResponseStatusException(HttpStatus.FORBIDDEN, "질문한 사람만 볼 수 있습니다");
        SseEmitter emitter = new SseEmitter(120_000L);  // 작업 전체 제한 120초 (아키텍처 4.2)
        // addEvent와 같은 자물쇠로 묶는다. 안 묶으면 "쌓인 것 전송"과 "새 이벤트 도착"이
        // 겹쳐서 이벤트가 중복되거나 빠질 수 있다.
        synchronized (task) {
            for (Map<String, Object> e : task.events()) send(emitter, e);
            if (isFinished(task)) emitter.complete();
            else {
                task.emitters().add(emitter);
                emitter.onCompletion(() -> { synchronized (task) { task.emitters().remove(emitter); } });
            }
        }
        return emitter;
    }

    // ------------------------------------------------------------------
    // 내부 API  — compose 내부망에서만 불린다
    // ------------------------------------------------------------------

    /**
     * ⑦ agent → backend 진행 이벤트 콜백 (아키텍처 9.2).
     *
     * <p>계층상 아래(AI Serving)에서 위(Application)로 가는 호출이라 예외적이지만,
     * 이벤트를 화면에 중계할 수 있는 건 backend뿐이라 아키텍처 3장이 허용한 2개 예외 중 하나다.
     *
     * <p>아직 없는 것: 서비스 토큰 확인 (아키텍처 10장, 9단계). 지금은 8080이 공개 포트라
     * 밖에서도 부를 수 있다.
     */
    @PostMapping("/internal/tasks/{id}/events")
    void receiveEvent(@PathVariable String id, @RequestBody Map<String, Object> event) {
        find(id);  // 없는 작업이면 404
        addEvent(id, event);
    }

    // 업무 데이터용 /internal/** (business-mcp 도구 8개)는 InternalApi.java에 있다.

    // ------------------------------------------------------------------
    // 이벤트 저장·전송  (→ task 모듈)
    // ------------------------------------------------------------------

    /**
     * 이벤트를 작업에 기록하고, 보고 있는 화면 모두에게 보낸다.
     *
     * <p>agent가 보낸 payload에 taskId·seq·ts를 여기서 붙인다 (아키텍처 9.3
     * "모든 이벤트에 taskId, seq, ts를 붙인다"). seq를 backend가 매기는 이유는
     * 순서를 아는 쪽이 이벤트를 모아 두는 쪽이기 때문이다.
     */
    private void addEvent(String id, Map<String, Object> event) {
        Task task = tasks.get(id);
        synchronized (task) {
            Map<String, Object> e = new LinkedHashMap<>(event);
            e.put("taskId", id);
            e.put("seq", task.events().size() + 1);
            e.put("ts", Instant.now().toString());
            task.events().add(e);
            // 전송 중에 onCompletion이 emitters를 건드릴 수 있어 복사본을 돈다.
            for (SseEmitter emitter : List.copyOf(task.emitters())) {
                send(emitter, e);
                if (isFinished(task)) emitter.complete();
            }
        }
    }

    /** DONE·FAILED가 나오면 그 작업은 끝이다. 스트림을 닫아도 되는지 판단할 때 쓴다. */
    private static boolean isFinished(Task task) {
        if (task.events().isEmpty()) return false;
        Object type = task.events().getLast().get("type");
        return "DONE".equals(type) || "FAILED".equals(type);
    }

    /**
     * SSE 한 건 전송. 실패는 삼킨다.
     *
     * <p>화면이 탭을 닫거나 새로고침하면 전송이 터지는데, 그건 정상 상황이다.
     * 여기서 예외를 올리면 다른 화면까지 못 받게 되고, 작업 자체도 끊긴다.
     * 끊긴 화면은 다시 구독하면 {@link #stream} 이 처음부터 다시 보내 준다.
     */
    private static void send(SseEmitter emitter, Map<String, Object> event) {
        try {
            emitter.send(SseEmitter.event().id(String.valueOf(event.get("seq"))).data(event));
        } catch (Exception ignored) {
            // 화면이 끊긴 경우. 다시 연결하면 처음부터 재전송된다
        }
    }

    private Task find(String id) {
        Task task = tasks.get(id);
        if (task == null) throw new org.springframework.web.server.ResponseStatusException(HttpStatus.NOT_FOUND);
        return task;
    }
}
