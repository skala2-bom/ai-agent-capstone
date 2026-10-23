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
import static capstone.TaskBatchExecution.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class CoordinationRequestsTest {
    static PostgreSQLContainer<?> pg;
    JdbcTemplate db; TaskEventLog events; Tasks tasks; Approvals approvals; ImpactAnalysis impact;
    CoordinationRequests requests; CoordinationExecution execution; TaskBatchExecution batch; ScreenApi screen;
    final Jwt planner = jwt("planner", "PLANNER"), buyer = jwt("buyer", "BUYER"), manager = jwt("approver", "APPROVER");
    static Jwt jwt(String id, String role) { return Jwt.withTokenValue("test").header("alg", "none").subject(id).claim("role", role).build(); }
    static List<Map<String, Object>> hubOps() { return List.of(m("op", "reschedule_plan", "plan_id", "PP-05", "new_date", "2026-11-25")); }
    @BeforeAll static void start() {
        pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
            .withDatabaseName("bom").withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d"); pg.start();
    }
    @AfterAll static void stop() { if (pg != null) pg.stop(); }
    @BeforeEach void setup() {
        var ds = new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app"); db = new JdbcTemplate(ds);
        db.execute("drop function if exists reject_test_plan() cascade");
        db.execute("drop function if exists reject_test_request() cascade");
        db.execute("truncate app_user,supplier,item,bom,inventory,production_plan,purchase_order,supplier_metric,ai_task,task_event,approval,change_history cascade");
        new ResourceDatabasePopulator(new FileSystemResource("../db/seed.sql")).execute(ds);
        var json = new ObjectMapper(); events = new TaskEventLog(db, json); impact = new ImpactAnalysis(db);
        approvals = new Approvals(db, json); tasks = new Tasks(db, json, approvals, "http://localhost:1", mock(AmqpTemplate.class));
        var tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
        var internal = new InternalApi(db, impact, new OptionAnalysis(db, impact), approvals, tx);
        screen = new ScreenApi(internal, db, approvals, tx, tasks);
        requests = new CoordinationRequests(db, json, approvals, impact);
        execution = new CoordinationExecution(db, json, approvals, impact);
        batch = new TaskBatchExecution(db, json, tasks, approvals, internal, impact);
    }
    Map<String, Object> forward() {
        String task = tasks.start("planner", "PLANNER", "private original question", "USER", null);
        tasks.addEvent(task, m("type", "DONE", "answer", "private answer", "result", m("intent", "IMPACT", "whatif", hubOps(), "impact", impact.analyze(hubOps()))));
        return send(task, planner);
    }
    Map<String, Object> reverse() {
        var delayed = screen.supplierDelay("PO-2610-106", m("due_date", "2026-11-27", "reason", "협력사 확정 지연"), buyer);
        String task = (String) delayed.get("analysis_task_id");
        tasks.addEvent(task, m("type", "DONE", "result", m("intent", "IMPACT", "whatif", List.of(), "impact", impact.analyze(List.of()))));
        return send(task, buyer);
    }
    Map<String, Object> send(String task, Jwt user) {
        var preview = requests.preview(task, user);
        return requests.create(m("sourceTaskId", task, "expectedFingerprint", preview.get("fingerprint"), "commandId", UUID.randomUUID().toString(), "message", "공유 요청 내용"), user);
    }
    Map<String, Object> claim(Map<String, Object> r, Jwt user) { return requests.claim((String) r.get("id"), m("expectedRevision", r.get("revision")), user); }
    String solve(Map<String, Object> r) { return tasks.createTask(Map.of("coordinationId", (String) r.get("id"), "question", "이 요청 해결해줘"), buyer).get("taskId"); }
    String prepared(Map<String, Object> r) {
        r = claim(r, buyer); String task = solve(r);
        events.append(task, m("type", "TASK_BATCH_CONTEXT", "workflowVersion", 3, "fingerprint", batch.snapshot(task).get("fingerprint"), "scope", List.of("INV-001|MCU"), "whatif", hubOps()));
        List<String> cards = new ArrayList<>();
        var options = maps(new OptionAnalysis(db, impact).compare(hubOps(), Set.of()).get("options"));
        for (var option : options) if (Boolean.TRUE.equals(option.get("recommended"))) {
            cards.add((String) tasks.receiveEvent(task, m("type", "APPROVAL_REQUIRED", "tool", option.get("tool"),
                    "args", option.get("args"), "summary", option.get("summary"))).get("approvalId"));
        }
        events.append(task, m("type", "TASK_REVIEW_READY", "reviewId", "review", "approvalIds", cards));
        Map<String, String> memos = new LinkedHashMap<>(); cards.forEach(c -> memos.put(c, "확인"));
        tasks.submit(task, new Approvals.TaskReview("submit", "review", "생산관리 요청", memos), buyer);
        var entries = cards.stream().map(c -> m("approvalId", c, "reviewStatus", "CONFIRMED")).toList();
        events.append(task, m("type", "TASK_BATCH_STATE", "mode", "BATCH", "status", "REVIEW", "revision", 1, "entries", entries));
        batch.command(task, m("requestId", "exec", "expectedRevision", 1, "action", "EXECUTE"), manager);
        events.append(task, m("type", "TASK_BATCH_STATE", "mode", "BATCH", "status", "EXECUTING", "revision", 2, "lastRequestId", "exec", "entries", entries));
        return task;
    }
    int poQty() { return db.queryForObject("select qty from purchase_order where po_id='PO-2609-001'", Integer.class); }
    String chatDelay(boolean reported, String... ids) {
        List<Map<String, Object>> ops = new ArrayList<>(), changes = new ArrayList<>();
        for (String id : ids) {
            var before = requests.delays.current(id);
            var after = new LinkedHashMap<>(before);
            String due = java.time.LocalDate.parse((String) before.get("due_date")).plusDays(7).toString();
            after.put("due_date", due);
            changes.add(m("poId", id, "before", before, "after", after));
            ops.add(m("op", "request_due_date_change", "po_id", id, "new_due_date", due));
        }
        String task = tasks.start("buyer", "BUYER", "입고가 일주일 늦어졌어. 문제 있어?", "USER", null);
        tasks.addEvent(task, m("type", "DONE", "result", m("intent", "IMPACT", "whatif", ops,
                "receipt_delay", m("reported", reported, "changes", changes), "impact", impact.analyze(ops))));
        return task;
    }
    Map<String, Object> confirmChat(String task) {
        var p = requests.preview(task, buyer);
        return m("sourceTaskId", task, "expectedFingerprint", p.get("fingerprint"), "commandId", UUID.randomUUID().toString(),
                "message", "생산 일정 조정 요청", "confirmDelay", true, "delayReason", "협력사 확정 지연 통보");
    }
    String wifiDate() { return db.queryForObject("select due_date::text from purchase_order where po_id='PO-2609-002'", String.class); }

    @Test void chatDelayPreviewIsReadOnlyAndConfirmationRecordsAndSharesOnce() {
        String task = chatDelay(true, "PO-2609-002");
        var p = requests.preview(task, buyer); assertThat(p).containsEntry("requiresDelayConfirmation", true);
        assertThat(p.get("planOps")).isEqualTo(List.of()); assertThat(wifiDate()).isEqualTo("2026-11-13");
        var body = confirmChat(task); body.put("confirmDelay", false);
        assertThatThrownBy(() -> requests.create(body, buyer)).isInstanceOf(ApiError.class).hasMessageContaining("동의");
        body.put("confirmDelay", true); body.put("delayReason", " ");
        assertThatThrownBy(() -> requests.create(body, buyer)).isInstanceOf(ApiError.class);
        assertThat(wifiDate()).isEqualTo("2026-11-13"); body.put("delayReason", "확정 통보");
        var r = requests.create(body, buyer);
        assertThat(r).containsEntry("direction", "BUY_TO_PLAN").containsEntry("status", "OPEN");
        assertThat(wifiDate()).isEqualTo("2026-11-20");
        assertThat(requests.create(body, buyer).get("id")).isEqualTo(task);
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isEqualTo(1);
        assertThat(events.latest(task, "SUPPLIER_DELAY_FACT")).containsKey("facts");
        assertThat(requests.list(planner)).hasSize(1);
        assertThat(requests.preview(task, buyer)).containsEntry("requiresDelayConfirmation", false);
        assertThatThrownBy(() -> requests.create(body, planner)).isInstanceOf(ApiError.class);
    }

    @Test void chatDelayRejectsHypothesesStaleAnalysisAndStalePreview() {
        String hypothetical = chatDelay(false, "PO-2609-002");
        assertThatThrownBy(() -> requests.preview(hypothetical, buyer)).isInstanceOf(ApiError.class);
        String task = chatDelay(true, "PO-2609-002"); var body = confirmChat(task);
        db.update("update production_plan set qty=qty+1 where plan_id='PP-01'");
        assertThatThrownBy(() -> requests.create(body, buyer)).isInstanceOf(ApiError.class).hasMessageContaining("바뀌었");
        db.update("update purchase_order set qty=qty+1 where po_id='PO-2609-002'");
        assertThatThrownBy(() -> requests.preview(task, buyer)).isInstanceOf(ApiError.class).hasMessageContaining("분석 이후");
        assertThat(wifiDate()).isEqualTo("2026-11-13");
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isZero();
    }

    @Test void multiReceiptDelayAndRequestWriteAreOneTransaction() {
        String task = chatDelay(true, "PO-2609-002", "PO-2610-106"); var body = confirmChat(task);
        db.execute("create function reject_test_request() returns trigger language plpgsql as $$ begin if NEW.type='COORDINATION_REQUEST' then raise exception 'request write failure'; end if; return NEW; end $$");
        db.execute("create trigger fail_request before insert on task_event for each row execute function reject_test_request()");
        assertThatThrownBy(() -> requests.create(body, buyer)).isInstanceOf(RuntimeException.class);
        assertThat(wifiDate()).isEqualTo("2026-11-13");
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isZero();
        assertThat(events.latest(task, "SUPPLIER_DELAY_FACT")).isEmpty();
        db.execute("drop function reject_test_request() cascade");
        requests.create(body, buyer);
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isEqualTo(2);
    }

    @Test void anotherChatCannotRecordTheSameDelayAgain() {
        String a = chatDelay(true, "PO-2609-002"), b = chatDelay(true, "PO-2609-002");
        var bodyA = confirmChat(a); var bodyB = confirmChat(b);
        requests.create(bodyA, buyer);
        assertThatThrownBy(() -> requests.create(bodyB, buyer)).isInstanceOf(ApiError.class);
        assertThat(wifiDate()).isEqualTo("2026-11-20");
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isEqualTo(1);
    }
    String planDate() { return db.queryForObject("select plan_date::text from production_plan where plan_id='PP-05'", String.class); }

    @Test void explicitSharingPreservesPrivateHistoryAndConsent() {
        var r = forward(); String id = (String) r.get("id");
        assertThat(requests.list(buyer)).hasSize(1);
        assertThat(requests.get(id, buyer).toString()).doesNotContain("private original", "private answer");
        assertThatThrownBy(() -> tasks.get(id, buyer)).isInstanceOf(ApiError.class);
        assertThatThrownBy(() -> tasks.createTask(Map.of("parentTaskId", id, "question", "이어서"), buyer)).isInstanceOf(ApiError.class);
        assertThat(requests.list(manager)).isEmpty();
        assertThat(planDate()).isEqualTo("2026-12-14"); assertThat(poQty()).isEqualTo(500);
    }
    @Test void sendRejectsStalePreviewAndDuplicateSendIsIdempotent() {
        var r = forward(); var saved = requests.store.get((String) r.get("id"));
        assertThat(requests.create(map(saved.get("creationCommand")), planner).get("id")).isEqualTo(r.get("id"));
        assertThat(requests.list(buyer)).hasSize(1);
        String task = tasks.start("planner", "PLANNER", "test", "USER", null);
        tasks.addEvent(task, m("type", "DONE", "result", m("intent", "IMPACT", "whatif", hubOps())));
        var preview = requests.preview(task, planner);
        db.update("update production_plan set qty=qty+1 where plan_id='PP-01'");
        assertThatThrownBy(() -> requests.create(m("sourceTaskId", task, "commandId", "new", "message", "test", "expectedFingerprint", preview.get("fingerprint")), planner)).isInstanceOf(ApiError.class).hasMessageContaining("바뀌었");
    }
    @Test void concurrentBuyersHaveExactlyOneAssignee() throws Exception {
        var r = forward(); db.update("insert into app_user values('buyer2','김구매','BUYER','unused')");
        try (var pool = Executors.newFixedThreadPool(2)) {
            Callable<Boolean> a = () -> { try { claim(r, buyer); return true; } catch (ApiError e) { return false; } };
            Callable<Boolean> b = () -> { try { claim(r, jwt("buyer2", "BUYER")); return true; } catch (ApiError e) { return false; } };
            var results = pool.invokeAll(List.of(a, b));
            assertThat(results.stream().filter(f -> { try { return f.get(); } catch(Exception e) { throw new RuntimeException(e); } }).count()).isEqualTo(1);
        }
    }
    @Test void solverIsUniqueAndRoleCannotBeElevatedByRequestBody() {
        var r = claim(forward(), buyer);
        String task = solve(r); assertThat(solve(r)).isEqualTo(task);
        assertThatThrownBy(() -> tasks.createTask(Map.of("coordinationId", (String) r.get("id"), "role", "BUYER"), planner)).isInstanceOf(ApiError.class);
        assertThat(events.latest(task, "COORDINATION_START").toString()).contains("SOLVE", "reschedule_plan");
        assertThatThrownBy(() -> tasks.receiveEvent(task, m("type", "COORDINATION_REQUEST"))).isInstanceOf(ApiError.class);
    }
    @Test void atomicForwardExecutionAppliesPlansAndPosOnce() {
        var r = forward(); String task = prepared(r);
        assertThat(planDate()).isEqualTo("2026-12-14"); assertThat(poQty()).isEqualTo(500);
        var result = batch.complete(task, Map.of("requestId", "exec"));
        assertThat(result).containsEntry("status", "COMPLETED").containsEntry("remainingWhatif", List.of());
        // 6.47: 추천안이 안전재고까지 채우는 400개 분할이라 원 발주에 100개가 남는다 (전에는 100개 분할 → 400개)
        assertThat(planDate()).isEqualTo("2026-11-25"); assertThat(poQty()).isEqualTo(100);
        assertThat(requests.get((String) r.get("id"), planner)).containsEntry("status", "COMPLETED").containsEntry("unread", true);
        batch.complete(task, Map.of("requestId", "exec"));
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isEqualTo(2);
    }
    @Test void planWriteFailureRollsBackPurchaseChangesAndRequestResult() {
        var r = forward(); String task = prepared(r);
        db.execute("create function reject_test_plan() returns trigger language plpgsql as $$ begin raise exception 'plan write failure'; end $$");
        db.execute("create trigger fail_plan before update on production_plan for each row execute function reject_test_plan()");
        assertThatThrownBy(() -> batch.complete(task, Map.of("requestId", "exec"))).isInstanceOf(RuntimeException.class);
        assertThat(poQty()).isEqualTo(500); assertThat(planDate()).isEqualTo("2026-12-14");
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isZero();
        assertThat(requests.get((String) r.get("id"), planner).get("status")).isNotEqualTo("COMPLETED");
    }
    @Test void changedPlanAndTamperedWhatifNeverExecute() {
        var r = forward(); String task = prepared(r);
        events.append(task, m("type", "TASK_BATCH_CONTEXT", "fingerprint", batch.snapshot(task).get("fingerprint"), "scope", List.of("INV-001|MCU"),
            "whatif", List.of(m("op", "reschedule_plan", "plan_id", "PP-05", "new_date", "2026-11-26"))));
        assertThatThrownBy(() -> batch.complete(task, Map.of("requestId", "exec"))).isInstanceOf(ApiError.class).hasMessageContaining("동의한");
        db.update("update production_plan set qty=401 where plan_id='PP-05'");
        assertThatThrownBy(() -> batch.snapshot(task)).isInstanceOf(ApiError.class).hasMessageContaining("요청 당시");
        assertThat(poQty()).isEqualTo(500);
    }
    @Test void finalRejectionPreventsQueuedExecutionAndLateProposals() {
        var r = forward(); String task = prepared(r); r = requests.get((String) r.get("id"), manager);
        String id = (String) r.get("id"); var body = m("commandId", "reject", "reason", "발주 변경 불가", "expectedRevision", r.get("revision"));
        assertThatThrownBy(() -> requests.reject(id, body, buyer)).isInstanceOf(ApiError.class);
        assertThat(requests.reject(id, body, manager)).containsEntry("status", "REJECTED");
        assertThat(requests.reject(id, body, manager)).containsEntry("status", "REJECTED");
        assertThatThrownBy(() -> batch.complete(task, Map.of("requestId", "exec"))).isInstanceOf(ApiError.class);
        assertThatThrownBy(() -> tasks.receiveEvent(task, m("type", "APPROVAL_REQUIRED", "tool", "split_po"))).isInstanceOf(ApiError.class);
        assertThat(batch.state(task, manager)).containsEntry("status", "REJECTED").containsEntry("canExecute", false);
        assertThat(poQty()).isEqualTo(500); assertThat(planDate()).isEqualTo("2026-12-14");
        var received = requests.get(id, planner); assertThat(received).containsEntry("decisionReason", "발주 변경 불가").containsEntry("unread", true);
        requests.read(id, m("revision", received.get("revision")), planner);
        assertThat(new CoordinationRequests(db, new ObjectMapper(), approvals, impact).get(id, planner)).containsEntry("unread", false);
    }
    @Test void reverseRejectionKeepsRecordedSupplierDelay() {
        var r = claim(reverse(), planner); String id = (String) r.get("id");
        requests.reject(id, m("commandId", "reject", "expectedRevision", r.get("revision"), "reason", "생산 일정 조정 불가"), planner);
        assertThat(db.queryForObject("select due_date::text from purchase_order where po_id='PO-2610-106'", String.class)).isEqualTo("2026-11-27");
        assertThat(requests.get(id, buyer)).containsEntry("status", "REJECTED").containsEntry("unread", true);
    }
    @Test void reversePreviewStagesAndPlannerAloneExecutes() {
        var r = claim(reverse(), planner); String id = (String) r.get("id");
        var ops = List.of(m("op", "reschedule_plan", "plan_id", "PP-03", "new_date", "2026-11-28"));
        var preview = execution.preview(id, m("expectedRevision", r.get("revision"), "whatif", ops), planner);
        assertThat(preview).containsEntry("canExecute", true);
        assertThat(db.queryForObject("select plan_date::text from production_plan where plan_id='PP-03'", String.class)).isEqualTo("2026-11-23");
        var body = m("commandId", "execute", "expectedRevision", r.get("revision"), "previewId", preview.get("previewId"), "reason", "생산 일정 조정 확정");
        assertThatThrownBy(() -> execution.execute(id, body, buyer)).isInstanceOf(ApiError.class);
        assertThat(execution.execute(id, body, planner)).containsEntry("status", "COMPLETED");
        execution.execute(id, body, planner);
        assertThat(db.queryForObject("select count(*) from change_history where target_type='PRODUCTION_PLAN'", Integer.class)).isEqualTo(1);
    }
    @Test void reversePreviewRejectsUnsolvedChangesAndStaleExecution() {
        var r = claim(reverse(), planner); String id = (String) r.get("id");
        var bad = execution.preview(id, m("expectedRevision", r.get("revision"), "whatif", List.of(m("op", "change_plan_qty", "plan_id", "PP-03", "new_qty", 601))), planner);
        assertThat(bad).containsEntry("canExecute", false);
        assertThatThrownBy(() -> execution.preview(id, m("expectedRevision", r.get("revision"), "whatif", List.of(m("op", "cancel_po", "po_id", "PO-2610-106"))), planner)).isInstanceOf(ApiError.class);
        var good = execution.preview(id, m("expectedRevision", r.get("revision"), "whatif", List.of(m("op", "reschedule_plan", "plan_id", "PP-03", "new_date", "2026-11-28"))), planner);
        db.update("update production_plan set qty=qty+1 where plan_id='PP-01'");
        assertThatThrownBy(() -> execution.execute(id, m("commandId", "x", "reason", "확정", "previewId", good.get("previewId"), "expectedRevision", r.get("revision")), planner)).isInstanceOf(ApiError.class).hasMessageContaining("바뀌었");
    }
    Map<String, Object> childRequest(Map<String, Object> parent) {
        String task = tasks.start("planner", "PLANNER", "생산 변경과 발주 대응 필요", "USER", null);
        tasks.addEvent(task, m("type", "DONE", "result", m("intent", "IMPACT", "whatif", hubOps())));
        var preview = requests.preview(task, planner);
        return requests.create(m("sourceTaskId", task, "commandId", UUID.randomUUID().toString(), "expectedFingerprint", preview.get("fingerprint"),
                "parentRequestId", parent.get("id"), "message", "연결된 구매 해결 요청"), planner);
    }
    @Test void reverseRequestCanDelegatePurchaseChangesAndBothFinishTogether() {
        var parent = claim(reverse(), planner); var child = childRequest(parent);
        assertThat(requests.get((String) parent.get("id"), planner)).containsEntry("status", "WAITING_LINKED");
        assertThatThrownBy(() -> execution.preview((String) parent.get("id"), m("expectedRevision", number(parent.get("revision"))+1, "whatif", hubOps()), planner)).isInstanceOf(ApiError.class).hasMessageContaining("연결된 구매");
        String task = prepared(child);
        assertThat(batch.complete(task, Map.of("requestId", "exec"))).containsEntry("status", "COMPLETED");
        assertThat(requests.get((String) parent.get("id"), buyer)).containsEntry("status", "COMPLETED").containsEntry("unread", true);
        assertThat(requests.get((String) child.get("id"), planner)).containsEntry("status", "COMPLETED");
    }
    @Test void childFinalRejectionReopensOriginalProductionReview() {
        var parent = claim(reverse(), planner); var child = claim(childRequest(parent), buyer);
        requests.escalate((String) child.get("id"), m("expectedRevision", child.get("revision")), buyer);
        child = requests.get((String) child.get("id"), manager);
        requests.reject((String) child.get("id"), m("commandId", "reject", "expectedRevision", child.get("revision"), "reason", "추가 발주 변경 불가"), manager);
        var original = requests.get((String) parent.get("id"), planner);
        assertThat(original).containsEntry("status", "IN_PROGRESS").containsEntry("needsAttention", true);
        assertThat(map(original.get("linkedOutcome"))).containsEntry("reason", "추가 발주 변경 불가");
    }
    @Test void finalExecutionAndFinalRejectionHaveOneWinner() throws Exception {
        var r = forward(); String task = prepared(r); String id = (String) r.get("id");
        var current = requests.get(id, manager);
        var rejection = m("commandId", "race", "expectedRevision", current.get("revision"), "reason", "최종 거절");
        try (var pool = Executors.newFixedThreadPool(2)) {
            Callable<Boolean> a = () -> { try { batch.complete(task, Map.of("requestId", "exec")); return true; } catch (ApiError e) { return false; } };
            Callable<Boolean> b = () -> { try { requests.reject(id, rejection, manager); return true; } catch (ApiError e) { return false; } };
            var results = pool.invokeAll(List.of(a,b));
            assertThat(results.stream().filter(f -> {try {return f.get();} catch(Exception e){throw new RuntimeException(e);}}).count()).isEqualTo(1);
        }
        boolean completed = "COMPLETED".equals(requests.get(id, planner).get("status"));
        assertThat(poQty()).isEqualTo(completed ? 400 : 500);
        assertThat(planDate()).isEqualTo(completed ? "2026-11-25" : "2026-12-14");
    }
}
