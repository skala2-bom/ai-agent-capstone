package capstone;

import java.time.LocalDate;
import java.util.*;
import java.util.function.Predicate;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * business-mcp 도구 1차 8개가 부르는 {@code /internal/**} (contracts.md 2.1).
 *
 * <p><b>지금은 고정 응답이다.</b> AI·MCP 파트가 도구를 먼저 붙일 수 있게, 계약의 <b>모양</b>을
 * 먼저 내놓은 것이다. 숫자와 ID는 SC1("허브를 11/25로 당기면?") 시퀀스에서 가져온 예시이고,
 * seed 단계(기획안 데이터)에서 실제 DB 조회·계산으로 하나씩 바뀐다. 바뀌어도 모양은 그대로다.
 *
 * <p>고정 응답이어도 <b>오류 경로는 진짜로 낸다</b> (contracts.md 2.5). 없는 ID, 닫힌 발주,
 * 승인 없는 변경, 리드타임 위반을 넣으면 4xx + {@code {error, message, retry_hint}}가 나온다.
 * MCP 파트가 ToolError 변환과 "모델이 retry_hint를 읽고 다시 부르기"를 지금 시험할 수 있게 하려는 것이다.
 *
 * <p>JSON 키는 snake_case다. 도구 인자·결과를 모델이 그대로 읽기 때문에 contracts 2.2·2.3과 맞췄다.
 * 수량 단위는 개, 금액은 원, 날짜는 YYYY-MM-DD.
 *
 * <p>아직 없는 것: 요청자 헤더({@code X-Role} 등)를 읽은 금액 마스킹(SEC-001), 서비스 토큰 확인,
 * 실제 변경(지금은 검사만 하고 저장하지 않는다).
 */
// ponytail: 데이터가 이 파일 안의 Map이다. seed 단계에서 master·plan·purchase·inventory 모듈 + DB로 교체
@RestController
@RequestMapping("/internal")
class InternalApi {

    /** 시연 기준일 (contracts.md 1.4). 리드타임 검사의 "오늘". 실제 날짜를 쓰면 데모 결과가 날마다 바뀐다. */
    static final LocalDate TODAY = LocalDate.of(2026, 11, 2);

    // ------------------------------------------------------------------
    // 예시 데이터 (SC1·SC4 시퀀스에 나온 ID만). 순서를 지키려고 LinkedHashMap
    // ------------------------------------------------------------------

    private static final Map<String, Map<String, Object>> ITEMS = byId("item_id",
            m("item_id", "HUB", "name", "스마트 허브", "type", "FG", "unit", "EA",
                    "lead_time_days", 0, "moq", 0, "safety_stock", 0, "high_value", false,
                    "unit_price", null, "supplier_id", null),
            m("item_id", "SWITCH", "name", "스마트 스위치", "type", "FG", "unit", "EA",
                    "lead_time_days", 0, "moq", 0, "safety_stock", 0, "high_value", false,
                    "unit_price", null, "supplier_id", null),
            m("item_id", "MCU", "name", "MCU", "type", "PART", "unit", "EA",
                    "lead_time_days", 14, "moq", 300, "safety_stock", 50, "high_value", true,
                    "unit_price", 3200, "supplier_id", "SUP-01"),
            m("item_id", "RELAY", "name", "스위치 릴레이", "type", "PART", "unit", "EA",
                    "lead_time_days", 21, "moq", 500, "safety_stock", 100, "high_value", false,
                    "unit_price", 850, "supplier_id", "SUP-03"));

    private static final List<Map<String, Object>> INVENTORY = List.of(
            m("item_id", "HUB", "type", "FG", "on_hand", 120, "safety_stock", 0),
            m("item_id", "SWITCH", "type", "FG", "on_hand", 80, "safety_stock", 0),
            m("item_id", "MCU", "type", "PART", "on_hand", 400, "safety_stock", 50),
            m("item_id", "RELAY", "type", "PART", "on_hand", 600, "safety_stock", 100));

    private static final List<Map<String, Object>> PLANS = List.of(
            m("plan_id", "PP-04", "product_id", "SWITCH", "plan_date", "2026-11-26", "qty", 300, "status", "PLANNED"),
            m("plan_id", "PP-05", "product_id", "HUB", "plan_date", "2026-11-28", "qty", 200, "status", "PLANNED"));

    private static final Map<String, Map<String, Object>> POS = byId("po_id",
            m("po_id", "PO-2609-001", "item_id", "MCU", "supplier_id", "SUP-01", "qty", 500,
                    "due_date", "2026-11-30", "status", "OPEN", "unit_price", 3200, "amount", 1_600_000,
                    "parent_po_id", null),
            m("po_id", "PO-2610-106", "item_id", "RELAY", "supplier_id", "SUP-03", "qty", 1000,
                    "due_date", "2026-11-20", "status", "OPEN", "unit_price", 850, "amount", 850_000,
                    "parent_po_id", null),
            // 이미 입고된 발주. PO_NOT_OPEN 오류 경로를 시험하라고 넣었다
            m("po_id", "PO-2608-014", "item_id", "MCU", "supplier_id", "SUP-01", "qty", 300,
                    "due_date", "2026-10-28", "status", "RECEIVED", "unit_price", 3200, "amount", 960_000,
                    "parent_po_id", null));

    // ------------------------------------------------------------------
    // 조회 4개
    // ------------------------------------------------------------------

    /** get_item — 품목 정보 (리드타임·MOQ·안전재고). */
    @GetMapping("/items/{id}")
    Map<String, Object> getItem(@PathVariable String id) {
        return find(ITEMS, id, "품목");
    }

    /** get_inventory — 현재 재고. type·item_id는 둘 다 선택. */
    @GetMapping("/inventory")
    Map<String, Object> getInventory(@RequestParam(required = false) String type,
                                     @RequestParam(name = "item_id", required = false) String itemId) {
        if (itemId != null) find(ITEMS, itemId, "품목");  // 없는 ID면 빈 목록 대신 NOT_FOUND로 알려 준다
        return m("as_of", TODAY.toString(), "items", INVENTORY.stream()
                .filter(eq("type", type)).filter(eq("item_id", itemId)).toList());
    }

    /** list_plans — 기간 안의 생산 계획. from·to는 양끝 포함. */
    @GetMapping("/plans")
    Map<String, Object> listPlans(@RequestParam(required = false) String from,
                                  @RequestParam(required = false) String to) {
        return m("plans", PLANS.stream().filter(between("plan_date", from, to)).toList());
    }

    /** list_receipts — 입고 예정 발주. 상태 기본값 OPEN. */
    @GetMapping("/purchase-orders")
    Map<String, Object> listReceipts(@RequestParam(defaultValue = "OPEN") String status,
                                     @RequestParam(name = "item_id", required = false) String itemId,
                                     @RequestParam(required = false) String from,
                                     @RequestParam(required = false) String to) {
        if (itemId != null) find(ITEMS, itemId, "품목");
        return m("purchase_orders", POS.values().stream()
                .filter(eq("status", status)).filter(eq("item_id", itemId))
                .filter(between("due_date", from, to)).toList());
    }

    // ------------------------------------------------------------------
    // 계산 2개
    // ------------------------------------------------------------------

    /**
     * analyze_impact — 가정 변경(whatif)을 적용했을 때의 결품·경고·과잉 (contracts.md 2.2).
     *
     * <p><b>계산이 여기 있는 이유</b>: 업무 규칙을 한 곳에만 두기 위해서다 (아키텍처 1.1).
     * business-mcp가 직접 계산하면 화면 조회와 AI 분석이 서로 다른 숫자를 낼 수 있다.
     *
     * <p>지금 값은 SC1 정답 그대로다. 실제 계산을 넣을 때 (→ analysis 모듈):
     * <ul>
     *   <li>P(t) = 초기재고 + 누적입고 − 누적소요 를 날짜별로 만든다 (아키텍처 1.2 F-01)</li>
     *   <li>결품 P(t)&lt;0 / 경고 0≤P(t)&lt;안전재고 / 과잉은 고가 부품만, 기간은 max(리드타임, 30일)</li>
     *   <li>같은 날 결품과 경고가 겹치면 결품만 남긴다</li>
     * </ul>
     * 경고 한 건마다 rule_id·projected_stock·threshold를 붙여야 AI가 "왜 결품인지"를 지어내지 않는다.
     */
    @PostMapping("/analysis/impact")
    Map<String, Object> analyzeImpact(@RequestBody(required = false) Map<String, Object> body) {
        return m("policy_version", "v0.1",
                "shortages", List.of(
                        m("rule_id", "INV-001", "item_id", "MCU", "plan_id", "PP-05",
                                "plan_date", "2026-11-25", "projected_stock", -100, "threshold", 0, "qty", 100),
                        m("rule_id", "INV-001", "item_id", "MCU", "plan_id", "PP-04",
                                "plan_date", "2026-11-26", "projected_stock", -100, "threshold", 0, "qty", 100)),
                "warnings", List.of(),   // INV-002: 0 ≤ P(t) < 안전재고
                "excesses", List.of(),   // INV-003: 고가 부품만
                // agent가 이 주소로 rag-mcp Resource를 읽어 근거로 인용한다
                "evidence_uri", "doc://ECN-2026-014/3.2");
    }

    /**
     * compare_options — 결품 해결 대안 비교 (contracts.md 2.3).
     *
     * <p>대안마다 {@code tool}·{@code args}를 그대로 담는다. 모델이 고른 대안을 그대로 승인 카드에
     * 올리고 그대로 실행하므로, 모델이 인자를 다시 만들면서 숫자가 틀어질 여지가 없다.
     * 실제 구현에서 added_cost·risk는 supplier_metric을 읽어 채운다 (decisions.md 2절).
     */
    @PostMapping("/analysis/options")
    Map<String, Object> compareOptions(@RequestBody(required = false) Map<String, Object> body) {
        return m("policy_version", "v0.1",
                "options", List.of(
                        m("option_id", "OPT-1", "type", "SPLIT_PO",
                                "summary", "PO-2609-001을 400/100으로 나눠 100개를 11/25에 받는다",
                                "tool", "split_po",
                                "args", m("po_id", "PO-2609-001", "split_qty", 100, "new_due_date", "2026-11-25"),
                                "added_cost", 350_000,   // 긴급 300,000 + 분할 배송 50,000 (decisions.md 3.5)
                                "risk", "LOW", "feasible", true, "side_effects", List.of(),
                                "evidence", List.of("doc://SUPPLY-2026-003/5.1")),
                        m("option_id", "OPT-2", "type", "DUE_DATE_CHANGE",
                                "summary", "PO-2609-001 500개 전체를 11/25로 당긴다",
                                "tool", "request_due_date_change",
                                "args", m("po_id", "PO-2609-001", "new_due_date", "2026-11-25"),
                                "added_cost", 1_500_000,  // 5일 × 300,000원/일 (expedite_cost_per_day)
                                "risk", "MID", "feasible", true, "side_effects", List.of(),
                                "evidence", List.of("doc://SUPPLY-2026-003/4.2"))),
                "recommended_option_id", "OPT-1");
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
     * MOQ는 다시 보지 않는다 (3.4). 새 발주 번호는 backend가 만든다.
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
        return m("original", m("po_id", id, "qty", qty - splitQty, "due_date", po.get("due_date"), "status", "OPEN"),
                "new", m("po_id", "PO-2609-012", "qty", splitQty, "due_date", newDue.toString(),
                        "status", "OPEN", "parent_po_id", id));
    }

    /**
     * 변경 도구 공통 앞단 검사: 발주가 있나 → 승인 ID가 있나 → OPEN인가.
     *
     * <p>승인을 OPEN보다 먼저 보는 이유: 승인 없는 호출은 발주 상태와 상관없이 성립하지 않는다.
     * 지금은 approval_id가 <b>있는지만</b> 본다. 승인 API가 생기면 APPROVED인지, 도구·인자가
     * 승인 카드와 같은지까지 본다 (contracts.md 2.7).
     */
    private Map<String, Object> openPoWithApproval(String id, Map<String, Object> body) {
        Map<String, Object> po = find(POS, id, "발주");
        if (!(body.get("approval_id") instanceof String s) || s.isBlank())
            throw new ApiError("APPROVAL_REQUIRED", "승인 없이 발주를 바꿀 수 없습니다.",
                    "변경 도구는 승인 후에만 부를 수 있습니다. 승인 요청을 먼저 올리고 받은 approval_id를 넣으세요.");
        if (!"OPEN".equals(po.get("status")))
            throw new ApiError("PO_NOT_OPEN", "열려 있는 발주가 아닙니다.",
                    "현재 상태는 " + po.get("status") + "입니다. OPEN 발주만 바꿀 수 있습니다.");
        return po;
    }

    /** LT-001: 새 입고일 ≥ 오늘 + 리드타임. retry_hint에 가장 빠른 가능일을 넣어 모델이 그 날짜로 다시 부르게 한다. */
    private static LocalDate checkLeadTime(Map<String, Object> po, Object newDueDate) {
        LocalDate earliest = TODAY.plusDays((int) ITEMS.get(po.get("item_id")).get("lead_time_days"));
        LocalDate newDue;
        try {
            newDue = LocalDate.parse(String.valueOf(newDueDate));
        } catch (Exception e) {
            throw new ApiError("LEADTIME_INFEASIBLE", "new_due_date를 읽지 못했습니다.",
                    "YYYY-MM-DD 형식으로 주세요. 가장 빠른 가능일은 " + earliest + "입니다.");
        }
        if (newDue.isBefore(earliest))
            throw new ApiError("LEADTIME_INFEASIBLE", "리드타임상 그 날짜에 받을 수 없습니다.",
                    "가장 빠른 가능일은 " + earliest + "입니다. 그 이후 날짜로 다시 요청하세요.");
        return newDue;
    }

    // ------------------------------------------------------------------
    // 오류 응답 (contracts.md 2.5)
    // ------------------------------------------------------------------

    /**
     * 업무 오류. 4xx + {@code {error, message, retry_hint}}로 나간다.
     * retry_hint는 비워 두지 않는다 — 모델이 이걸 읽고 인자를 고쳐 스스로 다시 부른다.
     *
     * <p>판정(결품·경고·과잉)은 오류가 아니다. 호출 자체가 성립하지 않을 때만 쓴다.
     */
    static class ApiError extends RuntimeException {
        final String code, retryHint;
        final HttpStatus status;

        ApiError(String code, String message, String retryHint) {
            super(message);
            this.code = code;
            this.retryHint = retryHint;
            // 없는 것은 404, 상태가 안 맞는 것은 409, 나머지는 요청이 틀린 것이라 400
            this.status = switch (code) {
                case "NOT_FOUND" -> HttpStatus.NOT_FOUND;
                case "PO_NOT_OPEN" -> HttpStatus.CONFLICT;
                case "APPROVAL_REQUIRED" -> HttpStatus.FORBIDDEN;
                default -> HttpStatus.BAD_REQUEST;
            };
        }
    }

    @ExceptionHandler(ApiError.class)
    ResponseEntity<Map<String, Object>> onApiError(ApiError e) {
        return ResponseEntity.status(e.status).body(m("error", e.code, "message", e.getMessage(), "retry_hint", e.retryHint));
    }

    // ------------------------------------------------------------------
    // 작은 도우미
    // ------------------------------------------------------------------

    /** 없는 ID면 NOT_FOUND. retry_hint에 있는 ID 목록을 넣는다 (교재 2장 find_product 패턴). */
    private static Map<String, Object> find(Map<String, Map<String, Object>> table, String id, String what) {
        Map<String, Object> row = table.get(id);
        if (row == null)
            throw new ApiError("NOT_FOUND", what + " " + id + "을(를) 찾을 수 없습니다.",
                    "있는 ID: " + String.join(", ", table.keySet()));
        return row;
    }

    /** 순서를 지키는 Map. Map.of는 순서가 없고 null 값과 10쌍 초과를 못 받는다. */
    static Map<String, Object> m(Object... kv) {
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) map.put((String) kv[i], kv[i + 1]);
        return map;
    }

    @SafeVarargs
    private static Map<String, Map<String, Object>> byId(String key, Map<String, Object>... rows) {
        Map<String, Map<String, Object>> map = new LinkedHashMap<>();
        for (Map<String, Object> r : rows) map.put((String) r.get(key), r);
        return map;
    }

    /** 값이 null이면 거르지 않는다 (선택 조건). */
    private static Predicate<Map<String, Object>> eq(String key, String value) {
        return r -> value == null || value.equals(r.get(key));
    }

    /** YYYY-MM-DD는 문자열 비교가 곧 날짜 비교라 parse하지 않는다. 양끝 포함. */
    private static Predicate<Map<String, Object>> between(String key, String from, String to) {
        return r -> {
            String d = (String) r.get(key);
            return (from == null || d.compareTo(from) >= 0) && (to == null || d.compareTo(to) <= 0);
        };
    }
}
