package capstone;

import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.*;

/**
 * 화면이 부르는 조회·변경 API (contracts.md 3.1). 로그인 필수, 금액은 {@link Masking}이 역할대로 가린다.
 *
 * <p>{@code /internal}과 <b>같은 메서드를 그대로 부른다.</b> 화면 조회와 AI 도구가 같은 숫자를 보게 하려는 것이다
 * (아키텍처 1.1 "규칙은 한 곳에"). 응답 모양도 {@code /internal}과 같다 (contracts.md 2.1 응답 예시).
 *
 * <p>{@code GET /api/items/{id}/bom}은 BOM 펼치기(explode_bom, 2차 도구)와 같이 만든다.
 */
@RestController
@RequestMapping("/api")
class ScreenApi {

    private final InternalApi internal;
    private final JdbcTemplate db;
    private final Approvals approvals;
    private final TransactionTemplate tx;
    private final Tasks tasks;

    ScreenApi(InternalApi internal, JdbcTemplate db, Approvals approvals, TransactionTemplate tx, Tasks tasks) {
        this.internal = internal;
        this.db = db;
        this.approvals = approvals;
        this.tx = tx;
        this.tasks = tasks;
    }

    @GetMapping("/plans")
    Map<String, Object> plans(@RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
                              @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to) {
        return internal.listPlans(from, to);
    }

    @GetMapping("/inventory")
    Map<String, Object> inventory(@RequestParam(required = false) String type,
                                  @RequestParam(name = "item_id", required = false) String itemId) {
        return internal.getInventory(type, itemId);
    }

    @GetMapping("/purchase-orders")
    Map<String, Object> purchaseOrders(@RequestParam(defaultValue = "OPEN") String status,
                                       @RequestParam(name = "item_id", required = false) String itemId,
                                       @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
                                       @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to) {
        return internal.listReceipts(status, itemId, from, to);
    }

    /** GET /api/items/{id}/bom — BOM 트리 (contracts.md 3.1). explode_bom과 같은 계산 */
    @GetMapping("/items/{id}/bom")
    Map<String, Object> bom(@PathVariable String id,
                            @RequestParam(name = "on_date", required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate onDate) {
        return internal.explodeBom(id, onDate);
    }

    /** GET /api/suppliers/metrics — 협력사 평가지표 목록 (화면 평가지표 표) */
    @GetMapping("/suppliers/metrics")
    Map<String, Object> metrics(@RequestParam(name = "item_id", required = false) String itemId) {
        return internal.supplierMetrics(itemId);
    }

    /**
     * POST /api/analysis/impact {@code {whatif}} — 화면의 영향 분석 (업무 현황의 "신규 위험", 생산계획 "영향 미리보기").
     * AI 도구 analyze_impact와 <b>같은 계산</b>이다. whatif를 비우면 지금 DB 상태 그대로 판정한다.
     * 가정이라 DB는 바뀌지 않는다. 금액은 로그인한 사람의 역할대로 가려진다.
     */
    @PostMapping("/analysis/impact")
    Map<String, Object> impact(@RequestBody(required = false) Map<String, Object> body) {
        return internal.analyzeImpact(body);
    }

    /**
     * POST /api/analysis/options {@code {whatif, issues}} — 화면의 해결안 비교. AI 도구 compare_options와 같은 계산.
     * 화면은 지금 DB 상태(whatif 없음)의 결품·과잉에 대한 대안을 보여 준다. 실행은 여기서 하지 않는다 —
     * AI의 발주 변경은 승인 요청 → 팀장 승인을 거친다. 확정된 입고 정보의 수동 입력은 별도 화면 API다.
     */
    @PostMapping("/analysis/options")
    Map<String, Object> options(@RequestBody(required = false) Map<String, Object> body) {
        return internal.compareOptions(body);
    }

    // ------------------------------------------------------------------
    // 화면 직접 변경 — 사용자 권한으로 저장하고 변경 이력을 남긴다
    // 요청 모양은 프론트(frontend/src/services/api.ts)가 먼저 정한 것을 그대로 따랐다
    // ------------------------------------------------------------------

    /**
     * PUT /api/plans/{id} {@code {date?, qty?, reason?}} — 생산 일정·수량 변경 (F2, PLANNER).
     *
     * <p>승인이 없는 이유: 생산 일정은 생산관리팀의 결정이고, 발주처럼 회사 밖으로 나가는 약속이 아니다.
     * 바꾼 뒤의 영향은 AI에게 물어 본다 (SC1·SC2·SC3이 이 순서다).
     */
    @PutMapping("/plans/{id}")
    Map<String, Object> updatePlan(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "PLANNER");
        return tx.execute(t -> {
            List<Map<String, Object>> rows = db.queryForList(
                    "select plan_id, product_id, plan_date::text as plan_date, qty, status from production_plan where plan_id = ? for update", id);
            if (rows.isEmpty())
                throw new ApiError("NOT_FOUND", "생산계획 " + id + "을(를) 찾을 수 없습니다.", "GET /api/plans에서 ID를 확인하세요.");
            Map<String, Object> before = rows.getFirst();
            if (!"PLANNED".equals(before.get("status")))
                throw new ApiError("PLAN_NOT_OPEN", "이미 끝났거나 취소된 계획입니다.", "현재 상태는 " + before.get("status") + "입니다.");
            if (body.get("date") == null && body.get("qty") == null)
                throw ApiError.invalid("바꿀 값이 없습니다.", "date(YYYY-MM-DD)나 qty 중 하나 이상을 보내세요.");
            LocalDate date = body.get("date") == null ? LocalDate.parse((String) before.get("plan_date")) : notBeforeToday(body.get("date"), "date");
            int qty = body.get("qty") == null ? (int) before.get("qty") : positive(body.get("qty"));
            db.update("update production_plan set plan_date = ?, qty = ? where plan_id = ?", date, qty, id);
            Map<String, Object> after = new LinkedHashMap<>(before);
            after.put("plan_date", date.toString());
            after.put("qty", qty);
            approvals.history("PRODUCTION_PLAN", id, "update_plan", before, after, (String) body.get("reason"), user.getSubject(), null);
            return after;
        });
    }

    /** 구매·팀장이 확정된 입고 수량·예정일을 직접 반영한다. AI 실행 도구와는 별도다. */
    @PutMapping("/purchase-orders/{id}")
    Map<String, Object> updatePurchase(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "BUYER");
        if (!(body.get("reason") instanceof String reason) || reason.isBlank())
            throw ApiError.invalid("변경 사유를 입력해 주세요.", "확정된 수량·일정 변경 내용을 적어 주세요.");
        if (body.get("due_date") == null && body.get("qty") == null)
            throw ApiError.invalid("바꿀 값이 없습니다.", "due_date 또는 qty를 보내세요.");
        Map<String, Object> change = tx.execute(t -> {
            List<Map<String, Object>> rows = db.queryForList("""
                    select po_id, item_id, qty, order_date::text as order_date, due_date::text as due_date, status
                    from purchase_order where po_id = ? for update""", id);
            if (rows.isEmpty()) throw new ApiError("NOT_FOUND", "발주를 찾을 수 없습니다.", "발주 목록을 다시 불러오세요.");
            Map<String, Object> before = rows.getFirst();
            if (!"OPEN".equals(before.get("status")))
                throw new ApiError("PO_NOT_OPEN", "입고 완료·취소된 발주는 수정할 수 없습니다.", "입고 예정인 발주를 선택하세요.");
            LocalDate due = body.get("due_date") == null ? LocalDate.parse((String) before.get("due_date"))
                    : notBeforeToday(body.get("due_date"), "due_date");
            if (due.isBefore(LocalDate.parse((String) before.get("order_date"))))
                throw ApiError.invalid("입고 예정일이 발주일보다 앞입니다.", "발주일 이후의 날짜를 입력하세요.");
            int qty = body.get("qty") == null ? (int) before.get("qty") : positive(body.get("qty"));
            if (qty == (int) before.get("qty") && due.toString().equals(before.get("due_date")))
                throw ApiError.invalid("변경된 내용이 없습니다.", "수량이나 입고 예정일을 변경하세요.");
            Map<String, Object> after = new LinkedHashMap<>(before);
            after.put("qty", qty); after.put("due_date", due.toString());
            db.update("update purchase_order set qty = ?, due_date = ? where po_id = ?", qty, due, id);
            approvals.history("PURCHASE_ORDER", id, "update_purchase", before, after, reason.trim(), user.getSubject(), null);
            return InternalApi.m("before", before, "after", after);
        });
        @SuppressWarnings("unchecked") Map<String, Object> before = (Map<String, Object>) change.get("before");
        @SuppressWarnings("unchecked") Map<String, Object> after = (Map<String, Object>) change.get("after");
        String question = "[자동 분석] 입고 정보 수동 변경: %s(%s) 수량 %s → %s개, 예정일 %s → %s. 사유: %s. 결품·과잉 영향을 알려 주세요."
                .formatted(id, after.get("item_id"), before.get("qty"), after.get("qty"), before.get("due_date"), after.get("due_date"), reason.trim());
        List<Map<String, Object>> whatif = new java.util.ArrayList<>();
        if (!before.get("qty").equals(after.get("qty")))
            whatif.add(InternalApi.m("op", "change_po_qty", "po_id", id, "new_qty", after.get("qty")));
        if (!before.get("due_date").equals(after.get("due_date")))
            whatif.add(InternalApi.m("op", "request_due_date_change", "po_id", id, "new_due_date", after.get("due_date")));
        Map<String, Object> result = new LinkedHashMap<>(after);
        result.put("analysis_task_id", tasks.start(user.getSubject(), "PLANNER", question, "SYSTEM", whatif));
        return result;
    }

    /**
     * PUT /api/purchase-orders/{id}/due-date {@code {due_date, reason}} — 협력사 지연 통보 입력 (F3, BUYER·APPROVER).
     *
     * <p>승인이 없는 이유: 협력사가 늦는다는 <b>사실</b>을 적는 것이지 우리가 결정하는 변경이 아니다 (아키텍처 4.6).
     * 그래서 리드타임 검사도 하지 않는다 — 협력사가 정한 날짜다. 사유는 필수 (이력에 남는다).
     *
     * <p><b>저장한 뒤 영향 분석 작업을 자동으로 만든다</b> (trigger=SYSTEM, 아키텍처 4.6, decisions.md 6.6).
     * 생산관리팀은 일정을 바꾸지 않았으니 문제를 모른다 — 구매가 놓치면 생산 당일에야 알게 된다 (기획안 SC4).
     * 응답의 {@code analysis_task_id}로 화면이 바로 결과를 따라갈 수 있다.
     */
    @PutMapping("/purchase-orders/{id}/due-date")
    Map<String, Object> supplierDelay(@PathVariable String id, @RequestBody Map<String, Object> rawBody, @AuthenticationPrincipal Jwt user) {
        Map<String, Object> body = new LinkedHashMap<>(rawBody);
        Auth.require(user, "BUYER");
        if (!(body.get("reason") instanceof String reason) || reason.isBlank())
            throw ApiError.invalid("사유가 없습니다.", "협력사 통보 내용을 reason에 적어 주세요. 변경 이력에 남습니다.");
        Map<String, Object> po = tx.execute(t -> {
            List<Map<String, Object>> rows = db.queryForList(
                    "select po_id, item_id, qty, due_date::text as due_date, status from purchase_order where po_id = ? for update", id);
            if (rows.isEmpty())
                throw new ApiError("NOT_FOUND", "발주 " + id + "을(를) 찾을 수 없습니다.", "GET /api/purchase-orders에서 ID를 확인하세요.");
            Map<String, Object> before = rows.getFirst();
            if (!"OPEN".equals(before.get("status")))
                throw new ApiError("PO_NOT_OPEN", "열려 있는 발주가 아닙니다.", "현재 상태는 " + before.get("status") + "입니다.");
            LocalDate due = notBeforeToday(body.get("due_date"), "due_date");
            db.update("update purchase_order set due_date = ? where po_id = ?", due, id);
            Map<String, Object> after = new LinkedHashMap<>(before);
            after.put("due_date", due.toString());
            approvals.history("PURCHASE_ORDER", id, "supplier_delay", before, after, (String) body.get("reason"), user.getSubject(), null);
            body.put("_before", before.get("due_date"));
            // 응답은 바뀐 발주 한 건, 목록 조회(/api/purchase-orders)와 같은 모양
            return db.queryForList("""
                    select po_id, item_id, po.supplier_id, s.name as supplier_name, qty, unit_price, qty * unit_price as amount,
                           order_date::text as order_date, due_date::text as due_date, status, parent_po_id
                    from purchase_order po join supplier s on s.supplier_id = po.supplier_id where po_id = ?""", id).getFirst();
        });
        // 변경이 커밋된 뒤에 만든다 — agent가 바뀐 DB를 읽어야 한다
        Map<String, Object> result = new LinkedHashMap<>(po);
        result.put("analysis_task_id", startDelayAnalysis(user.getSubject(), po, body));
        return result;
    }

    /**
     * 협력사 지연 자동 분석 작업 (trigger=SYSTEM).
     *
     * <ul>
     *   <li>whatif를 backend가 채운다 — 무엇이 바뀌었는지 이미 안다. 모델이 질문에서 추측할 이유가 없다 (contracts.md 2.6).
     *       변경은 이미 DB에 들어갔으므로 같은 날짜로 다시 적용해도 결과는 같다</li>
     *   <li>역할은 <b>PLANNER</b>로 돌려 금액을 숨긴다. 작업 이력은 입력한 사람만 보며,
     *       다른 담당자는 공통 업무 현황에서 현재 데이터의 영향을 확인한다</li>
     *   <li>작업 주인은 지연을 입력한 사람이다 (이력에 누가 시작했는지 남게)</li>
     * </ul>
     */
    private String startDelayAnalysis(String userId, Map<String, Object> po, Map<String, Object> body) {
        String question = "[자동 분석] 협력사 입고 지연: %s(%s %,d개) 입고일 %s → %s. 사유: %s. 이 변경으로 생기는 결품·과잉을 알려 주세요."
                .formatted(po.get("po_id"), po.get("item_id"), (int) po.get("qty"), body.get("_before"), po.get("due_date"), body.get("reason"));
        List<Map<String, Object>> whatif = List.of(
                InternalApi.m("op", "request_due_date_change", "po_id", po.get("po_id"), "new_due_date", po.get("due_date")));
        return tasks.start(userId, "PLANNER", question, "SYSTEM", whatif);
    }

    /**
     * PUT /api/suppliers/{supplierId}/items/{itemId}/metrics — 협력사 평가지표 입력 (decisions.md 2.4, BUYER·APPROVER).
     *
     * <p>구매 담당자가 입고 검수 후 납기 준수율·품질 점수 등을 넣는다. 보낸 칸만 바꾸고, 언제·누가 넣었는지 남긴다.
     * <b>AI 도구로는 열지 않는다</b> — 평가지표는 대안 비교의 기준값이라, AI가 고칠 수 있으면 자기 추천을 통과시키려고
     * 기준을 바꾸는 길이 열린다 (채점 기준을 채점자가 고치는 셈). 그래서 이 API는 {@code /api}(사람 로그인)에만 있다.
     */
    @PutMapping("/suppliers/{supplierId}/items/{itemId}/metrics")
    Map<String, Object> updateMetric(@PathVariable String supplierId, @PathVariable String itemId,
                                     @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "BUYER");
        // 칸 이름 → 허용 범위. 칸 이름은 이 목록에서만 SQL에 들어간다
        Map<String, double[]> fields = new LinkedHashMap<>();
        fields.put("on_time_rate", new double[]{0, 100});
        fields.put("quality_score", new double[]{0, 100});
        fields.put("unit_price", new double[]{0, 1e9});
        fields.put("lead_time_days", new double[]{1, 365});
        fields.put("expedite_cost_per_day", new double[]{0, 1e9});
        fields.put("split_fee", new double[]{0, 1e9});
        fields.put("delay_penalty", new double[]{0, 1e9});
        List<String> sets = new java.util.ArrayList<>();
        List<Object> values = new java.util.ArrayList<>();
        for (var f : fields.entrySet()) {
            Object v = body.get(f.getKey());
            if (v == null) continue;
            if (!(v instanceof Number n) || n.doubleValue() < f.getValue()[0] || n.doubleValue() > f.getValue()[1])
                throw ApiError.invalid(f.getKey() + " 값이 범위를 벗어났습니다: " + v,
                        f.getKey() + "는 " + (long) f.getValue()[0] + " ~ " + (long) f.getValue()[1] + " 사이 숫자로 주세요.");
            sets.add(f.getKey() + " = ?");
            values.add(f.getKey().equals("on_time_rate") ? n.doubleValue() : (Object) n.intValue());
        }
        if (sets.isEmpty())
            throw ApiError.invalid("바꿀 값이 없습니다.", "다음 중 하나 이상을 보내세요: " + String.join(", ", fields.keySet()));
        values.add(InternalApi.TODAY);
        values.add(user.getSubject());
        values.add(supplierId);
        values.add(itemId);
        int n = db.update("update supplier_metric set " + String.join(", ", sets) + ", evaluated_at = ?, evaluated_by = ?"
                + " where supplier_id = ? and item_id = ?", values.toArray());
        if (n == 0)
            throw new ApiError("NOT_FOUND", "평가지표 " + supplierId + " × " + itemId + "을(를) 찾을 수 없습니다.",
                    "GET /api/suppliers/metrics에서 협력사·부품 조합을 확인하세요.");
        @SuppressWarnings("unchecked") List<Map<String, Object>> rows = (List<Map<String, Object>>) internal.supplierMetrics(itemId).get("metrics");
        return rows.stream().filter(r -> supplierId.equals(r.get("supplier_id"))).findFirst().orElseThrow();
    }

    private static LocalDate notBeforeToday(Object v, String key) {
        LocalDate d;
        try {
            d = LocalDate.parse(String.valueOf(v));
        } catch (Exception e) {
            throw ApiError.invalid(key + "를 읽지 못했습니다: " + v, "YYYY-MM-DD 형식으로 주세요. 예: 2026-11-25");
        }
        if (d.isBefore(InternalApi.TODAY))
            throw ApiError.invalid(key + "가 기준일(" + InternalApi.TODAY + ")보다 앞입니다.", "지난 날짜는 이미 재고에 반영돼 있습니다.");
        return d;
    }

    private static int positive(Object v) {
        if (v instanceof Number n && n.intValue() > 0 && n.doubleValue() == n.intValue()) return n.intValue();
        throw ApiError.invalid("qty는 1 이상의 정수여야 합니다: " + v, "개 단위 정수로 주세요.");
    }
}
