package capstone;

import java.util.*;

import com.fasterxml.jackson.databind.ObjectMapper;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;

import static capstone.InternalApi.m;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 승인 → 실행 흐름 (아키텍처 4.4, contracts.md 2.7·3.3, decisions.md 6.18).
 *
 * <p>지키려는 것은 하나다: <b>팀장이 승인한 카드와 도구·인자가 정확히 같을 때만 발주가 바뀐다.</b>
 * 시나리오 순서대로 DB 상태가 이어지므로 흐름마다 테스트 하나로 쓴다. 이 클래스의 두 테스트는 같은 DB를 쓰고,
 * ImpactAnalysisTest와는 컨테이너를 따로 쓴다.
 */
class ApprovalFlowTest {

    static PostgreSQLContainer<?> pg;
    static JdbcTemplate db;
    static Approvals approvals;
    static InternalApi internal;
    static ScreenApi screen;
    static ImpactAnalysis impact;

    @BeforeAll
    static void startDb() {
        pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
                .withDatabaseName("bom")
                .withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d");
        pg.start();
        DriverManagerDataSource ds = new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app");
        db = new JdbcTemplate(ds);
        TransactionTemplate tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
        approvals = new Approvals(db, new ObjectMapper());
        impact = new ImpactAnalysis(db);
        internal = new InternalApi(db, impact, new OptionAnalysis(db, impact), approvals, tx);
        screen = new ScreenApi(internal, db, approvals, tx);
    }

    static final Map<String, Object> SPLIT = m("po_id", "PO-2609-001", "split_qty", 100, "new_due_date", "2026-11-25");

    @Test
    void SC1_추천안이_승인을_거쳐_실제로_실행되고_결품이_풀린다() {
        // ① agent가 APPROVAL_REQUIRED를 보낸다 → PENDING 카드
        var card = approvals.create("T-1", "buyer", "BUYER",
                m("type", "APPROVAL_REQUIRED", "tool", "split_po", "args", SPLIT, "summary", "MCU 100개 먼저", "amount", 390_000));
        String id = (String) card.get("approval_id");
        assertThat(card).containsEntry("status", "PENDING").containsEntry("created", true);
        assertThat(card.get("before").toString()).contains("qty=500", "due_date=2026-12-12");
        assertThat(card.get("after").toString()).contains("qty=400", "qty=100", "2026-11-25");

        // ② 전송 재시도로 같은 이벤트가 또 와도 카드는 하나
        var again = approvals.create("T-1", "buyer", "BUYER", m("tool", "split_po", "args", SPLIT));
        assertThat(again).containsEntry("approval_id", id).containsEntry("created", false);

        // ③ 생산관리 담당자의 작업에서는 승인 요청 자체가 안 된다
        assertThatThrownBy(() -> approvals.create("T-2", "planner", "PLANNER", m("tool", "split_po", "args", SPLIT)))
                .isInstanceOfSatisfying(ApiError.class, e -> assertThat(e.code).isEqualTo("ROLE_NOT_ALLOWED"));

        // ④ 승인 전에 실행하면 거부
        assertCode(() -> internal.splitPo("PO-2609-001", call(SPLIT, id)), "APPROVAL_REQUIRED");

        // ⑤ 거절은 사유 필수 → 승인
        assertCode(() -> approvals.decide("T-1", id, "REJECTED", " ", "approver"), "INVALID_ARGUMENT");
        assertThat(approvals.decide("T-1", id, "APPROVED", null, "approver")).containsEntry("status", "APPROVED");
        assertCode(() -> approvals.decide("T-1", id, "APPROVED", null, "approver"), "APPROVAL_NOT_PENDING");

        // ⑥ 승인과 다른 인자(200개)로 부르면 거부, 발주는 그대로
        assertCode(() -> internal.splitPo("PO-2609-001", call(m("split_qty", 200, "new_due_date", "2026-11-25"), id)), "APPROVAL_REQUIRED");
        assertThat(db.queryForObject("select qty from purchase_order where po_id = 'PO-2609-001'", Integer.class)).isEqualTo(500);

        // ⑦ 승인 그대로 부르면 실행: 원 발주 400(12/12 그대로) + 새 발주 100(11/25)
        var result = internal.splitPo("PO-2609-001", call(SPLIT, id));
        assertThat(result.toString()).contains("po_id=PO-2609-001, qty=400, due_date=2026-12-12", "po_id=PO-2609-010, qty=100, due_date=2026-11-25");
        assertThat(approvals.card(id)).containsEntry("status", "EXECUTED");
        // 변경 이력: 같은 트랜잭션에서 한 줄, 승인 ID와 요청자가 남는다 (AUD-001)
        assertThat(db.queryForList("select action, changed_by, approval_id from change_history where target_id = 'PO-2609-001'"))
                .containsExactly(Map.of("action", "split_po", "changed_by", "buyer", "approval_id", id));

        // ⑧ 같은 승인으로 두 번 실행할 수 없다
        assertCode(() -> internal.splitPo("PO-2609-001", call(SPLIT, id)), "APPROVAL_REQUIRED");

        // ⑨ 이제 허브를 당겨도 MCU 결품이 없다 — 계산이 바뀐 DB를 본다
        var after = impact.analyze(List.of(m("op", "reschedule_plan", "plan_id", "PP-05", "new_date", "2026-11-25")));
        // (다른 테스트가 릴레이 입고를 늦춰 둘 수 있어 MCU만 본다. 두 테스트는 같은 DB를 쓴다)
        assertThat(after.get("shortages").toString()).doesNotContain("item_id=MCU");
    }

    @Test
    void 화면_변경은_승인_없이_바로_바뀌고_이력이_남는다() {
        Jwt buyer = jwt("buyer", "BUYER"), planner = jwt("planner", "PLANNER"), approver = jwt("approver", "APPROVER");

        // 협력사 지연 입력 (SC4의 시작): 구매·팀장만, 사유 필수
        assertCode(() -> screen.supplierDelay("PO-2610-106", m("due_date", "2026-11-27", "reason", "설비 고장"), planner), "ROLE_NOT_ALLOWED");
        assertCode(() -> screen.supplierDelay("PO-2610-106", m("due_date", "2026-11-27"), buyer), "INVALID_ARGUMENT");
        var po = screen.supplierDelay("PO-2610-106", m("due_date", "2026-11-27", "reason", "파워링크 설비 고장 통보"), buyer);
        assertThat(po).containsEntry("due_date", "2026-11-27");
        // DB가 바뀌었으니 whatif 없이도 SC4 결품이 보인다
        assertThat(impact.analyze(List.of()).get("shortages").toString()).contains("item_id=RELAY", "projected_stock=-1000");

        // 생산계획 변경: 생산관리만 (팀장도 안 된다 — 팀장이 가진 건 구매 권한이다)
        assertCode(() -> screen.updatePlan("PP-07", m("qty", 500), approver), "ROLE_NOT_ALLOWED");
        assertCode(() -> screen.updatePlan("PP-07", m("date", "2026-10-01"), planner), "INVALID_ARGUMENT");
        assertThat(screen.updatePlan("PP-07", m("qty", 500, "reason", "주문 감소"), planner)).containsEntry("qty", 500);

        assertThat(db.queryForList("select target_id, action, approval_id from change_history where approval_id is null order by id"))
                .containsExactly(m("target_id", "PO-2610-106", "action", "supplier_delay", "approval_id", null),
                                 m("target_id", "PP-07", "action", "update_plan", "approval_id", null));
    }

    // ------------------------------------------------------------------

    /** 변경 도구 호출 body = 승인 카드의 args + approval_id (agent-draft가 이렇게 부른다) */
    private static Map<String, Object> call(Map<String, Object> args, String approvalId) {
        Map<String, Object> body = new LinkedHashMap<>(args);
        body.remove("po_id");   // po_id는 경로로 간다
        body.put("approval_id", approvalId);
        return body;
    }

    private static Jwt jwt(String sub, String role) {
        return Jwt.withTokenValue("t").header("alg", "none").subject(sub).claim("role", role).build();
    }

    private static void assertCode(Runnable r, String code) {
        assertThatThrownBy(r::run).isInstanceOfSatisfying(ApiError.class, e -> assertThat(e.code).isEqualTo(code));
    }
}
