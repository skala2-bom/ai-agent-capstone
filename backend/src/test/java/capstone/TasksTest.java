package capstone;

import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.amqp.core.AmqpTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.security.oauth2.jwt.Jwt;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;

import static capstone.InternalApi.m;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

/**
 * 작업·이벤트가 DB에 남는가 (8단계, decisions.md 6.20).
 *
 * <p>agent 주소를 일부러 닿지 않는 곳으로 둔다 — agent 없이 이벤트를 직접 넣어 보고,
 * agent에 못 닿았을 때 재시도 대기열을 거쳐 FAILED가 남는지도 같이 본다. RabbitMQ는 가짜(mock)로 두고
 * 무엇을 어느 대기열에 넣는지만 본다 — 대기열 사이를 옮기는 건 RabbitMQ의 일이다.
 */
class TasksTest {

    static Tasks tasks;
    static JdbcTemplate db;
    static AmqpTemplate queue = mock(AmqpTemplate.class);

    @BeforeAll
    static void startDb() {
        PostgreSQLContainer<?> pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
                .withDatabaseName("bom")
                .withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d");
        pg.start();
        db = new JdbcTemplate(new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app"));
        tasks = newTasks();
    }

    /** backend 재시작 = Tasks를 새로 만든다 (메모리의 연결·자물쇠가 다 사라진다) */
    static Tasks newTasks() {
        ObjectMapper json = new ObjectMapper();
        return new Tasks(db, json, new Approvals(db, json), "http://localhost:1", queue);
    }

    static final Jwt BUYER = jwt("buyer", "BUYER"), PLANNER = jwt("planner", "PLANNER"), APPROVER = jwt("approver", "APPROVER");

    @Test
    void 이벤트에_순번이_붙어_저장되고_상태가_따라_바뀐다_재시작해도_남는다() {
        String id = insertTask("buyer", "BUYER");
        tasks.addEvent(id, m("type", "TOOL_START", "tool", "analyze_impact"));
        assertThat(tasks.find(id).status()).isEqualTo("RUNNING");
        tasks.receiveEvent(id, m("type", "APPROVAL_REQUIRED", "tool", "split_po",
                "args", m("po_id", "PO-2609-001", "split_qty", 100, "new_due_date", "2026-11-25"), "amount", 390_000));
        assertThat(tasks.find(id).status()).isEqualTo("WAITING_APPROVAL");
        tasks.addEvent(id, m("type", "DONE", "status", "DONE", "answer", "끝", "result", m("intent", "ADJUST"), "sources", List.of()));

        // 재시작한 것처럼 새 인스턴스로 읽어도 그대로다
        Tasks restarted = newTasks();
        Map<String, Object> t = restarted.get(id, BUYER);
        assertThat(t).containsEntry("status", "DONE").containsEntry("answer", "끝").containsEntry("question", "q");
        assertThat(t.get("result")).isEqualTo(Map.of("intent", "ADJUST"));
        assertThat(db.queryForList("select seq, type from task_event where task_id = ? order by seq", id))
                .extracting(r -> r.get("seq") + " " + r.get("type"))
                .containsExactly("1 TOOL_START", "2 APPROVAL_REQUIRED", "3 DONE");
        // 끝난 작업에 늦게 온 이벤트는 쌓이지만 상태는 바뀌지 않는다
        restarted.addEvent(id, m("type", "TOOL_START"));
        assertThat(restarted.find(id).status()).isEqualTo("DONE");
    }

    @Test
    @SuppressWarnings("unchecked")
    void agent에_닿지_못하면_30초_2분_뒤_다시_보내고_세_번째에_DLQ와_FAILED() {
        String id = tasks.start("planner", "PLANNER", "허브를 11/25로 당기면?", "USER", null);
        Map<String, Object> msg = sent(TaskQueue.TASKS);
        assertThat(msg).containsEntry("kind", "start").containsEntry("taskId", id).containsEntry("attempt", 1);
        assertThat((Map<String, Object>) msg.get("body")).containsEntry("question", "허브를 11/25로 당기면?");

        tasks.deliver(msg);   // 1번째 실패
        msg = sent("ai.tasks.retry.30s");
        tasks.deliver(msg);   // 2번째 실패
        msg = sent("ai.tasks.retry.2m");
        assertThat(tasks.find(id).status()).isEqualTo("QUEUED");   // 재시도 동안은 기다리는 중
        tasks.deliver(msg);   // 3번째 실패 → 끝
        assertThat(sent(TaskQueue.DLQ)).containsEntry("attempt", 3).containsKey("error");
        assertThat(tasks.find(id).status()).isEqualTo("FAILED");
        assertThat(db.queryForObject("select payload->>'errorCode' from task_event where task_id = ? and seq = 1", String.class, id))
                .isEqualTo("AGENT_UNAVAILABLE");
    }

    @Test
    void 이미_시작한_작업은_다시_보내지_않는다() {
        String id = insertTask("buyer", "BUYER");
        tasks.addEvent(id, m("type", "TOOL_START", "tool", "analyze_impact"));   // agent가 이미 받아서 돌고 있다
        reset(queue);
        tasks.deliver(m("kind", "start", "taskId", id, "attempt", 1, "body", m()));
        verifyNoInteractions(queue);   // agent에 안 보냈으니 실패도 재시도도 없다
    }

    /** 이 대기열에 마지막으로 넣은 메시지 */
    @SuppressWarnings("unchecked")
    private static Map<String, Object> sent(String queueName) {
        ArgumentCaptor<Object> c = ArgumentCaptor.forClass(Object.class);
        verify(queue, atLeastOnce()).convertAndSend(eq(queueName), c.capture());
        return (Map<String, Object>) c.getValue();
    }

    @Test
    void 생산관리는_자기_작업만_구매와_팀장은_전체를_본다() {
        String buyers = insertTask("buyer", "BUYER");
        String planners = insertTask("planner", "PLANNER");

        assertThat(tasks.list(false, PLANNER)).extracting(t -> t.get("userId")).containsOnly("planner");
        assertThat(tasks.list(false, APPROVER)).extracting(t -> t.get("taskId")).contains(buyers, planners);
        assertThat(tasks.list(true, BUYER)).extracting(t -> t.get("userId")).containsOnly("buyer");

        assertThat(tasks.get(planners, BUYER)).containsEntry("taskId", planners);
        assertThatThrownBy(() -> tasks.get(buyers, PLANNER))
                .isInstanceOfSatisfying(ApiError.class, e -> assertThat(e.code).isEqualTo("ROLE_NOT_ALLOWED"));
    }

    // ------------------------------------------------------------------

    private static String insertTask(String user, String role) {
        String id = java.util.UUID.randomUUID().toString();
        db.update("insert into ai_task (task_id, thread_id, user_id, role, question) values (?, ?, ?, ?, 'q')", id, id, user, role);
        return id;
    }

    private static Jwt jwt(String sub, String role) {
        return Jwt.withTokenValue("t").header("alg", "none").subject(sub).claim("role", role).build();
    }
}
