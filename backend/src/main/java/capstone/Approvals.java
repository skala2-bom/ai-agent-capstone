package capstone;

import java.util.*;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;

import static capstone.InternalApi.m;

/**
 * approval 모듈 — 승인 카드 만들기·대기함·결정, 실행 전 승인 확인, 변경 이력 (아키텍처 4.4·10장, contracts.md 2.7·3.3).
 *
 * <p>방어선은 하나다: <b>승인 기록이 없으면 backend가 발주를 바꾸지 않는다.</b>
 * AI가 잘못 판단하거나 프롬프트 공격을 받아도, 팀장이 승인한 카드와 도구·인자가 정확히 같아야만 실행된다.
 *
 * <pre>
 * agent ── APPROVAL_REQUIRED 이벤트 ──▶ {@link #create} → PENDING, approvalId 발급
 * 팀장  ── GET /api/approvals ────────▶ 대기함
 * 팀장  ── POST /api/ai/tasks/{id}/approval ▶ {@link #decide} → APPROVED | REJECTED → agent 재개
 * agent ── 변경 도구(approval_id) ────▶ {@link #requireApproved} → 실행 → {@link #executed} → EXECUTED
 * </pre>
 */
@RestController
class Approvals {

    /** 승인을 거쳐야 하는 도구 = business-mcp에서 destructive_hint가 붙는 도구 (contracts.md 2.1) */
    static final Set<String> CHANGE_TOOLS = Set.of("request_due_date_change", "split_po", "change_po_qty", "cancel_po");

    private final JdbcTemplate db;
    private final ObjectMapper json;

    Approvals(JdbcTemplate db, ObjectMapper json) {
        this.db = db;
        this.json = json;
    }

    // ------------------------------------------------------------------
    // 만들기 (agent 이벤트에서)
    // ------------------------------------------------------------------

    /**
     * APPROVAL_REQUIRED 이벤트로 승인 카드를 만든다.
     *
     * <p>요청자 역할이 BUYER·APPROVER가 아니면 거부한다 (아키텍처 10장). agent는 이 거부를 받으면
     * "발주 조정은 구매 담당자가 요청할 수 있습니다"로 답하고 끝낸다.
     *
     * <p><b>같은 작업·도구·인자로 PENDING 카드가 이미 있으면 그걸 돌려준다.</b> agent는 이벤트 전송이 실패하면
     * 최대 3번 다시 보낸다(agent-draft 브랜치). 그때마다 카드를 새로 만들면 팀장 대기함에 같은 카드가 쌓인다.
     *
     * @return 카드, 그리고 새로 만들었는지({@code created})
     */
    Map<String, Object> create(String taskId, String userId, String role, Map<String, Object> event) {
        if (!Auth.allows(role, "BUYER"))
            throw new ApiError("ROLE_NOT_ALLOWED", "발주 조정은 구매 담당자나 팀장만 요청할 수 있습니다.",
                    "승인 요청을 올리지 말고, 영향과 해결 대안까지만 안내한 뒤 구매 담당자에게 요청하라고 답하세요.");
        String tool = String.valueOf(event.get("tool"));
        if (!CHANGE_TOOLS.contains(tool))
            throw ApiError.invalid("승인이 필요한 도구가 아닙니다: " + tool, "tool은 " + String.join(" · ", CHANGE_TOOLS) + " 중 하나입니다.");
        if (!(event.get("args") instanceof Map<?, ?> rawArgs))
            throw ApiError.invalid("args가 없습니다.", "compare_options 대안의 args를 그대로 넣으세요.");
        Map<String, Object> args = withoutApprovalId(rawArgs);
        String argsJson = write(args);

        List<String> same = db.queryForList(
                "select approval_id from approval where task_id = ? and tool = ? and args = ?::jsonb and status in ('DRAFT', 'PENDING')",
                String.class, taskId, tool, argsJson);
        if (!same.isEmpty()) return withCreated(card(same.getFirst()), false);

        Map<String, Object> preview = preview(tool, args);
        String id = "APR-" + UUID.randomUUID().toString().substring(0, 8);
        Object amount = event.get("amount");
        db.update("""
                insert into approval (approval_id, task_id, tool, args, summary, amount, evidence, before, after, requested_by, status, problem)
                values (?, ?, ?, ?::jsonb, ?, ?, ?::jsonb, ?::jsonb, ?::jsonb, ?, ?, ?)""",
                id, taskId, tool, argsJson, event.get("summary"),
                amount instanceof Number n ? n.longValue() : null,
                write(event.get("evidence") == null ? List.of() : event.get("evidence")),
                write(preview.get("before")), write(preview.get("after")), userId,
                // 구매 담당자의 요청은 초안으로 시작한다 — 메모를 달고 직접 보낸다 (decisions 6.33). 팀장은 바로 대기함으로
                "APPROVER".equals(role) ? "PENDING" : "DRAFT",
                event.get("problem") instanceof String p ? p : null);
        return withCreated(card(id), true);
    }

    /**
     * 변경 전후 미리보기. 도구 이름과 인자만으로는 사람이 판단할 수 없어서 카드에 같이 싣는다 (contracts.md 3.3).
     * 예측일 뿐이고, 실제로 바뀐 값은 실행할 때 {@code after}에 다시 쓴다.
     */
    private Map<String, Object> preview(String tool, Map<String, Object> args) {
        String poId = String.valueOf(args.get("po_id"));
        List<Map<String, Object>> rows = db.queryForList(
                "select po_id, item_id, qty, due_date::text as due_date, status from purchase_order where po_id = ?", poId);
        if (rows.isEmpty())
            throw new ApiError("NOT_FOUND", "발주 " + poId + "을(를) 찾을 수 없습니다.", "compare_options 대안의 args를 그대로 넣으세요.");
        Map<String, Object> po = rows.getFirst();
        Map<String, Object> before = m("po_id", poId, "item_id", po.get("item_id"), "qty", po.get("qty"), "due_date", po.get("due_date"));
        Object after = switch (tool) {
            case "request_due_date_change" -> m("po_id", poId, "qty", po.get("qty"), "due_date", args.get("new_due_date"));
            case "split_po" -> List.of(
                    m("po_id", poId, "qty", (int) po.get("qty") - toInt(args.get("split_qty")), "due_date", po.get("due_date")),
                    m("po_id", "(신규)", "qty", toInt(args.get("split_qty")), "due_date", args.get("new_due_date")));
            case "change_po_qty" -> m("po_id", poId, "qty", args.get("new_qty"), "due_date", po.get("due_date"));
            default -> m("po_id", poId, "status", "CANCELLED");   // cancel_po
        };
        return m("before", before, "after", after);
    }

    // ------------------------------------------------------------------
    // 대기함·결정 (화면에서)
    // ------------------------------------------------------------------

    /**
     * GET /api/approvals?status=PENDING — 승인 대기함. status를 빼면 전부. 오래된 것부터.
     * 팀장은 전부, 다른 역할은 **자기가 요청한 카드만** — 올린 건이 어떻게 됐는지는 요청자도 봐야 한다 (decisions 6.28).
     * 거르는 건 backend다. 화면에서 거르면 API를 직접 부르는 사람에게 남의 카드가 보인다.
     */
    @GetMapping("/api/approvals")
    List<Map<String, Object>> list(@RequestParam(required = false) String status, @AuthenticationPrincipal Jwt user) {
        boolean all = Auth.allows(user.getClaimAsString("role"), "APPROVER");
        // 남의 초안(DRAFT)은 팀장에게도 안 보인다 — 요청자가 보내기 전이다 (decisions 6.33)
        return db.queryForList("""
                select approval_id from approval
                where (cast(? as text) is null or status = ?)
                  and (requested_by = ? or (? and status <> 'DRAFT'))
                order by requested_at""",
                String.class, status, status, user.getSubject(), all).stream().map(this::card).toList();
    }

    /** GET /api/approvals/{id} — 카드 하나. 팀장이 아니면 자기가 요청한 카드만 */
    @GetMapping("/api/approvals/{id}")
    Map<String, Object> get(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        Map<String, Object> c = card(id);
        boolean mine = user.getSubject().equals(c.get("requested_by"));
        if (!mine && (!Auth.allows(user.getClaimAsString("role"), "APPROVER") || "DRAFT".equals(c.get("status"))))
            throw new ApiError("ROLE_NOT_ALLOWED", "다른 사람이 요청한 승인입니다.", "자기가 요청한 승인만 볼 수 있습니다. 전체는 구매팀장 계정으로 보세요.");
        return c;
    }

    /**
     * 승인·거절. 화면 API({@code POST /api/ai/tasks/{id}/approval})가 부른다. 거절은 사유 필수.
     * 팀장이 자기가 요청한 건을 자기가 승인해도 된다 (decisions.md 1.5).
     */
    Map<String, Object> decide(String taskId, String approvalId, String decision, String reason, String approverId) {
        if (!"APPROVED".equals(decision) && !"REJECTED".equals(decision))
            throw ApiError.invalid("decision은 APPROVED 또는 REJECTED입니다: " + decision, "둘 중 하나로 다시 보내세요.");
        if ("REJECTED".equals(decision) && (reason == null || reason.isBlank()))
            throw ApiError.invalid("거절 사유가 없습니다.", "거절할 때는 reason에 사유를 적어 주세요. AI가 그 사유를 보고 다른 대안을 냅니다.");
        int n = db.update("""
                update approval set status = ?, decided_by = ?, decided_at = now(), reason = ?
                where approval_id = ? and task_id = ? and status = 'PENDING'""",
                decision, approverId, reason, approvalId, taskId);
        if (n == 0) {
            Map<String, Object> c = card(approvalId);   // 없으면 NOT_FOUND
            if (!taskId.equals(c.get("task_id")))
                throw new ApiError("NOT_FOUND", "이 작업의 승인 카드가 아닙니다: " + approvalId, "작업 " + c.get("task_id") + "의 카드입니다.");
            throw new ApiError("APPROVAL_NOT_PENDING", "이미 처리된 승인입니다.", "현재 상태는 " + c.get("status") + "입니다.");
        }
        return card(approvalId);
    }

    /**
     * 요청자의 검토 (decisions 6.33): 초안(DRAFT)에 문제 정의·메모를 달아 팀장에게 보내거나(PENDING) 보내지 않는다(WITHDRAWN).
     * 요청한 사람만 할 수 있다. 화면 API는 {@link Tasks}가 연다 — 보내지 않으면 AI에 알려야 해서.
     */
    Map<String, Object> review(String approvalId, String requester, boolean send, String problem, String memo) {
        if (send && (problem == null || problem.isBlank()))
            throw ApiError.invalid("문제 정의가 없습니다.", "무엇이 문제인지 한 줄이라도 적어 주세요. AI가 미리 채운 문장을 고쳐 써도 됩니다.");
        Map<String, Object> c = card(approvalId);   // 없으면 NOT_FOUND
        if (!requester.equals(c.get("requested_by")))
            throw new ApiError("ROLE_NOT_ALLOWED", "다른 사람이 요청한 승인입니다.", "승인 요청은 요청한 본인만 보낼 수 있습니다.");
        int n = db.update("""
                update approval set status = ?, problem = coalesce(?, problem), memo = ?
                where approval_id = ? and status = 'DRAFT'""",
                send ? "PENDING" : "WITHDRAWN", problem, memo, approvalId);
        if (n == 0)
            throw new ApiError("APPROVAL_NOT_PENDING", "이미 보낸 요청입니다.", "현재 상태는 " + c.get("status") + "입니다.");
        return card(approvalId);
    }

    // ------------------------------------------------------------------
    // 실행 (변경 도구에서, 트랜잭션 안)
    // ------------------------------------------------------------------

    /**
     * 변경 도구 실행 직전 확인. 승인이 APPROVED이고 <b>도구·인자가 카드와 정확히 같아야</b> 통과한다 (contracts.md 2.7).
     * 행을 잠가서(for update) 같은 승인으로 두 번 실행되는 것을 막는다. 호출하는 쪽 트랜잭션 안에서 불러야 한다.
     *
     * @param actual 실제 호출 인자 (경로의 po_id 포함, approval_id 제외)
     * @return 요청자 ID — 변경 이력의 changed_by
     */
    String requireApproved(Object approvalId, String tool, Map<String, Object> actual) {
        String howTo = "변경 도구는 승인 후에만 부를 수 있습니다. 승인 요청(APPROVAL_REQUIRED)을 먼저 올리고 받은 approval_id를 넣으세요.";
        if (!(approvalId instanceof String id) || id.isBlank())
            throw new ApiError("APPROVAL_REQUIRED", "승인 없이 발주를 바꿀 수 없습니다.", howTo);
        List<Map<String, Object>> rows = db.queryForList(
                "select status, tool, args::text as args, requested_by from approval where approval_id = ? for update", id);
        if (rows.isEmpty())
            throw new ApiError("APPROVAL_REQUIRED", "승인 기록이 없습니다: " + id, howTo);
        Map<String, Object> a = rows.getFirst();
        if (!"APPROVED".equals(a.get("status")))
            throw new ApiError("APPROVAL_REQUIRED", "실행할 수 있는 승인이 아닙니다 (현재 " + a.get("status") + ").",
                    "EXECUTED".equals(a.get("status")) ? "이미 실행된 승인입니다. 같은 승인으로 두 번 실행할 수 없습니다."
                            : List.of("DRAFT", "PENDING").contains(a.get("status")) ? "승인을 기다린 뒤 다시 부르세요."
                            : "거절되었거나 요청자가 보내지 않은 승인입니다. 다른 대안을 내세요.");
        Map<String, Object> approved = read((String) a.get("args"));
        if (!tool.equals(a.get("tool")) || !sameArgs(approved, actual))
            throw new ApiError("APPROVAL_REQUIRED", "승인된 내용과 다른 호출입니다.",
                    "승인된 호출은 " + a.get("tool") + " " + approved + " 입니다. 인자를 그대로 쓰거나 새로 승인을 받으세요.");
        return (String) a.get("requested_by");
    }

    /** 승인을 EXECUTED로 바꾸고 변경 이력을 쓴다. 변경과 같은 트랜잭션에서 부른다 (규칙 AUD-001) */
    void executed(String approvalId, String changedBy, String tool, String poId, Object before, Object after) {
        db.update("update approval set status = 'EXECUTED', executed_at = now(), after = ?::jsonb where approval_id = ?",
                write(after), approvalId);
        history("PURCHASE_ORDER", poId, tool, before, after, null, changedBy, approvalId);
    }

    /** 변경 이력 한 줄. 승인 없는 화면 변경(일정 변경·협력사 지연 입력)도 여기로 쓴다 */
    void history(String targetType, String targetId, String action, Object before, Object after,
                 String reason, String changedBy, String approvalId) {
        db.update("""
                insert into change_history (target_type, target_id, action, before, after, reason, changed_by, approval_id)
                values (?, ?, ?, ?::jsonb, ?::jsonb, ?, ?, ?)""",
                targetType, targetId, action, write(before), write(after), reason, changedBy, approvalId);
    }

    // ------------------------------------------------------------------
    // 작은 도우미
    // ------------------------------------------------------------------

    /** 카드 하나. JSON 칸은 객체로 풀어서 낸다. 금액은 응답 직전에 {@link Masking}이 역할대로 가린다 */
    Map<String, Object> card(String id) {
        List<Map<String, Object>> rows = db.queryForList("""
                select approval_id, task_id, tool, args::text as args, summary, problem, memo, amount, evidence::text as evidence,
                       before::text as before, after::text as after, status, requested_by, requested_at::text as requested_at,
                       decided_by, decided_at::text as decided_at, reason, executed_at::text as executed_at
                from approval where approval_id = ?""", id);
        if (rows.isEmpty()) throw new ApiError("NOT_FOUND", "승인 " + id + "을(를) 찾을 수 없습니다.", "대기함(GET /api/approvals)에서 ID를 확인하세요.");
        Map<String, Object> c = new LinkedHashMap<>(rows.getFirst());
        for (String k : List.of("args", "evidence", "before", "after"))
            if (c.get(k) != null) c.put(k, readAny((String) c.get(k)));
        return c;
    }

    /** 인자 비교. 숫자 100과 문자열 "100"은 같다고 본다 (JSON을 거치며 타입이 바뀔 수 있다). 키가 하나라도 다르면 다르다 */
    static boolean sameArgs(Map<String, Object> approved, Map<String, Object> actual) {
        if (!approved.keySet().equals(actual.keySet())) return false;
        return approved.keySet().stream().allMatch(k -> String.valueOf(approved.get(k)).equals(String.valueOf(actual.get(k))));
    }

    static Map<String, Object> withoutApprovalId(Map<?, ?> args) {
        Map<String, Object> out = new LinkedHashMap<>();
        args.forEach((k, v) -> { if (!"approval_id".equals(k)) out.put(String.valueOf(k), v); });
        return out;
    }

    private static Map<String, Object> withCreated(Map<String, Object> card, boolean created) {
        card.put("created", created);
        return card;
    }

    private static int toInt(Object v) {
        return v instanceof Number n ? n.intValue() : Integer.parseInt(String.valueOf(v));
    }

    private String write(Object o) {
        try {
            return json.writeValueAsString(o);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private Map<String, Object> read(String s) {
        try {
            return json.readValue(s, new TypeReference<>() {});
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private Object readAny(String s) {
        try {
            return json.readValue(s, Object.class);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
