package capstone;

import java.time.Instant;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestClient;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

// ponytail: 작업·이벤트를 메모리에만 둔다. 재시작하면 사라짐 → task 모듈 담당이 ai_task/task_event 테이블로 교체
@SpringBootApplication
@RestController
public class BackendApplication {

    public static void main(String[] args) {
        SpringApplication.run(BackendApplication.class, args);
    }

    record Task(String id, List<Map<String, Object>> events, List<SseEmitter> emitters) {}

    private final Map<String, Task> tasks = new ConcurrentHashMap<>();
    private final RestClient agent;

    BackendApplication(@Value("${AGENT_URL:http://agent:8000}") String agentUrl) {
        this.agent = RestClient.create(agentUrl);
    }

    // ① 화면 → 백엔드: 작업 접수 후 202, ② 백엔드 → agent는 비동기로
    @PostMapping("/api/ai/tasks")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, String> createTask(@RequestBody Map<String, String> body) {
        String id = UUID.randomUUID().toString();
        tasks.put(id, new Task(id, new ArrayList<>(), new ArrayList<>()));
        CompletableFuture.runAsync(() -> {
            try {
                agent.post().uri("/agent/tasks")
                        .body(Map.of("taskId", id, "threadId", id, "question", body.getOrDefault("question", "")))
                        .retrieve().toBodilessEntity();
            } catch (Exception e) {
                addEvent(id, Map.of("type", "FAILED", "errorCode", "AGENT_UNAVAILABLE", "message", e.getMessage()));
            }
        });
        return Map.of("taskId", id);
    }

    // ⑦ 화면이 구독. 구독 전에 쌓인 이벤트는 다시 보낸다
    @GetMapping(path = "/api/ai/tasks/{id}/stream", produces = "text/event-stream")
    SseEmitter stream(@PathVariable String id) {
        Task task = find(id);
        SseEmitter emitter = new SseEmitter(120_000L);
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

    // ⑦ agent → 백엔드: 진행 이벤트 콜백
    @PostMapping("/internal/tasks/{id}/events")
    void receiveEvent(@PathVariable String id, @RequestBody Map<String, Object> event) {
        find(id);
        addEvent(id, event);
    }

    // ⑤ business-mcp → 백엔드: 영향 분석. 지금은 고정 JSON → analysis 모듈 담당이 실제 계산으로 교체
    @PostMapping("/internal/analysis/impact")
    Map<String, Object> analyzeImpact(@RequestBody(required = false) Map<String, Object> whatif) {
        return Map.of(
                "shortages", List.of(
                        Map.of("item_id", "MCU", "plan_id", "PP-05", "qty", 100, "date", "2026-11-25"),
                        Map.of("item_id", "MCU", "plan_id", "PP-04", "qty", 100, "date", "2026-11-26")),
                "excesses", List.of(),
                "evidence_uri", "doc://ECN-2026-014/3.2");
    }

    private void addEvent(String id, Map<String, Object> event) {
        Task task = tasks.get(id);
        synchronized (task) {
            Map<String, Object> e = new LinkedHashMap<>(event);
            e.put("taskId", id);
            e.put("seq", task.events().size() + 1);
            e.put("ts", Instant.now().toString());
            task.events().add(e);
            for (SseEmitter emitter : List.copyOf(task.emitters())) {
                send(emitter, e);
                if (isFinished(task)) emitter.complete();
            }
        }
    }

    private static boolean isFinished(Task task) {
        if (task.events().isEmpty()) return false;
        Object type = task.events().getLast().get("type");
        return "DONE".equals(type) || "FAILED".equals(type);
    }

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
