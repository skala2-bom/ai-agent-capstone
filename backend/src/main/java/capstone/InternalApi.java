package capstone;

import java.time.LocalDate;
import java.util.*;

import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;

/**
 * business-mcp 도구 1차 8개가 부르는 {@code /internal/**} (contracts.md 2.1).
 *
 * <p>지금 상태
 * <ul>
 *   <li>조회 4개 — seed DB(db/seed.sql)에서 읽는다</li>
 *   <li>계산 2개 — 실제 계산 ({@link ImpactAnalysis}, {@link OptionAnalysis})</li>
 *   <li>변경 2개 — 팀장 승인과 도구·인자가 같을 때만 저장한다. 변경·이력·승인 상태를 한 트랜잭션으로 ({@link Approvals})</li>
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
 * <p>금액은 응답 직전에 {@code X-Role}대로 가린다 ({@link Masking}). 아직 없는 것: 서비스 토큰 확인 (9단계).
 */
@RestController
@RequestMapping("/internal")
class InternalApi {

    /** 시연 기준일 (contracts.md 1.4). 실제 날짜를 쓰면 데모 결과가 날마다 바뀐다. */
    static final LocalDate TODAY = LocalDate.of(2026, 11, 2);

    private final JdbcTemplate db;
    private final ImpactAnalysis impact;
    private final OptionAnalysis options;
    private final Approvals approvals;
    private final TransactionTemplate tx;

    InternalApi(JdbcTemplate db, ImpactAnalysis impact, OptionAnalysis options, Approvals approvals, TransactionTemplate tx) {
        this.db = db;
        this.impact = impact;
        this.options = options;
        this.approvals = approvals;
        this.tx = tx;
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

    /**
     * get_inventory — 현재 재고. type(FG|PART)·item_id는 둘 다 선택. 반제품은 재고가 없어 나오지 않는다.
     *
     * <p>{@code used_in}: 이 부품이 들어가는 완제품 이름 (BOM을 반제품까지 따라 내려가 찾는다). 2개 이상이면 공용 부품이다.
     * 화면의 자재 목록이 "공용 N개 제품"을 보여 주려고 더했다 — 칸을 더한 것이라 도구 쪽은 영향이 없다.
     */
    @GetMapping("/inventory")
    Map<String, Object> getInventory(@RequestParam(required = false) String type,
                                     @RequestParam(name = "item_id", required = false) String itemId) {
        type = blankToNull(type);
        itemId = blankToNull(itemId);
        if (itemId != null) getItem(itemId);  // 없는 ID면 빈 목록 대신 NOT_FOUND로 알려 준다
        List<Map<String, Object>> rows = db.queryForList("""
                select v.item_id, i.name, i.type, v.on_hand, i.safety_stock,
                       i.supplier_id, s.name as supplier_name, i.lead_time_days
                from inventory v join item i using (item_id) left join supplier s on s.supplier_id = i.supplier_id
                where (cast(? as text) is null or i.type = ?) and (cast(? as text) is null or v.item_id = ?)
                order by i.type, v.item_id""", type, type, itemId, itemId);
        Map<String, List<String>> usedIn = new HashMap<>();
        db.query("""
                with recursive x(fg, child) as (
                    select b.parent_id, b.child_id from bom b join item f on f.item_id = b.parent_id and f.type = 'FG'
                    union
                    select x.fg, b.child_id from x join bom b on b.parent_id = x.child)
                select distinct x.child, f.name from x join item f on f.item_id = x.fg order by 2""",
                rs -> { usedIn.computeIfAbsent(rs.getString(1), k -> new ArrayList<>()).add(rs.getString(2)); });
        rows.forEach(r -> r.put("used_in", usedIn.getOrDefault((String) r.get("item_id"), List.of())));
        return m("as_of", TODAY.toString(), "items", rows);
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
        itemId = blankToNull(itemId);
        if (blankToNull(status) == null) status = "OPEN";
        if (itemId != null) getItem(itemId);
        return m("purchase_orders", db.queryForList("""
                select po_id, po.item_id, i.name as item_name, po.supplier_id, s.name as supplier_name, qty, unit_price, qty * unit_price as amount,
                       order_date::text as order_date, due_date::text as due_date, status, parent_po_id
                from purchase_order po join supplier s on s.supplier_id = po.supplier_id join item i on i.item_id = po.item_id
                where status = ? and (cast(? as text) is null or po.item_id = ?)
                  and (cast(? as date) is null or due_date >= ?) and (cast(? as date) is null or due_date <= ?)
                order by due_date, po_id""", status, itemId, itemId, from, from, to, to));
    }

    // ------------------------------------------------------------------
    // 2차 조회 3개 (contracts.md 2.1)
    // ------------------------------------------------------------------

    /** explode_bom — 제품을 부품 단위로 펼친다. on_date 기본은 기준일 (설계변경 유효일 때문에 날짜가 필요하다) */
    @GetMapping("/bom/{productId}/explode")
    Map<String, Object> explodeBom(@PathVariable String productId,
                                   @RequestParam(name = "on_date", required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate onDate) {
        return impact.bom(productId, onDate == null ? TODAY : onDate);
    }

    /** where_used — 이 부품이 들어가는 완제품 */
    @GetMapping("/bom/where-used/{itemId}")
    Map<String, Object> whereUsed(@PathVariable String itemId,
                                  @RequestParam(name = "on_date", required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate onDate) {
        return impact.whereUsed(itemId, onDate == null ? TODAY : onDate);
    }

    /**
     * get_supplier_metrics — 협력사 × 부품 평가지표 (decisions.md 2절). item_id를 빼면 전부.
     * 주 협력사가 먼저 나온다. 단가는 응답 직전에 역할대로 가려진다 ({@link Masking}).
     * <b>조회만</b> 도구로 연다 — 입력은 사람이 화면에서만 한다 (decisions.md 2.4).
     */
    @GetMapping("/suppliers/metrics")
    Map<String, Object> supplierMetrics(@RequestParam(name = "item_id", required = false) String itemId) {
        itemId = blankToNull(itemId);
        if (itemId != null) getItem(itemId);
        return m("metrics", db.queryForList("""
                select m.supplier_id, s.name as supplier_name, s.is_primary, m.item_id, i.name as item_name,
                       m.unit_price, m.lead_time_days, m.expedite_cost_per_day, m.split_fee, m.delay_penalty,
                       m.on_time_rate, m.quality_score, m.evaluated_at::text as evaluated_at, m.evaluated_by
                from supplier_metric m join supplier s using (supplier_id) join item i on i.item_id = m.item_id
                where cast(? as text) is null or m.item_id = ?
                order by m.item_id, s.is_primary desc, m.supplier_id""", itemId, itemId));
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
    @SuppressWarnings("unchecked")
    Map<String, Object> compareOptions(@RequestBody(required = false) Map<String, Object> body) {
        Set<String> items = new HashSet<>();
        if (body != null && body.get("issues") instanceof List<?> issues)
            for (Object i : issues)
                if (i instanceof Map<?, ?> issue && issue.get("item_id") != null) items.add(String.valueOf(issue.get("item_id")));
        // constraint: 팀장이 재요청하며 정한 날짜·분할 수량 (decisions 6.47). 없으면 기본 후보
        Map<String, Object> constraint = body != null && body.get("constraint") instanceof Map<?, ?> c
                ? new LinkedHashMap<>((Map<String, Object>) c) : Map.of();
        return options.compare(whatif(body), items, constraint);
    }

    // ------------------------------------------------------------------
    // 변경 2개 — 승인 필요. 승인 확인 → 검사 → 변경 + 이력 + 승인 EXECUTED를 한 트랜잭션으로
    // ------------------------------------------------------------------

    /** request_due_date_change — 발주 입고일 변경. 검사: 발주 있나 → 승인 → OPEN → 리드타임. */
    @PostMapping("/purchase-orders/{id}/due-date")
    Map<String, Object> changeDueDate(@PathVariable String id, @RequestBody Map<String, Object> body) {
        return tx.execute(t -> {
            Change c = begin(id, body, "request_due_date_change");
            LocalDate newDue = checkLeadTime(c.po(), body.get("new_due_date"));
            db.update("update purchase_order set due_date = ? where po_id = ?", newDue, id);
            Map<String, Object> after = m("po_id", id, "qty", c.po().get("qty"), "due_date", newDue.toString());
            approvals.executed(c.approvalId(), c.requester(), "request_due_date_change", id, c.before(), after);
            return m("po_id", id,
                    "before", m("due_date", c.po().get("due_date")),
                    "after", m("due_date", newDue.toString()),
                    "status", "OPEN");
        });
    }

    /**
     * split_po — 발주를 둘로 나눠 떼어낸 쪽만 날짜를 옮긴다 (decisions.md 3절).
     *
     * <p>검사 순서는 decisions.md 3.3 그대로: OPEN → 수량 범위 → 리드타임 → 날짜가 같은지.
     * MOQ는 다시 보지 않는다 (3.4). 새 발주는 원 발주의 협력사·단가·발주일을 그대로 물려받는다 —
     * 같은 물량을 나눠 받는 것이지 새로 주문하는 게 아니다.
     */
    @PostMapping("/purchase-orders/{id}/split")
    Map<String, Object> splitPo(@PathVariable String id, @RequestBody Map<String, Object> body) {
        return tx.execute(t -> {
            Change c = begin(id, body, "split_po");
            Map<String, Object> po = c.po();
            int qty = (int) po.get("qty");
            int splitQty = body.get("split_qty") instanceof Number n ? n.intValue() : 0;
            if (splitQty <= 0 || splitQty >= qty)
                throw new ApiError("INVALID_SPLIT_QTY", "분할 수량이 범위를 벗어났습니다.",
                        "split_qty는 1 ~ " + (qty - 1) + " 사이여야 합니다 (원 발주 " + qty + "개).");
            LocalDate newDue = checkLeadTime(po, body.get("new_due_date"));
            if (newDue.toString().equals(po.get("due_date")))
                throw new ApiError("SPLIT_NOT_USEFUL", "분할 후 입고일이 원 발주와 같습니다.",
                        "입고일이 같으면 나눌 이유가 없습니다. 다른 new_due_date를 주거나 분할하지 마세요.");

            // 새 번호 = 원 발주와 같은 달(PO-YYMM-)의 다음 번호. 원 발주를 잠근 트랜잭션 안이라 같은 원 발주끼리는 겹치지 않는다
            String prefix = id.substring(0, id.lastIndexOf('-') + 1);
            Integer last = db.queryForObject(
                    "select max(split_part(po_id, '-', 3)::int) from purchase_order where po_id like ?", Integer.class, prefix + "%");
            String newId = prefix + "%03d".formatted(last + 1);
            db.update("update purchase_order set qty = ? where po_id = ?", qty - splitQty, id);
            db.update("""
                    insert into purchase_order (po_id, item_id, supplier_id, qty, unit_price, order_date, due_date, status, parent_po_id)
                    select ?, item_id, supplier_id, ?, unit_price, order_date, ?, 'OPEN', po_id from purchase_order where po_id = ?""",
                    newId, splitQty, newDue, id);

            Map<String, Object> original = m("po_id", id, "qty", qty - splitQty, "due_date", po.get("due_date"), "status", "OPEN");
            Map<String, Object> created = m("po_id", newId, "qty", splitQty, "due_date", newDue.toString(), "status", "OPEN", "parent_po_id", id);
            approvals.executed(c.approvalId(), c.requester(), "split_po", id, c.before(), List.of(original, created));
            return m("original", original, "new", created);
        });
    }

    /**
     * change_po_qty — 발주 수량 변경 (2차, 과잉 해결용). 검사: 승인 → OPEN → 수량 → MOQ.
     * 분할(3.4)과 달리 MOQ를 본다 — 이건 나눠 받는 게 아니라 주문량 자체가 줄어드는 것이라,
     * 협력사의 생산 로트 기준(MOQ)이 그대로 적용된다. 0개로 만들려면 cancel_po를 쓴다.
     */
    @PostMapping("/purchase-orders/{id}/qty")
    Map<String, Object> changePoQty(@PathVariable String id, @RequestBody Map<String, Object> body) {
        return tx.execute(t -> {
            Change c = begin(id, body, "change_po_qty");
            int qty = (int) c.po().get("qty");
            if (!(body.get("new_qty") instanceof Number n) || n.intValue() <= 0 || n.doubleValue() != n.intValue())
                throw ApiError.invalid("new_qty는 1 이상의 정수여야 합니다.", "0개로 만들려면 cancel_po를 쓰세요.");
            int newQty = ((Number) body.get("new_qty")).intValue();
            if (newQty == qty)
                throw ApiError.invalid("수량이 지금과 같습니다 (" + qty + "개).", "바꿀 수량을 주세요.");
            int moq = db.queryForObject("select moq from item where item_id = ?", Integer.class, c.po().get("item_id"));
            if (newQty < moq)
                throw new ApiError("BELOW_MOQ", "최소 발주 수량 미만입니다.",
                        "최소 수량은 " + moq + "개입니다. " + moq + "개 이상으로 다시 요청하거나, 다 필요 없으면 cancel_po를 쓰세요.");
            db.update("update purchase_order set qty = ? where po_id = ?", newQty, id);
            Map<String, Object> after = m("po_id", id, "item_id", c.po().get("item_id"), "qty", newQty, "due_date", c.po().get("due_date"));
            approvals.executed(c.approvalId(), c.requester(), "change_po_qty", id, c.before(), after);
            return m("po_id", id, "before", m("qty", qty), "after", m("qty", newQty), "status", "OPEN");
        });
    }

    /** cancel_po — 발주 취소 (2차, 과잉·설계변경 진부화 해결용). 검사: 승인 → OPEN. 행은 지우지 않고 CANCELLED로 둔다 (이력) */
    @PostMapping("/purchase-orders/{id}/cancel")
    Map<String, Object> cancelPo(@PathVariable String id, @RequestBody Map<String, Object> body) {
        return tx.execute(t -> {
            Change c = begin(id, body, "cancel_po");
            db.update("update purchase_order set status = 'CANCELLED' where po_id = ?", id);
            Map<String, Object> after = new LinkedHashMap<>(c.before());
            after.put("status", "CANCELLED");
            approvals.executed(c.approvalId(), c.requester(), "cancel_po", id, c.before(), after);
            return m("po_id", id, "before", m("status", "OPEN"), "after", m("status", "CANCELLED"));
        });
    }

    /** 변경 하나의 앞단에서 모은 것 */
    Map<String, Object> updatePurchaseApproved(String id, Map<String, Object> body) {
        return tx.execute(t -> {
            Change c = begin(id, body, "update_purchase");
            LocalDate due = checkLeadTime(c.po(), body.get("new_due_date"));
            if (!(body.get("new_qty") instanceof Number n) || n.intValue() <= 0 || n.doubleValue() != n.intValue())
                throw ApiError.invalid("수량은 양의 정수여야 합니다.", "수정안을 다시 확인하세요.");
            int qty = ((Number) body.get("new_qty")).intValue();
            int moq = db.queryForObject("select moq from item where item_id=?", Integer.class, c.po().get("item_id"));
            if (qty < moq) throw ApiError.invalid("최소 발주 수량 " + moq + "개 미만입니다.", "수량을 다시 확인하세요.");
            db.update("update purchase_order set qty=?, due_date=? where po_id=?", qty, due, id);
            Map<String, Object> after = m("po_id", id, "item_id", c.po().get("item_id"), "qty", qty, "due_date", due.toString());
            approvals.executed(c.approvalId(), c.requester(), "update_purchase", id, c.before(), after);
            return m("po_id", id, "before", c.before(), "after", after, "status", "OPEN");
        });
    }

    record Change(Map<String, Object> po, Map<String, Object> before, String approvalId, String requester) {}

    /**
     * 변경 도구 공통 앞단: 발주를 잠그고 → 승인 확인 → OPEN 확인. 트랜잭션 안에서 부른다.
     *
     * <p>승인을 OPEN보다 먼저 보는 이유: 승인 없는 호출은 발주 상태와 상관없이 성립하지 않는다.
     * 승인은 APPROVED이고 도구·인자가 카드와 같아야 한다 ({@link Approvals#requireApproved}).
     */
    private Change begin(String id, Map<String, Object> body, String tool) {
        Map<String, Object> po = one("""
                select po.po_id, po.item_id, po.qty, po.status, po.order_date::text as order_date, po.due_date::text as due_date,
                       m.lead_time_days
                from purchase_order po
                join supplier_metric m on m.supplier_id = po.supplier_id and m.item_id = po.item_id
                where po.po_id = ? for update of po""", id, "발주", "purchase_order", "po_id");
        Map<String, Object> actual = Approvals.withoutApprovalId(body);
        actual.put("po_id", id);
        String requester = approvals.requireApproved(body.get("approval_id"), tool, actual);
        if (!"OPEN".equals(po.get("status")))
            throw new ApiError("PO_NOT_OPEN", "열려 있는 발주가 아닙니다.",
                    "현재 상태는 " + po.get("status") + "입니다. OPEN 발주만 바꿀 수 있습니다.");
        Map<String, Object> before = m("po_id", id, "item_id", po.get("item_id"), "qty", po.get("qty"), "due_date", po.get("due_date"));
        return new Change(po, before, (String) body.get("approval_id"), requester);
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

    /**
     * 빈 쿼리 값({@code ?item_id=})은 "조건 없음"으로 본다. business-mcp(httpx)는 None 인자를 빈 값으로 보내는데,
     * 이걸 값으로 받으면 type="" 조건에 걸려 오류 없이 빈 목록이 나간다 — 모델이 "재고 없음"으로 읽는다.
     * 부르는 쪽마다 고치는 대신 받는 쪽 한 곳에서 막는다.
     */
    static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s;
    }

    /** 순서를 지키는 Map. Map.of는 순서가 없고 null 값과 10쌍 초과를 못 받는다. */
    static Map<String, Object> m(Object... kv) {
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) map.put((String) kv[i], kv[i + 1]);
        return map;
    }
}
