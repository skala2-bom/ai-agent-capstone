package capstone;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

import static capstone.InternalApi.m;
import static org.assertj.core.api.Assertions.assertThat;

/** SEC-001 역할별 금액 가리기 (contracts.md 1.2). 키는 남고 값만 null이 되는가. */
class MaskingTest {

    /** 단가·총액·추가 비용·비용 등급이 다 들어 있는 응답 (중첩까지) */
    private static Map<String, Object> sample() {
        return m("purchase_orders", List.of(m("po_id", "PO-1", "unit_price", 3500, "amount", 1_750_000)),
                "options", List.of(m("option_id", "OPT-1", "added_cost", 390_000L, "cost_level", "MID")));
    }

    @Test
    void 팀장은_전부_본다() {
        var r = sample();
        Masking.mask(r, "APPROVER");
        assertThat(r).isEqualTo(sample());
    }

    @Test
    void 구매는_단가만_가려진다() {
        var r = sample();
        Masking.mask(r, "BUYER");
        assertThat(r.toString()).contains("unit_price=null", "amount=1750000", "added_cost=390000", "cost_level=MID");
    }

    @Test
    void 생산관리는_금액이_다_가려지고_등급만_보인다() {
        var r = sample();
        Masking.mask(r, "PLANNER");
        assertThat(r.toString()).contains("unit_price=null", "amount=null", "added_cost=null", "cost_level=MID");
    }

    @Test
    void 역할을_모르거나_없으면_생산관리_기준으로_가린다() {
        for (String role : new String[]{null, "", "ADMIN"}) {
            var r = sample();
            Masking.mask(r, role);
            assertThat(r.toString()).as("role=%s", role).contains("unit_price=null", "amount=null", "added_cost=null");
        }
        var lower = sample();
        Masking.mask(lower, "approver");   // 대소문자는 봐준다
        assertThat(lower).isEqualTo(sample());
    }
}
