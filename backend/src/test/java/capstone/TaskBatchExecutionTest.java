package capstone;

import java.util.*;
import java.util.concurrent.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.springframework.amqp.core.AmqpTemplate;
import org.springframework.core.io.FileSystemResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.*;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.*;
import static capstone.InternalApi.m;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class TaskBatchExecutionTest {
    static PostgreSQLContainer<?> pg;
    JdbcTemplate db;
    Approvals approvals;
    InternalApi internal;
    TaskBatchExecution batch;
    TaskEventLog events;
    String first, second;
    static Jwt jwt(String id, String role) { return Jwt.withTokenValue("test").header("alg", "none").subject(id).claim("role", role).build(); }
    final Jwt manager = jwt("approver", "APPROVER");

    @BeforeAll static void start() {
        pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
            .withDatabaseName("bom").withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d");
        pg.start();
    }
    @AfterAll static void stop() { if (pg != null) pg.stop(); }
    @BeforeEach void setup() {
        var ds = new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app");
        db = new JdbcTemplate(ds);
        db.execute("truncate app_user,supplier,item,bom,inventory,production_plan,purchase_order,supplier_metric,ai_task,task_event,approval,change_history cascade");
        new ResourceDatabasePopulator(new FileSystemResource("../db/seed.sql")).execute(ds);
        var json = new ObjectMapper(); events = new TaskEventLog(db, json);
        approvals = new Approvals(db, json);
        var impact = new ImpactAnalysis(db);
        var tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
        internal = spy(new InternalApi(db, impact, new OptionAnalysis(db, impact), approvals, tx));
        var tasks = new Tasks(db, json, approvals, "http://localhost:1", mock(AmqpTemplate.class));
        batch = new TaskBatchExecution(db, json, tasks, approvals, internal, impact);
        db.update("insert into ai_task(task_id,thread_id,user_id,role,question) values('T-BATCH','T-BATCH','approver','APPROVER','test')");
        events.append("T-BATCH", m("type", "TASK_BATCH_CONTEXT", "workflowVersion", 3,
            "fingerprint", batch.snapshot("T-BATCH").get("fingerprint"), "scope", List.of("INV-001|MCU"),
            "whatif", List.of(m("op", "reschedule_plan", "plan_id", "PP-05", "new_date", "2026-11-25"))));
        first = (String) approvals.create("T-BATCH", "approver", "APPROVER", m("tool", "split_po",
            "args", m("po_id", "PO-2609-001", "split_qty", 100, "new_due_date", "2026-11-25"), "summary", "MCU 100개" )).get("approval_id");
        second = (String) approvals.create("T-BATCH", "approver", "APPROVER", m("tool", "request_due_date_change",
            "args", m("po_id", "PO-2609-004", "new_due_date", "2026-11-15"), "summary", "두 번째 변경" )).get("approval_id");
    }
    List<Map<String, Object>> entries(String firstStatus) {
        return List.of(m("approvalId", first, "reviewStatus", firstStatus), m("approvalId", second, "reviewStatus", "CONFIRMED"));
    }
    Map<String, Object> prepare(String action, List<Map<String, Object>> entries) {
        events.append("T-BATCH", m("type", "TASK_BATCH_STATE", "mode", "BATCH", "status", "REVIEW", "revision", 1, "entries", entries));
        var cmd = m("requestId", "request-1", "expectedRevision", 1, "action", action);
        batch.command("T-BATCH", cmd, manager);
        events.append("T-BATCH", m("type", "TASK_BATCH_STATE", "status", "EXECUTING", "revision", 2, "lastRequestId", "request-1", "entries", entries));
        return cmd;
    }
    Map<String, Object> execute() { return batch.complete("T-BATCH", Map.of("requestId", "request-1")); }
    int qty() { return db.queryForObject("select qty from purchase_order where po_id='PO-2609-001'", Integer.class); }
    int history() { return db.queryForObject("select count(*) from change_history", Integer.class); }

    @Test void reviewDoesNotExecuteAndOldApprovalIsBlocked() {
        events.append("T-BATCH", m("type", "TASK_BATCH_STATE", "mode", "BATCH", "status", "REVIEW", "revision", 0));
        batch.command("T-BATCH", m("requestId", "review", "expectedRevision", 0, "action", "CONFIRM", "approvalId", first), manager);
        assertThat(qty()).isEqualTo(500);
        assertThat(approvals.card(first).get("status")).isEqualTo("PENDING");
        assertThatThrownBy(() -> approvals.decide("T-BATCH", first, "APPROVED", null, "approver")).isInstanceOf(ApiError.class);
        assertThatThrownBy(() -> batch.command("T-BATCH", m("action", "CONFIRM"), jwt("buyer", "BUYER"))).isInstanceOf(ApiError.class);
    }
    @Test void delayedOutboxCannotOverwriteNewerCheckpointState() {
        events.append("T-BATCH", m("type", "TASK_BATCH_STATE", "stateVersion", 30, "revision", 2, "status", "EXECUTING"));
        events.append("T-BATCH", m("type", "TASK_BATCH_STATE", "stateVersion", 29, "revision", 1, "status", "REVIEW"));
        assertThat(batch.state("T-BATCH", manager)).containsEntry("status", "EXECUTING");
        events.append("T-BATCH", m("type", "TASK_BATCH_STATE", "stateVersion", 35, "revision", 2, "status", "COMPLETED"));
        events.append("T-BATCH", m("type", "TASK_BATCH_STATE", "stateVersion", 30, "revision", 2, "status", "EXECUTING"));
        assertThat(batch.state("T-BATCH", manager)).containsEntry("status", "COMPLETED");
    }
    @Test void executesAllAndDuplicateRequestsReturnSameResult() throws Exception {
        prepare("EXECUTE", entries("CONFIRMED"));
        try (var pool = Executors.newFixedThreadPool(2)) {
            var a = pool.submit(this::execute); var b = pool.submit(this::execute);
            assertThat(a.get(15, TimeUnit.SECONDS)).containsEntry("status", "COMPLETED");
            assertThat(b.get(15, TimeUnit.SECONDS)).containsEntry("status", "COMPLETED");
        }
        assertThat(qty()).isEqualTo(400);
        assertThat(history()).isEqualTo(2);
        assertThat(approvals.card(first).get("status")).isEqualTo("EXECUTED");
        assertThat(approvals.card(second).get("status")).isEqualTo("EXECUTED");
    }
    @Test void laterFailureRollsBackEarlierMutationAndHistory() {
        prepare("EXECUTE", entries("CONFIRMED"));
        doThrow(ApiError.invalid("두 번째 변경 실패", "test")).when(internal).changeDueDate(eq("PO-2609-004"), anyMap());
        assertThatThrownBy(this::execute).isInstanceOf(ApiError.class).hasMessageContaining("두 번째");
        verify(internal).splitPo(eq("PO-2609-001"), anyMap());
        assertThat(qty()).isEqualTo(500);
        assertThat(history()).isZero();
        assertThat(approvals.card(first).get("status")).isEqualTo("PENDING");
        assertThat(events.latest("T-BATCH", "TASK_BATCH_RESULT")).isEmpty();
    }
    @Test void excludedUnresolvedPartBlocksEveryMutation() {
        prepare("EXECUTE", entries("EXCLUDED"));
        assertThatThrownBy(this::execute).isInstanceOf(ApiError.class).hasMessageContaining("문제가 남아");
        assertThat(qty()).isEqualTo(500); assertThat(history()).isZero();
    }
    @Test void changesDuringReviewInvalidateExecution() {
        prepare("EXECUTE", entries("CONFIRMED"));
        db.update("update production_plan set qty=qty+1 where plan_id='PP-01'");
        assertThatThrownBy(this::execute).isInstanceOf(ApiError.class).hasMessageContaining("업무 데이터가 변경");
        assertThat(qty()).isEqualTo(500); assertThat(history()).isZero();
    }
    @Test void cancelDoesNotWriteBusinessDataAndRemainsVisibleToManager() {
        prepare("CANCEL", entries("CONFIRMED"));
        assertThat(execute()).containsEntry("status", "CANCELLED");
        assertThat(qty()).isEqualTo(500); assertThat(history()).isZero();
        assertThat(approvals.list(null, manager)).hasSize(2);
        assertThat(approvals.card(first).get("status")).isEqualTo("WITHDRAWN");
    }
    @Test void manualRevisionIsStagedUntilAtomicCommit() {
        var old = approvals.create("T-BATCH", "approver", "APPROVER", m("tool", "request_due_date_change",
            "args", m("po_id", "PO-2610-114", "new_due_date", "2026-11-23"))).get("approval_id");
        approvals.decide("T-BATCH", (String) old, "REJECTED", "직접 수정", "approver");
        var card = approvals.create("T-BATCH", "approver", "APPROVER", m("tool", "update_purchase",
            "args", m("po_id", "PO-2610-114", "new_qty", 1000, "new_due_date", "2026-11-23"),
            "previousApprovalId", old, "proposalId", "manual-1", "revision", 2));
        assertThat(card).containsEntry("status", "PENDING").containsEntry("amount", 200000L);
        assertThat(db.queryForObject("select qty from purchase_order where po_id='PO-2610-114'", Integer.class)).isEqualTo(800);
    }
}
