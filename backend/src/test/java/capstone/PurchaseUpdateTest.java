package capstone;

import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.amqp.core.AmqpTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.support.TransactionTemplate;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;

import static capstone.InternalApi.m;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class PurchaseUpdateTest {
    static JdbcTemplate db;
    static ScreenApi screen;
    static AmqpTemplate queue = mock(AmqpTemplate.class);
    static final String PO = "PO-2610-106";
    static final Jwt BUYER = jwt("buyer", "BUYER"), BOSS = jwt("approver", "APPROVER"), PLANNER = jwt("planner", "PLANNER");

    @BeforeAll
    static void startDb() {
        var pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
                .withDatabaseName("bom").withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d");
        pg.start();
        var ds = new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app");
        db = new JdbcTemplate(ds);
        var json = new ObjectMapper();
        var approvals = new Approvals(db, json);
        var tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
        var impact = new ImpactAnalysis(db);
        var internal = new InternalApi(db, impact, new OptionAnalysis(db, impact), approvals, tx);
        screen = new ScreenApi(internal, db, approvals, tx, new Tasks(db, json, approvals, "http://localhost:1", queue));
    }

    @BeforeEach
    void reset() {
        db.execute("truncate ai_task cascade");
        db.execute("truncate change_history");
        db.update("update purchase_order set qty=1200, due_date='2026-11-20', status='OPEN' where po_id=?", PO);
        org.mockito.Mockito.reset(queue);
    }

    @Test
    @SuppressWarnings("unchecked")
    void 수량과_당긴_입고일을_함께_저장하고_이력과_분석을_남긴다() {
        var result = screen.updatePurchase(PO, m("qty", 1000, "due_date", "2026-11-19", "reason", "  협력사 확정  "), BUYER);
        assertThat(result).containsEntry("qty", 1000).containsEntry("due_date", "2026-11-19");
        assertThat(db.queryForMap("select qty, due_date::text as due_date from purchase_order where po_id=?", PO))
                .containsEntry("qty", 1000).containsEntry("due_date", "2026-11-19");
        var history = db.queryForMap("select action, before::text, after::text, reason, changed_by, approval_id from change_history");
        assertThat(history).containsEntry("action", "update_purchase").containsEntry("reason", "협력사 확정")
                .containsEntry("changed_by", "buyer").containsEntry("approval_id", null);
        assertThat(history.get("before").toString()).contains("1200", "2026-11-20");
        assertThat(history.get("after").toString()).contains("1000", "2026-11-19");
        assertThat(db.queryForMap("select user_id, role, trigger from ai_task where task_id=?", result.get("analysis_task_id")))
                .containsEntry("user_id", "buyer").containsEntry("role", "PLANNER").containsEntry("trigger", "SYSTEM");
        ArgumentCaptor<Object> capture = ArgumentCaptor.forClass(Object.class);
        verify(queue).convertAndSend(eq(TaskQueue.TASKS), capture.capture());
        Map<String, Object> body = (Map<String, Object>) ((Map<?, ?>) capture.getValue()).get("body");
        assertThat((List<Map<String, Object>>) body.get("whatif")).containsExactly(
                m("op", "change_po_qty", "po_id", PO, "new_qty", 1000),
                m("op", "request_due_date_change", "po_id", PO, "new_due_date", "2026-11-19"));
    }

    @Test
    void 한_항목만_바꿀_수_있고_변경_없는_재요청은_이력을_늘리지_않는다() {
        assertThat(screen.updatePurchase(PO, m("qty", 1300, "reason", "수량 확정"), BOSS))
                .containsEntry("qty", 1300).containsEntry("due_date", "2026-11-20");
        assertThat(screen.updatePurchase(PO, m("due_date", "2026-11-27", "reason", "일정 확정"), BUYER))
                .containsEntry("qty", 1300).containsEntry("due_date", "2026-11-27");
        invalid(m("qty", 1300, "due_date", "2026-11-27", "reason", "반복"));
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isEqualTo(2);
        assertThat(db.queryForObject("select count(*) from ai_task", Integer.class)).isEqualTo(2);
    }

    @Test
    void 권한_수량_날짜_사유_상태를_검증하고_실패하면_아무것도_바꾸지_않는다() {
        assertThatThrownBy(() -> screen.updatePurchase(PO, m("qty", 1000, "reason", "수정"), PLANNER))
                .isInstanceOfSatisfying(ApiError.class, e -> assertThat(e.code).isEqualTo("ROLE_NOT_ALLOWED"));
        for (Object qty : List.of(0, -1, 1.5, 2147483648L, "1000")) invalid(m("qty", qty, "reason", "수정"));
        for (String due : List.of("2026-02-30", "2026-11-01", "잘못된 날짜")) invalid(m("due_date", due, "reason", "수정"));
        invalid(m("qty", 1000)); invalid(m("qty", 1000, "reason", " ")); invalid(m("reason", "수정"));
        for (String status : List.of("RECEIVED", "CANCELLED")) {
            db.update("update purchase_order set status=? where po_id=?", status, PO);
            assertThatThrownBy(() -> screen.updatePurchase(PO, m("qty", 1000, "reason", "수정"), BUYER))
                    .isInstanceOfSatisfying(ApiError.class, e -> assertThat(e.code).isEqualTo("PO_NOT_OPEN"));
        }
        assertThat(db.queryForObject("select qty from purchase_order where po_id=?", Integer.class, PO)).isEqualTo(1200);
        assertThat(db.queryForObject("select count(*) from change_history", Integer.class)).isZero();
        assertThat(db.queryForObject("select count(*) from ai_task", Integer.class)).isZero();
        verifyNoInteractions(queue);
    }

    private static void invalid(Map<String, Object> body) {
        assertThatThrownBy(() -> screen.updatePurchase(PO, body, BUYER))
                .isInstanceOfSatisfying(ApiError.class, e -> assertThat(e.code).isEqualTo("INVALID_ARGUMENT"));
    }
    private static Jwt jwt(String id, String role) {
        return Jwt.withTokenValue("test").header("alg", "none").subject(id).claim("role", role).build();
    }
}
