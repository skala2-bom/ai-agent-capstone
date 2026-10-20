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
        // 대기열은 가짜 — 자동 분석 작업은 만들어지고 QUEUED로 남는다 (여기서는 작업이 생기는지만 본다)
        screen = new ScreenApi(internal, db, approvals, tx,
                new Tasks(db, new ObjectMapper(), approvals, "http://localhost:1", org.mockito.Mockito.mock(org.springframework.amqp.core.AmqpTemplate.class)));
        // 승인 카드는 작업(ai_task)을 참조한다. agent 없이 카드를 만들려고 작업 행만 넣어 둔다
        db.update("insert into ai_task (task_id, thread_id, user_id, role, question) values ('T-1', 'T-1', 'buyer', 'BUYER', 'q'), ('T-2', 'T-2', 'planner', 'PLANNER', 'q')");
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
        // 자동 분석 작업: SYSTEM, PLANNER 역할(금액 가림), 질문에 무엇이 바뀌었는지가 들어간다
        assertThat(db.queryForMap("select trigger, role, user_id, question from ai_task where task_id = ?", po.get("analysis_task_id")))
                .containsEntry("trigger", "SYSTEM").containsEntry("role", "PLANNER").containsEntry("user_id", "buyer")
                .hasEntrySatisfying("question", q -> assertThat((String) q).contains("PO-2610-106", "2026-11-20 → 2026-11-27", "설비 고장"));
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

    @Test
    void 이차_도구_BOM_펼치기와_사용_제품() {
        // 허브 1대 = 구매 부품 16종 (기획안 1.1), MCU 1 · 수정진동자 2 (무선모듈 2개)
        var hub = internal.explodeBom("FG-HUB", null);
        assertThat((List<?>) hub.get("parts")).hasSize(16);
        assertThat(hub.get("parts").toString()).contains("item_id=MCU, name=MCU, qty=1", "item_id=CRYSTAL, name=수정진동자, qty=2");
        // 설계변경: 11/30까지 옛 케이스, 12/1부터 새 케이스
        assertThat(internal.explodeBom("FG-DOOR", java.time.LocalDate.of(2026, 11, 30)).get("parts").toString()).contains("CASE-DOOR-V1").doesNotContain("CASE-DOOR-V2");
        assertThat(internal.explodeBom("FG-DOOR", java.time.LocalDate.of(2026, 12, 1)).get("parts").toString()).contains("CASE-DOOR-V2").doesNotContain("CASE-DOOR-V1");
        // MCU는 4종 모두, 릴레이는 플러그 1 · 스위치 2
        assertThat((List<?>) internal.whereUsed("MCU", null).get("products")).hasSize(4);
        assertThat(internal.whereUsed("RELAY", null).get("products").toString()).contains("FG-PLUG", "qty_per_unit=1", "FG-SWITCH", "qty_per_unit=2");
        assertCode(() -> internal.explodeBom("MCU", null), "NOT_FOUND");
    }

    @Test
    void 이차_도구_수량_변경과_취소는_승인을_거쳐_실행된다() {
        db.update("insert into ai_task (task_id, thread_id, user_id, role, question) values ('T-3', 'T-3', 'buyer', 'BUYER', 'q')");
        // SC2 해결: 플러그 케이스 11/27분 800 → 500
        Map<String, Object> qtyArgs = m("po_id", "PO-2610-115", "new_qty", 500);
        String q = (String) approvals.create("T-3", "buyer", "BUYER", m("tool", "change_po_qty", "args", qtyArgs)).get("approval_id");
        approvals.decide("T-3", q, "APPROVED", null, "approver");
        // MOQ(300) 미만으로 바꾸려는 호출은 승인과 인자가 달라서 먼저 막히므로, MOQ 검사는 별도 승인으로 본다
        Map<String, Object> tooSmall = m("po_id", "PO-2610-115", "new_qty", 100);
        String small = (String) approvals.create("T-3", "buyer", "BUYER", m("tool", "change_po_qty", "args", tooSmall)).get("approval_id");
        approvals.decide("T-3", small, "APPROVED", null, "approver");
        assertCode(() -> internal.changePoQty("PO-2610-115", call(tooSmall, small)), "BELOW_MOQ");
        assertThat(internal.changePoQty("PO-2610-115", call(qtyArgs, q)).toString()).contains("before={qty=800}", "after={qty=500}");

        // SC3 해결: 옛 케이스 발주 취소
        Map<String, Object> cancelArgs = m("po_id", "PO-2610-110");
        String c = (String) approvals.create("T-3", "buyer", "BUYER", m("tool", "cancel_po", "args", cancelArgs)).get("approval_id");
        assertCode(() -> internal.cancelPo("PO-2610-110", call(cancelArgs, c)), "APPROVAL_REQUIRED");   // 승인 전
        approvals.decide("T-3", c, "APPROVED", null, "approver");
        assertThat(internal.cancelPo("PO-2610-110", call(cancelArgs, c))).containsEntry("after", Map.of("status", "CANCELLED"));
        assertCode(() -> internal.cancelPo("PO-2610-110", call(cancelArgs, c)), "APPROVAL_REQUIRED");  // 같은 승인 두 번
        assertThat(db.queryForObject("select status from purchase_order where po_id = 'PO-2610-110'", String.class)).isEqualTo("CANCELLED");
    }

    @Test
    void 평가지표는_구매가_화면에서만_입력한다() {
        Jwt buyer = jwt("buyer", "BUYER"), planner = jwt("planner", "PLANNER");
        assertCode(() -> screen.updateMetric("SUP-POWER", "RELAY", m("on_time_rate", 80.5), planner), "ROLE_NOT_ALLOWED");
        assertCode(() -> screen.updateMetric("SUP-POWER", "RELAY", m("on_time_rate", 120), buyer), "INVALID_ARGUMENT");
        assertCode(() -> screen.updateMetric("SUP-POWER", "MCU", m("on_time_rate", 90), buyer), "NOT_FOUND");
        var row = screen.updateMetric("SUP-POWER", "RELAY", m("on_time_rate", 80.5, "quality_score", 90), buyer);
        assertThat(row).containsEntry("evaluated_by", "buyer").containsEntry("evaluated_at", "2026-11-02").containsEntry("quality_score", 90);
        assertThat(((Number) row.get("on_time_rate")).doubleValue()).isEqualTo(80.5);
    }

    @Test
    void 빈_쿼리_값은_조건_없음으로_본다() {
        // business-mcp(httpx)는 None 인자를 ?item_id= 처럼 빈 값으로 보낸다. 빈 목록이나 NOT_FOUND가 나면 안 된다
        assertThat((List<?>) internal.getInventory("", "").get("items")).hasSizeGreaterThan(28);
        assertThat((List<?>) internal.listReceipts("", "", null, null).get("purchase_orders")).isNotEmpty();
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
