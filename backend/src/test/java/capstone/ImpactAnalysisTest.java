package capstone;

import java.util.*;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import org.testcontainers.utility.MountableFile;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 골든셋 테스트 — seed 데이터로 SC1~SC4의 정답이 나오는가 (decisions.md 7.3·7.4).
 *
 * <p>아키텍처 11장 "기능 적합성" 증빙이다. compose와 같은 이미지에 db/*.sql을 그대로 돌려서
 * 시연 때와 같은 데이터로 계산한다. 정답 표를 바꾸면 이 파일과 decisions.md 7.3을 같이 고친다.
 *
 * <p>Docker가 있어야 돈다: {@code cd backend && mvn test}. 이미지 빌드(Dockerfile)는 테스트를 건너뛴다.
 */
class ImpactAnalysisTest {

    static PostgreSQLContainer<?> pg;
    static ImpactAnalysis analysis;

    @BeforeAll
    static void startDb() {
        pg = new PostgreSQLContainer<>(DockerImageName.parse("pgvector/pgvector:pg16").asCompatibleSubstituteFor("postgres"))
                .withDatabaseName("bom")
                .withCopyFileToContainer(MountableFile.forHostPath("../db"), "/docker-entrypoint-initdb.d");
        pg.start();
        // 슈퍼유저가 아니라 backend와 같은 app 계정으로 읽는다. 권한 문제도 같이 잡힌다
        analysis = new ImpactAnalysis(new JdbcTemplate(new DriverManagerDataSource(pg.getJdbcUrl(), "app", "app")));
    }

    // ------------------------------------------------------------------
    // 변경 전
    // ------------------------------------------------------------------

    @Test
    void 변경_전에는_문제가_없다() {
        var r = run();
        assertThat(shortages(r)).isEmpty();
        assertThat(warnings(r)).isEmpty();
        assertThat(excesses(r)).isEmpty();
        assertThat(r.get("policy_version")).isEqualTo("v0.1");
    }

    // ------------------------------------------------------------------
    // SC1~SC4 (decisions.md 7.3)
    // ------------------------------------------------------------------

    @Test
    void SC1_허브를_11월25일로_당기면_MCU가_두_번_모자란다() {
        var r = run(op("reschedule_plan", "plan_id", "PP-05", "new_date", "2026-11-25"));
        assertThat(shortages(r)).containsExactly(
                "MCU PP-05 2026-11-25 -100 발주불가",     // 리드타임 56일 → 새 발주는 12/28
                "MCU PP-04 2026-11-30 -100 발주불가");    // 연쇄 결품: 허브가 먼저 가져가서 플러그가 모자란다
        assertThat(warnings(r)).isEmpty();
        assertThat(excesses(r)).isEmpty();
    }

    @Test
    void SC2_스위치는_늘고_플러그는_줄면_결품과_과잉이_같이_생긴다() {
        var r = run(op("change_plan_qty", "plan_id", "PP-03", "new_qty", 900),
                    op("change_plan_qty", "plan_id", "PP-04", "new_qty", 500));
        assertThat(shortages(r)).containsExactly(
                "CASE-SWITCH PP-03 2026-11-23 -200 발주가능",  // 21일 → 11/23 도착. 기획안의 "정상 안내"
                "POWER-MODULE PP-03 2026-11-23 -200 발주불가",
                "RELAY PP-03 2026-11-23 -400 발주불가",
                "RELAY PP-04 2026-11-30 -100 발주가능",
                "CASE-SWITCH PP-06 2027-01-11 -200 발주가능",  // 같은 부족분이 이어진 것 (합산하지 않음)
                "RELAY PP-06 2027-01-11 -100 발주가능");
        assertThat(warnings(r)).containsExactly("MCU PP-03 2026-11-23 0<300");
        assertThat(excesses(r)).containsExactly("CASE-PLUG 2026-11-27 300 (480000원)");  // 기획안: 48만 원
    }

    @Test
    void SC3_도어센서를_1월4일로_미루면_옛_케이스가_남고_새_케이스가_모자란다() {
        var r = run(op("reschedule_plan", "plan_id", "PP-02", "new_date", "2027-01-04"));
        assertThat(shortages(r)).containsExactly(
                "CASE-DOOR-V2 PP-02 2027-01-04 -800 발주가능",
                "CASE-DOOR-V2 PP-07 2027-01-25 -800 발주가능");
        assertThat(warnings(r)).isEmpty();
        // 기획안에 있던 BLE 칩·홀 센서 800은 빠진다: 리드타임 8주라 N=56일, 1/4 소요가 기간 안에 들어온다 (decisions.md 1.3)
        assertThat(excesses(r)).containsExactly(
                "CASE-DOOR-V1 2026-11-15 800 (640000원)",   // 12/1 설계변경 이후 쓸 생산이 없다. 기획안 64만 원
                "COIN-CELL 2026-11-15 800 (200000원)");     // 기획안 20만 원
    }

    @Test
    void SC4_릴레이_입고가_1주_늦으면_스위치_생산에서_1000개가_모자란다() {
        var r = run(op("request_due_date_change", "po_id", "PO-2610-106", "new_due_date", "2026-11-27"));
        assertThat(shortages(r)).containsExactly("RELAY PP-03 2026-11-23 -1000 발주불가");
        assertThat(warnings(r)).isEmpty();
        assertThat(excesses(r)).isEmpty();
    }

    // ------------------------------------------------------------------
    // 해결안 검증 (decisions.md 7.4) — agent의 verify가 하는 일과 같다
    // ------------------------------------------------------------------

    @Test
    void SC1_해결안_12월12일분에서_100개를_당기면_결품이_모두_풀린다() {
        var r = run(op("reschedule_plan", "plan_id", "PP-05", "new_date", "2026-11-25"),
                    op("split_po", "po_id", "PO-2609-001", "split_qty", 100, "new_due_date", "2026-11-25"));
        assertThat(shortages(r)).isEmpty();
        assertThat(warnings(r)).containsExactly("MCU PP-05 2026-11-25 0<300", "MCU PP-04 2026-11-30 0<300");
    }

    @Test
    void SC1_해결안_11월27일분에서_당기면_연쇄_결품이_남는다() {
        var r = run(op("reschedule_plan", "plan_id", "PP-05", "new_date", "2026-11-25"),
                    op("split_po", "po_id", "PO-2609-008", "split_qty", 100, "new_due_date", "2026-11-25"));
        assertThat(shortages(r)).containsExactly("MCU PP-04 2026-11-30 -100 발주불가");
    }

    @Test
    void SC4_해결안_1000개를_11월22일로_나눠_받으면_풀린다() {
        var r = run(op("request_due_date_change", "po_id", "PO-2610-106", "new_due_date", "2026-11-27"),
                    op("split_po", "po_id", "PO-2610-106", "split_qty", 1000, "new_due_date", "2026-11-22"));
        assertThat(shortages(r)).isEmpty();
        assertThat(warnings(r)).containsExactly("RELAY PP-03 2026-11-23 0<200");
    }

    // ------------------------------------------------------------------
    // 오류 — retry_hint가 비어 있지 않은가
    // ------------------------------------------------------------------

    @Test
    void 없는_계획이면_NOT_FOUND와_비슷한_ID를_준다() {
        assertThatThrownBy(() -> run(op("reschedule_plan", "plan_id", "PP-5", "new_date", "2026-11-25")))
                .isInstanceOfSatisfying(ApiError.class, e -> {
                    assertThat(e.code).isEqualTo("NOT_FOUND");
                    assertThat(e.retryHint).contains("PP-01");
                });
    }

    @Test
    void 모르는_연산이면_INVALID_ARGUMENT와_연산_목록을_준다() {
        assertThatThrownBy(() -> run(op("move_plan", "plan_id", "PP-05")))
                .isInstanceOfSatisfying(ApiError.class, e -> {
                    assertThat(e.code).isEqualTo("INVALID_ARGUMENT");
                    assertThat(e.retryHint).contains("reschedule_plan");
                });
    }

    // ------------------------------------------------------------------
    // 읽기 쉽게 한 줄로 바꾸는 도우미
    // ------------------------------------------------------------------

    @SafeVarargs
    private static Map<String, Object> run(Map<String, Object>... ops) {
        return analysis.analyze(List.of(ops));
    }

    private static Map<String, Object> op(String name, Object... kv) {
        Map<String, Object> op = new LinkedHashMap<>(InternalApi.m(kv));
        op.put("op", name);
        return op;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> list(Map<String, Object> r, String key) {
        return (List<Map<String, Object>>) r.get(key);
    }

    private static List<String> shortages(Map<String, Object> r) {
        return list(r, "shortages").stream().map(e -> "%s %s %s %s %s".formatted(e.get("item_id"), e.get("plan_id"),
                e.get("plan_date"), e.get("projected_stock"), (boolean) e.get("orderable") ? "발주가능" : "발주불가")).toList();
    }

    private static List<String> warnings(Map<String, Object> r) {
        return list(r, "warnings").stream().map(e -> "%s %s %s %s<%s".formatted(e.get("item_id"), e.get("plan_id"),
                e.get("plan_date"), e.get("projected_stock"), e.get("threshold"))).toList();
    }

    private static List<String> excesses(Map<String, Object> r) {
        return list(r, "excesses").stream().map(e -> "%s %s %s (%s원)".formatted(e.get("item_id"), e.get("date"),
                e.get("qty"), e.get("amount"))).toList();
    }
}
