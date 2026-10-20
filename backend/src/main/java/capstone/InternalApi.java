package capstone;

import java.time.LocalDate;
import java.util.*;

import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

/**
 * business-mcp 도구 1차 8개가 부르는 {@code /internal/**} (contracts.md 2.1).
 *
 * <p>지금 상태
 * <ul>
 *   <li>조회 4개 — seed DB(db/seed.sql)에서 읽는다</li>
 *   <li>계산 2개 — 실제 계산 ({@link ImpactAnalysis}, {@link OptionAnalysis})</li>
 *   <li>변경 2개 — DB에서 발주를 읽어 검사는 진짜로 하고, 저장은 아직 안 한다 (→ 승인 API와 함께)</li>
 * </ul>
 * 모양은 계약대로 고정이다. 안쪽이 바뀌어도 AI·MCP 파트는 고칠 것이 없다.
 *
 * <p>오류는 4xx + {@code {error, message, retry_hint}} (contracts.md 2.5). MCP 파트가 ToolError 변환과
 * "모델이 retry_hint를 읽고 다시 부르기"를 지금 시험할 수 있다.
 *
 * <p>JSON 키는 snake_case다. DB 칸 이름을 그대로 내보내고, 모델이 도구 결과를 그대로 읽기 때문에
 * contracts 2.2·2.3과 맞췄다. 수량 단위는 개, 금액은 원, 날짜는 YYYY-MM-DD
 * (날짜는 SQL에서 {@code ::text}로 바꿔 Jackson 날짜 설정에 휘둘리지 않게 했다).
 *
 * <p>아직 없는 것: 요청자 헤더({@code X-Role})를 읽은 금액 마스킹(SEC-001), 서비스 토큰 확인.
 */
@RestController
@RequestMapping("/internal")
class InternalApi {

    /** 시연 기준일 (contracts.md 1.4). 실제 날짜를 쓰면 데모 결과가 날마다 바뀐다. */
    static final LocalDate TODAY = LocalDate.of(2026, 11, 2);

    private final JdbcTemplate db;
    private final ImpactAnalysis impact;
    private final OptionAnalysis options;

    InternalApi(JdbcTemplate db, ImpactAnalysis impact, OptionAnalysis options) {
        this.db = db;
        this.impact = impact;
        this.options = options;
    }

    // ------------------------------------------------------------------
    // 조회 4개
    // ------------------------------------------------------------------

    /** get_item — 품목 정보. 단가는 주 협력사의 평가지표 단가다 (완제품·반제품은 null). */
    @GetMapping("/items/{id}")
    Map<String, Object> getItem(@PathVariable String id) {
        return one("""
                select i.item_id, i.name, i.type, i.unit, i.lead_time_days, i.moq, i.safety_stock,
                       i.high_value, m.unit_price, i.supplier_id
                from item i
                left join supplier_metric m on m.supplier_id = i.supplier_id and m.item_id = i.item_id
                where i.item_id = ?""", id, "품목", "item", "item_id");
    }

    /** get_inventory — 현재 재고. type(FG|PART)·item_id는 둘 다 선택. 반제품은 재고가 없어 나오지 않는다. */
    @GetMapping("/inventory")
    Map<String, Object> getInventory(@RequestParam(required = false) String type,
                                     @RequestParam(name = "item_id", required = false) String itemId) {
        if (itemId != null) getItem(itemId);  // 없는 ID면 빈 목록 대신 NOT_FOUND로 알려 준다
        return m("as_of", TODAY.toString(), "items", db.queryForList("""
                select v.item_id, i.name, i.type, v.on_hand, i.safety_stock
                from inventory v join item i using (item_id)
                where (cast(? as text) is null or i.type = ?) and (cast(? as text) is null or v.item_id = ?)
                order by i.type, v.item_id""", type, type, itemId, itemId));
    }

    /** list_plans — 기간 안의 생산 계획. from·to는 양끝 포함, 둘 다 선택. */
    @GetMapping("/plans")
    Map<String, Object> listPlans(@RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
                                  @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to) {
        return m("plans", db.queryForList("""
                select plan_id, product_id, plan_date::text as plan_date, qty, status
                from production_plan
                where (cast(? as date) is null or plan_date >= ?) and (cast(? as date) is null or plan_date <= ?)
                order by plan_date, plan_id""", from, from, to, to));
    }

    /** list_receipts — 입고 예정 발주. status 기본값 OPEN. from·to는 입고 예정일 기준. */
    @GetMapping("/purchase-orders")
    Map<String, Object> listReceipts(@RequestParam(defaultValue = "OPEN") String status,
                                     @RequestParam(name = "item_id", required = false) String itemId,
                                     @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
                                     @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to) {
        if (itemId != null) getItem(itemId);
        return m("purchase_orders", db.queryForList("""
                select po_id, item_id, supplier_id, qty, unit_price, qty * unit_price as amount,
                       order_date::text as order_date, due_date::text as due_date, status, parent_po_id
                from purchase_order
                where status = ? and (cast(? as text) is null or item_id = ?)
                  and (cast(? as date) is null or due_date >= ?) and (cast(? as date) is null or due_date <= ?)
                order by due_date, po_id""", status, itemId, itemId, from, from, to, to));
    }

    // ------------------------------------------------------------------
    // 계산 2개
    // ------------------------------------------------------------------

    /**
     * analyze_impact — 가정 변경(whatif)을 적용했을 때의 결품·경고·과잉 (contracts.md 2.2).
     *
     * <p><b>계산이 여기(backend) 있는 이유</b>: 업무 규칙을 한 곳에만 두기 위해서다 (아키텍처 1.1).
     * business-mcp가 직접 계산하면 화면 조회와 AI 분석이 서로 다른 숫자를 낼 수 있다.
     * 계산 자체는 {@link ImpactAnalysis}. body: {@code {"whatif": [{"op": ..., ...}]}}
     */
    @PostMapping("/analysis/impact")
    Map<String, Object> analyzeImpact(@RequestBody(required = false) Map<String, Object> body) {
        return impact.analyze(whatif(body));
    }

    /** body의 whatif를 꺼낸다. 없으면 빈 목록 (= 지금 상태). */
    private static List<Map<String, Object>> whatif(Map<String, Object> body) {
        Object whatif = body == null ? null : body.get("whatif");
        if (whatif != null && !(whatif instanceof List<?>))
            throw ApiError.invalid("whatif는 목록이어야 합니다.", "예: {\"whatif\": [{\"op\": \"reschedule_plan\", \"plan_id\": \"PP-05\", \"new_date\": \"2026-11-25\"}]}");
        List<Map<String, Object>> ops = new ArrayList<>();
        if (whatif != null) for (Object o : (List<?>) whatif) {
            if (!(o instanceof Map<?, ?> op)) throw ApiError.invalid("whatif 항목은 객체여야 합니다: " + o, "각 항목에 op와 인자를 넣으세요.");
            @SuppressWarnings("unchecked") Map<String, Object> cast = (Map<String, Object>) op;
            ops.add(cast);
        }
        return ops;
    }

    /**
     * compare_options — 결품 해결 대안 비교 (contracts.md 2.3). 계산은 {@link OptionAnalysis}.
     *
     * <p>body: {@code {"whatif": [...], "issues": [{"item_id": "MCU"}, ...]}}
     * <ul>
     *   <li>whatif — analyze_impact에 넣었던 것과 같은 가정. 대안은 이 가정 위에서 만들고 검증한다</li>
     *   <li>issues — 풀고 싶은 결품. analyze_impact의 shortages 항목을 그대로 넣어도 된다 (item_id만 본다).
     *       비우면 모든 결품</li>
     * </ul>
     */
    @PostMapping("/analysis/options")
    Map<String, Object> compareOptions(@RequestBody(required = false) Map<String, Object> body) {
        Set<String> items = new HashSet<>();
        if (body != null && body.get("issues") instanceof List<?> issues)
            for (Object i : issues)
                if (i instanceof Map<?, ?> issue && issue.get("item_id") != null) items.add(String.valueOf(issue.get("item_id")));
        return options.compare(whatif(body), items);
    }

    // ------------------------------------------------------------------
    // 변경 2개 — 승인 필요. 지금은 검사만 하고 저장하지 않는다
    // ------------------------------------------------------------------

    /** request_due_date_change — 발주 입고일 변경. 검사: 승인 → OPEN → 리드타임. */
    @PostMapping("/purchase-orders/{id}/due-date")
    Map<String, Object> changeDueDate(@PathVariable String id, @RequestBody Map<String, Object> body) {
        Map<String, Object> po = openPoWithApproval(id, body);
        LocalDate newDue = checkLeadTime(po, body.get("new_due_date"));
        return m("po_id", id,
                "before", m("due_date", po.get("due_date")),
                "after", m("due_date", newDue.toString()),
                "status", "OPEN");
    }

    /**
     * split_po — 발주를 둘로 나눠 떼어낸 쪽만 날짜를 옮긴다 (decisions.md 3절).
     *
     * <p>검사 순서는 decisions.md 3.3 그대로: OPEN → 수량 범위 → 리드타임 → 날짜가 같은지.
     * MOQ는 다시 보지 않는다 (3.4). 새 발주 번호는 원 발주와 같은 달 번호의 다음 번호다.
     */
    @PostMapping("/purchase-orders/{id}/split")
    Map<String, Object> splitPo(@PathVariable String id, @RequestBody Map<String, Object> body) {
        Map<String, Object> po = openPoWithApproval(id, body);
        int qty = (int) po.get("qty");
        int splitQty = body.get("split_qty") instanceof Number n ? n.intValue() : 0;
        if (splitQty <= 0 || splitQty >= qty)
            throw new ApiError("INVALID_SPLIT_QTY", "분할 수량이 범위를 벗어났습니다.",
                    "split_qty는 1 ~ " + (qty - 1) + " 사이여야 합니다 (원 발주 " + qty + "개).");
        LocalDate newDue = checkLeadTime(po, body.get("new_due_date"));
        if (newDue.toString().equals(po.get("due_date")))
            throw new ApiError("SPLIT_NOT_USEFUL", "분할 후 입고일이 원 발주와 같습니다.",
                    "입고일이 같으면 나눌 이유가 없습니다. 다른 new_due_date를 주거나 분할하지 마세요.");
        String prefix = id.substring(0, id.lastIndexOf('-') + 1);  // "PO-2609-"
        Integer last = db.queryForObject(
                "select max(split_part(po_id, '-', 3)::int) from purchase_order where po_id like ?",
                Integer.class, prefix + "%");
        return m("original", m("po_id", id, "qty", qty - splitQty, "due_date", po.get("due_date"), "status", "OPEN"),
                "new", m("po_id", prefix + "%03d".formatted(last + 1), "qty", splitQty,
                        "due_date", newDue.toString(), "status", "OPEN", "parent_po_id", id));
    }

    /**
     * 변경 도구 공통 앞단 검사: 발주가 있나 → 승인 ID가 있나 → OPEN인가.
     *
     * <p>승인을 OPEN보다 먼저 보는 이유: 승인 없는 호출은 발주 상태와 상관없이 성립하지 않는다.
     * 지금은 approval_id가 <b>있는지만</b> 본다. 승인 API가 생기면 APPROVED인지, 도구·인자가
     * 승인 카드와 같은지까지 본다 (contracts.md 2.7).
     */
    private Map<String, Object> openPoWithApproval(String id, Map<String, Object> body) {
        Map<String, Object> po = one("""
                select po.po_id, po.qty, po.status, po.order_date::text as order_date, po.due_date::text as due_date,
                       m.lead_time_days
                from purchase_order po
                join supplier_metric m on m.supplier_id = po.supplier_id and m.item_id = po.item_id
                where po.po_id = ?""", id, "발주", "purchase_order", "po_id");
        if (!(body.get("approval_id") instanceof String s) || s.isBlank())
            throw new ApiError("APPROVAL_REQUIRED", "승인 없이 발주를 바꿀 수 없습니다.",
                    "변경 도구는 승인 후에만 부를 수 있습니다. 승인 요청을 먼저 올리고 받은 approval_id를 넣으세요.");
        if (!"OPEN".equals(po.get("status")))
            throw new ApiError("PO_NOT_OPEN", "열려 있는 발주가 아닙니다.",
                    "현재 상태는 " + po.get("status") + "입니다. OPEN 발주만 바꿀 수 있습니다.");
        return po;
    }

    /**
     * LT-001 (아키텍처 1.2 "발주일 + 리드타임 ≤ 필요일")을 이미 낸 발주에 적용한다.
     *
     * <p><b>기준은 오늘이 아니라 그 발주의 발주일이다.</b> 협력사는 발주일부터 만들기 시작했으므로
     * 발주일 + 리드타임 이후라면 물리적으로 받을 수 있다. "오늘 + 리드타임"으로 보면 MCU(56일)는
     * 12/28 전에는 어떤 발주도 당길 수 없게 되어 SC1·SC4의 해결안이 전부 막힌다 (decisions.md 7.2).
     * 당기는 데 드는 돈은 막지 않고 compare_options의 비용(expedite_cost_per_day)으로 드러낸다.
     *
     * <p>retry_hint에 가장 빠른 가능일을 넣어 모델이 그 날짜로 다시 부르게 한다.
     */
    private static LocalDate checkLeadTime(Map<String, Object> po, Object newDueDate) {
        LocalDate earliest = ImpactAnalysis.earliestReceipt(
                LocalDate.parse((String) po.get("order_date")), (int) po.get("lead_time_days"));
        LocalDate newDue;
        try {
            newDue = LocalDate.parse(String.valueOf(newDueDate));
        } catch (Exception e) {
            throw ApiError.invalid("new_due_date를 읽지 못했습니다.",
                    "YYYY-MM-DD 형식으로 주세요. 가장 빠른 가능일은 " + earliest + "입니다.");
        }
        if (newDue.isBefore(earliest))
            throw new ApiError("LEADTIME_INFEASIBLE", "리드타임상 그 날짜에 받을 수 없습니다.",
                    "가장 빠른 가능일은 " + earliest + "입니다. 그 이후 날짜로 다시 요청하세요.");
        return newDue;
    }

    // ------------------------------------------------------------------
    // 작은 도우미
    // ------------------------------------------------------------------

    /**
     * 한 줄 조회. 없으면 NOT_FOUND이고, retry_hint에 비슷한 ID를 넣는다 (교재 2장 find_product 패턴).
     * 비슷한 게 없으면 앞에서부터 20개를 보여 준다. table·col은 코드 안의 상수만 받는다.
     */
    private Map<String, Object> one(String sql, String id, String what, String table, String col) {
        List<Map<String, Object>> rows = db.queryForList(sql, id);
        if (!rows.isEmpty()) return rows.getFirst();
        String list = "select " + col + " from " + table;
        List<String> ids = db.queryForList(list + " where " + col + " ilike ? order by 1 limit 10", String.class, "%" + id + "%");
        if (ids.isEmpty()) ids = db.queryForList(list + " order by 1 limit 20", String.class);
        throw new ApiError("NOT_FOUND", what + " " + id + "을(를) 찾을 수 없습니다.",
                "비슷한 ID: " + String.join(", ", ids));
    }

    /** 순서를 지키는 Map. Map.of는 순서가 없고 null 값과 10쌍 초과를 못 받는다. */
    static Map<String, Object> m(Object... kv) {
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) map.put((String) kv[i], kv[i + 1]);
        return map;
    }
}
