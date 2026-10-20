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

    ScreenApi(InternalApi internal, JdbcTemplate db, Approvals approvals, TransactionTemplate tx) {
        this.internal = internal;
        this.db = db;
        this.approvals = approvals;
        this.tx = tx;
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

    // ------------------------------------------------------------------
    // 화면 변경 2개 — 승인 없이 바로 바꾸고 이력만 남긴다
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

    /**
     * PUT /api/purchase-orders/{id}/due-date {@code {due_date, reason}} — 협력사 지연 통보 입력 (F3, BUYER·APPROVER).
     *
     * <p>승인이 없는 이유: 협력사가 늦는다는 <b>사실</b>을 적는 것이지 우리가 결정하는 변경이 아니다 (아키텍처 4.6).
     * 그래서 리드타임 검사도 하지 않는다 — 협력사가 정한 날짜다. 사유는 필수 (이력에 남는다).
     * 9단계: 저장 후 trigger=SYSTEM 영향 분석 작업을 자동으로 만든다 (SC4).
     */
    @PutMapping("/purchase-orders/{id}/due-date")
    Map<String, Object> supplierDelay(@PathVariable String id, @RequestBody Map<String, Object> body, @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "BUYER");
        if (!(body.get("reason") instanceof String reason) || reason.isBlank())
            throw ApiError.invalid("사유가 없습니다.", "협력사 통보 내용을 reason에 적어 주세요. 변경 이력에 남습니다.");
        return tx.execute(t -> {
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
            // 응답은 바뀐 발주 한 건, 목록 조회(/api/purchase-orders)와 같은 모양
            return db.queryForList("""
                    select po_id, item_id, supplier_id, qty, unit_price, qty * unit_price as amount,
                           order_date::text as order_date, due_date::text as due_date, status, parent_po_id
                    from purchase_order where po_id = ?""", id).getFirst();
        });
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
