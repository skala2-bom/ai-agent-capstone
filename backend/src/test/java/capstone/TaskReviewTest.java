package capstone;

import java.util.*;
import java.util.concurrent.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.springframework.amqp.core.AmqpTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.security.oauth2.jwt.Jwt;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;
import static capstone.InternalApi.m;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class TaskReviewTest {
    static JdbcTemplate db, admin;
    static Approvals approvals;
    static Tasks tasks;
    static AmqpTemplate queue;
    String task, a, b;
    static final Jwt BUYER = jwt("buyer", "BUYER"), BOSS = jwt("approver", "APPROVER");

    @BeforeAll static void database() {
        var pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
                .withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d");
        pg.start();
        db = new JdbcTemplate(new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app"));
        admin = new JdbcTemplate(new DriverManagerDataSource(pg.getJdbcUrl(), pg.getUsername(), pg.getPassword()));
        var json = new ObjectMapper();
        approvals = new Approvals(db, json);
        queue = mock(AmqpTemplate.class);
        tasks = new Tasks(db, json, approvals, "http://localhost:1", queue);
    }

    @BeforeEach void fixtures() {
        reset(queue);
        task = UUID.randomUUID().toString();
        db.update("insert into ai_task (task_id, thread_id, user_id, role, question) values (?, ?, 'buyer', 'BUYER', 'q')", task, task);
        a = create("PO-2609-001"); b = create("PO-2609-002");
    }

    String create(String po) {
        return (String) tasks.receiveEvent(task, m("type", "APPROVAL_REQUIRED", "tool", "request_due_date_change",
                "args", m("po_id", po, "new_due_date", "2026-11-25"), "problem", po + " 고정 문제")).get("approvalId");
    }

    Approvals.TaskReview ready() {
        String version = UUID.randomUUID().toString();
        tasks.addEvent(task, m("type", "TASK_REVIEW_READY", "reviewId", version, "approvalIds", List.of(a, b)));
        return new Approvals.TaskReview(UUID.randomUUID().toString(), version, "전체 생산 차질 해결\n담당자 의견", Map.of(a, "첫 메모", b, "둘째 메모"));
    }

    @Test void allCardsAreSentWithFixedProblemsAndReplayDoesNotUndoManagerDecision() {
        var body = ready();
        var cards = approvals.reviewTask(task, "buyer", true, body);
        assertThat(cards).hasSize(2).allSatisfy(c -> {
            assertThat(c).containsEntry("status", "PENDING").containsEntry("taskProblem", body.taskProblem());
            assertThat(c.get("problem").toString()).endsWith("고정 문제");
            assertThat(c.get("memo")).isEqualTo(body.memos().get(c.get("approval_id")));
        });
        approvals.decide(task, a, "APPROVED", null, "approver");
        var replay = approvals.reviewTask(task, "buyer", true, body);
        assertThat(replay).filteredOn(c -> a.equals(c.get("approval_id"))).allSatisfy(c -> assertThat(c.get("status")).isEqualTo("APPROVED"));
        assertThatThrownBy(() -> approvals.reviewTask(task, "buyer", false, body)).isInstanceOf(ApiError.class);
        assertThat(approvals.card(b).get("status")).isEqualTo("PENDING");
    }

    @Test void earlyPartialStaleAndForeignRequestsChangeNothing() {
        var early = new Approvals.TaskReview("early", "not-ready", "큰 문제", Map.of(a, "a", b, "b"));
        assertThatThrownBy(() -> approvals.reviewTask(task, "buyer", true, early)).isInstanceOf(ApiError.class);
        var body = ready();
        assertThatThrownBy(() -> approvals.reviewTask(task, "buyer", true,
                new Approvals.TaskReview("partial", body.reviewId(), "큰 문제", Map.of(a, "a")))).isInstanceOf(ApiError.class);
        assertThatThrownBy(() -> approvals.reviewTask(task, "approver", true, body)).isInstanceOf(ApiError.class);
        create("PO-2609-003"); // 준비 완료 이후 새 카드가 들어오면 예전 화면으로 보낼 수 없다.
        assertThatThrownBy(() -> approvals.reviewTask(task, "buyer", true, body)).isInstanceOf(ApiError.class);
        assertThat(db.queryForList("select status from approval where task_id = ?", String.class, task)).containsOnly("DRAFT");
    }

    @Test void validationFailureAndDatabaseFailureRollBackTheEntireTask() {
        var body = ready();
        assertThatThrownBy(() -> approvals.reviewTask(task, "buyer", true,
                new Approvals.TaskReview("blank", body.reviewId(), " ", body.memos()))).isInstanceOf(ApiError.class);
        // 둘째 UPDATE를 DB에서 실패시켜 첫째 UPDATE도 되돌아가는지 확인한다.
        admin.execute("create or replace function reject_test_review() returns trigger language plpgsql as $$ begin if new.approval_id = '" + b
                + "' and new.status = 'PENDING' then raise exception 'test failure'; end if; return new; end $$");
        admin.execute("create trigger reject_test_review before update on approval for each row execute function reject_test_review()");
        try {
            assertThatThrownBy(() -> approvals.reviewTask(task, "buyer", true, body)).isInstanceOf(RuntimeException.class);
            assertThat(db.queryForList("select status from approval where task_id = ?", String.class, task)).containsOnly("DRAFT");
            assertThat(approvals.card(a).get("memo")).isNull();
        } finally { admin.execute("drop trigger reject_test_review on approval"); admin.execute("drop function reject_test_review()"); }
    }

    @Test void competingSendAndWithdrawHaveOneWholeTaskWinner() throws Exception {
        var first = ready();
        var second = new Approvals.TaskReview("other", first.reviewId(), first.taskProblem(), first.memos());
        var gate = new CountDownLatch(1);
        try (var pool = Executors.newFixedThreadPool(2)) {
            Future<Boolean> send = pool.submit(() -> { gate.await(); try { approvals.reviewTask(task, "buyer", true, first); return true; } catch (ApiError e) { return false; } });
            Future<Boolean> withdraw = pool.submit(() -> { gate.await(); try { approvals.reviewTask(task, "buyer", false, second); return true; } catch (ApiError e) { return false; } });
            gate.countDown();
            assertThat(send.get(5, TimeUnit.SECONDS) ^ withdraw.get(5, TimeUnit.SECONDS)).isTrue();
            assertThat(new HashSet<>(db.queryForList("select status from approval where task_id = ?", String.class, task))).hasSize(1);
        }
    }

    @Test void withdrawnCardsAreHiddenFromManagerIncludingDetailAndFilteredLists() {
        var body = ready();
        approvals.reviewTask(task, "buyer", false, body);
        for (String status : Arrays.asList(null, "WITHDRAWN", "DRAFT"))
            assertThat(approvals.list(status, BOSS)).noneMatch(c -> task.equals(c.get("task_id")));
        assertThatThrownBy(() -> approvals.get(a, BOSS)).isInstanceOf(ApiError.class);
        assertThat(approvals.get(a, BUYER)).containsEntry("status", "WITHDRAWN").containsEntry("taskProblem", body.taskProblem());
    }

    @Test void legacyPlainTextMemosStillReadNormally() {
        for (String memo : List.of("예전 메모", "{잘못된 JSON", "{\"personalMemo\":\"일반 JSON\"}")) {
            db.update("update approval set memo = ? where approval_id = ?", memo, a);
            assertThat(approvals.card(a)).containsEntry("memo", memo).doesNotContainKey("taskProblem");
        }
    }

    @Test void withdrawalSurvivesQueueOutageAndBackendRestart() {
        var body = ready();
        doThrow(new RuntimeException("offline")).when(queue).convertAndSend(eq(TaskQueue.TASKS), any(Object.class));
        tasks.withdraw(task, body, BUYER);
        assertThat(tasks.find(task).status()).isEqualTo("RUNNING");
        reset(queue);
        new Tasks(db, new ObjectMapper(), approvals, "http://localhost:1", queue).recoverWithdrawals();
        verify(queue, atLeast(2)).convertAndSend(eq(TaskQueue.TASKS), any(Object.class));
        assertThat(tasks.find(task).status()).isEqualTo("RUNNING");
    }

    @Test void subsequentDraftReviewLeavesEarlierManagerDecisionsAlone() {
        approvals.reviewTask(task, "buyer", true, ready());
        approvals.decide(task, a, "REJECTED", "다른 방법", "approver");
        String c = create("PO-2609-003");
        tasks.addEvent(task, m("type", "TASK_REVIEW_READY", "reviewId", "next", "approvalIds", List.of(a, b, c)));
        var body = new Approvals.TaskReview("next-request", "next", "재제안 전체 문제", Map.of(c, "보완 메모"));
        assertThat(approvals.reviewTask(task, "buyer", true, body)).hasSize(1);
        assertThat(approvals.card(a).get("status")).isEqualTo("REJECTED");
        assertThat(approvals.card(b).get("taskProblem")).isEqualTo("전체 생산 차질 해결\n담당자 의견");
    }

    Map<String, Object> reproposalEvent(String previous, String proposal) {
        return m("type", "APPROVAL_REQUIRED", "tool", "request_due_date_change",
                "args", m("po_id", "PO-2609-001", "new_due_date", "2026-11-24"),
                "summary", "[재제안 2차 · 이전 요청 " + previous + "] MCU 전량 조정", "problem", "MCU 고정 문제",
                "previousApprovalId", previous, "proposalId", proposal, "revision", 2);
    }

    @Test void rejectedReproposalGoesDirectlyToManagerWithOriginalBuyerContext() {
        var body = ready();
        approvals.reviewTask(task, "buyer", true, body);
        approvals.decide(task, a, "REJECTED", "납기 하루 더 당김", "approver");
        var event = reproposalEvent(a, "reproposal-1");
        var proposal = approvals.create(task, "buyer", "BUYER", event);
        assertThat(proposal).containsEntry("status", "PENDING").containsEntry("taskProblem", body.taskProblem())
                .containsEntry("memo", "첫 메모").containsEntry("previousApprovalId", a);
        assertThat(approvals.list("PENDING", BOSS)).extracting(c -> c.get("approval_id")).contains(proposal.get("approval_id"));
        assertThat(approvals.card(b)).containsEntry("status", "PENDING");
        // 재제안이 최초 구매 전송 requestId를 물려받으면 원 요청 재시도가 깨진다.
        assertThat(approvals.reviewTask(task, "buyer", true, body)).hasSize(2);
        String id = (String) proposal.get("approval_id");
        approvals.decide(task, id, "APPROVED", null, "approver");
        assertThat(approvals.create(task, "buyer", "BUYER", event))
                .containsEntry("approval_id", id).containsEntry("status", "APPROVED").containsEntry("created", false);
        assertThat(db.queryForObject("select count(*) from approval where task_id = ?", Integer.class, task)).isEqualTo(3);
    }

    @Test void unrejectedForeignTaskAndDifferentItemCannotBypassBuyerReview() {
        var event = reproposalEvent(a, "invalid-proposal");
        assertThatThrownBy(() -> approvals.create(task, "buyer", "BUYER", event)).isInstanceOf(ApiError.class); // DRAFT
        approvals.reviewTask(task, "buyer", true, ready());
        assertThatThrownBy(() -> approvals.create(task, "buyer", "BUYER", event)).isInstanceOf(ApiError.class); // PENDING
        approvals.decide(task, a, "REJECTED", "재검토", "approver");
        var wrongItem = new LinkedHashMap<>(event);
        wrongItem.put("args", m("po_id", "PO-2609-002", "new_due_date", "2026-11-24"));
        assertThatThrownBy(() -> approvals.create(task, "buyer", "BUYER", wrongItem)).isInstanceOf(ApiError.class);
        String other = UUID.randomUUID().toString();
        db.update("insert into ai_task (task_id, thread_id, user_id, role, question) values (?, ?, 'buyer', 'BUYER', 'q')", other, other);
        assertThatThrownBy(() -> approvals.create(other, "buyer", "BUYER", event)).isInstanceOf(ApiError.class);
        assertThat(db.queryForObject("select count(*) from approval where task_id = ?", Integer.class, task)).isEqualTo(2);
    }

    @Test void existingReproposalDraftIsPromotedOnceUsingItsExactCardId() {
        approvals.reviewTask(task, "buyer", true, ready());
        approvals.decide(task, a, "REJECTED", "재검토", "approver");
        var oldEvent = reproposalEvent(a, "legacy-proposal");
        oldEvent.remove("previousApprovalId");
        String id = (String) approvals.create(task, "buyer", "BUYER", oldEvent).get("approval_id");
        assertThat(approvals.card(id)).containsEntry("status", "DRAFT");
        var recovery = reproposalEvent(a, "legacy-proposal");
        recovery.put("approvalId", id);
        assertThat(approvals.create(task, "buyer", "BUYER", recovery)).containsEntry("approval_id", id)
                .containsEntry("status", "PENDING").containsEntry("created", false).containsEntry("memo", "첫 메모");
        approvals.decide(task, id, "REJECTED", "다시 검토", "approver");
        assertThat(approvals.create(task, "buyer", "BUYER", recovery)).containsEntry("approval_id", id).containsEntry("status", "REJECTED");
        assertThat(db.queryForObject("select count(*) from approval where task_id = ?", Integer.class, task)).isEqualTo(3);
        recovery.put("approvalId", b);
        assertThatThrownBy(() -> approvals.create(task, "buyer", "BUYER", recovery)).isInstanceOf(ApiError.class);
    }

    @Test void reproposalPreservesLegacyMemoWithoutInventingAnOverallProblem() {
        approvals.reviewTask(task, "buyer", true, ready());
        db.update("update approval set memo = '예전 협력사 메모' where approval_id = ?", a);
        approvals.decide(task, a, "REJECTED", "재검토", "approver");
        var proposal = approvals.create(task, "buyer", "BUYER", reproposalEvent(a, "legacy-memo"));
        assertThat(proposal).containsEntry("status", "PENDING").containsEntry("memo", "예전 협력사 메모").containsEntry("taskProblem", "");
    }

    private static Jwt jwt(String sub, String role) {
        return Jwt.withTokenValue("t").header("alg", "none").subject(sub).claim("role", role).build();
    }
}
