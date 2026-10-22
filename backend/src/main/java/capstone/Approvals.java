package capstone;

import java.util.*;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
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
    static final Set<String> CHANGE_TOOLS = Set.of("request_due_date_change", "split_po", "change_po_qty", "cancel_po", "update_purchase");

    private final JdbcTemplate db;
    private final ObjectMapper json;
    private final TransactionTemplate tx;

    Approvals(JdbcTemplate db, ObjectMapper json) {
        this.db = db;
        this.json = json;
        this.tx = new TransactionTemplate(new DataSourceTransactionManager(Objects.requireNonNull(db.getDataSource())));
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
        return tx.execute(s -> {
            var coordination = new CoordinationStore(db, json);
            coordination.requireSolution(taskId, coordination.lockLinked(taskId));
            lockTask(taskId);
            return createLocked(taskId, userId, role, event);
        });
    }

    private Map<String, Object> createLocked(String taskId, String userId, String role, Map<String, Object> event) {
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

        Map<String, Object> preview = preview(tool, args);
        String previous = event.get("previousApprovalId") instanceof String v && !v.isBlank() ? v : null;
        String proposal = event.get("proposalId") instanceof String v ? v : null;
        String inheritedMemo = null;
        if (previous != null) {
            Map<String, Object> parent = card(previous);
            if (!taskId.equals(parent.get("task_id")) || !userId.equals(parent.get("requested_by"))
                    || !"REJECTED".equals(parent.get("status")) || proposal == null || proposal.isBlank()
                    || !(event.get("revision") instanceof Number revision) || revision.intValue() < 2
                    || !Objects.equals(((Map<?, ?>) parent.get("before")).get("item_id"), ((Map<?, ?>) preview.get("before")).get("item_id")))
                throw ApiError.invalid("같은 작업에서 거절된 품목의 재제안만 바로 전달할 수 있습니다.", "이전 승인 ID·품목·제안 회차를 확인하세요.");
            // 기존 구매 설명/메모를 계승한다. 최초 구매 전송의 requestId는 복사하지 않는다.
            inheritedMemo = write(m("format", "task-review-v1", "taskProblem", Objects.toString(parent.get("taskProblem"), ""),
                    "personalMemo", Objects.toString(parent.get("memo"), ""), "previousApprovalId", previous, "proposalId", proposal));
            if (event.get("approvalId") instanceof String knownId) {
                // 업데이트 전에 이미 등록된 재제안 초안. 정확한 카드 ID로만 전환하며 결정된 카드는 되돌리지 않는다.
                return existingReproposal(card(knownId), taskId, userId, tool, args, previous, inheritedMemo);
            }
            for (String candidate : db.queryForList("select approval_id from approval where task_id = ?", String.class, taskId)) {
                Map<String, Object> existing = card(candidate);
                if (previous.equals(existing.get("previousApprovalId")) && proposal.equals(existing.get("proposalId")))
                    return existingReproposal(existing, taskId, userId, tool, args, previous, inheritedMemo);
            }
        } else if (event.containsKey("approvalId")) {
            throw ApiError.invalid("기존 카드 연결은 재제안 복구에만 사용합니다.", "이전 거절 카드와 제안 정보를 확인하세요.");
        }
        List<String> same = db.queryForList(
                "select approval_id from approval where task_id = ? and tool = ? and args = ?::jsonb and status in ('DRAFT', 'PENDING')",
                String.class, taskId, tool, argsJson);
        if (!same.isEmpty()) return previous == null ? withCreated(card(same.getFirst()), false)
                : existingReproposal(card(same.getFirst()), taskId, userId, tool, args, previous, inheritedMemo);

        String id = "APR-" + UUID.randomUUID().toString().substring(0, 8);
        Object amount = event.get("amount");
        if ("update_purchase".equals(tool)) {
            var quote = db.queryForMap("select p.due_date::text as due_date, m.expedite_cost_per_day from purchase_order p join supplier_metric m on m.supplier_id=p.supplier_id and m.item_id=p.item_id where p.po_id=?", args.get("po_id"));
            long days = Math.max(0, java.time.temporal.ChronoUnit.DAYS.between(java.time.LocalDate.parse(String.valueOf(args.get("new_due_date"))), java.time.LocalDate.parse((String) quote.get("due_date"))));
            amount = days * toInt(args.get("new_qty")) * ((Number) quote.get("expedite_cost_per_day")).longValue();
        }
        db.update("""
                insert into approval (approval_id, task_id, tool, args, summary, amount, evidence, before, after, requested_by, status, problem, memo)
                values (?, ?, ?, ?::jsonb, ?, ?, ?::jsonb, ?::jsonb, ?::jsonb, ?, ?, ?, ?)""",
                id, taskId, tool, argsJson, event.get("summary"),
                amount instanceof Number n ? n.longValue() : null,
                write(event.get("evidence") == null ? List.of() : event.get("evidence")),
                write(preview.get("before")), write(preview.get("after")), userId,
                // 최초 구매 요청만 초안. 검증된 거절 재제안은 구매 재검토 없이 팀장에게 간다.
                previous != null || "APPROVER".equals(role) ? "PENDING" : "DRAFT",
                event.get("problem") instanceof String p ? p : null, inheritedMemo);
        return withCreated(card(id), true);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> existingReproposal(Map<String, Object> existing, String taskId, String userId,
            String tool, Map<String, Object> args, String previous, String inheritedMemo) {
        boolean sameParent = previous.equals(existing.get("previousApprovalId"))
                || (existing.get("previousApprovalId") == null && Objects.toString(existing.get("summary"), "").contains("· 이전 요청 " + previous + "]"));
        if (!sameParent || !taskId.equals(existing.get("task_id")) || !userId.equals(existing.get("requested_by"))
                || !tool.equals(existing.get("tool")) || !sameArgs((Map<String, Object>) existing.get("args"), args))
            throw ApiError.invalid("기존 재제안 카드와 연결 정보가 다릅니다.", "같은 작업·도구·인자·이전 요청인지 확인하세요.");
        String id = (String) existing.get("approval_id");
        db.update("update approval set status = 'PENDING', memo = ? where approval_id = ? and status = 'DRAFT'", inheritedMemo, id);
        return withCreated(card(id), false);
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
            case "update_purchase" -> m("po_id", poId, "qty", args.get("new_qty"), "due_date", args.get("new_due_date"));
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
        // 초안·보내지 않음은 팀장의 목록·필터·집계에서 제외한다 (decisions 6.39)
        return db.queryForList("""
                select approval_id from approval
                where (cast(? as text) is null or status = ?)
                  and ((not ? and requested_by = ?) or (? and (status not in ('DRAFT', 'WITHDRAWN')
                    or (status='WITHDRAWN' and exists(select 1 from task_event e where e.task_id=approval.task_id and e.type='TASK_BATCH_RESULT')))))
                order by requested_at""",
                String.class, status, status, all, user.getSubject(), all).stream().map(this::card).toList();
    }

    /** GET /api/approvals/{id} — 카드 하나. 팀장이 아니면 자기가 요청한 카드만 */
    @GetMapping("/api/approvals/{id}")
    Map<String, Object> get(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        Map<String, Object> c = card(id);
        boolean mine = user.getSubject().equals(c.get("requested_by"));
        boolean finalizedBatch = "WITHDRAWN".equals(c.get("status"))
                && !new TaskEventLog(db, json).latest((String) c.get("task_id"), "TASK_BATCH_RESULT").isEmpty();
        if (!mine && (!Auth.allows(user.getClaimAsString("role"), "APPROVER")
                || "DRAFT".equals(c.get("status")) || ("WITHDRAWN".equals(c.get("status")) && !finalizedBatch)))
            throw new ApiError("ROLE_NOT_ALLOWED", "다른 사람이 요청한 승인입니다.", "자기가 요청한 승인만 볼 수 있습니다. 전체는 구매팀장 계정으로 보세요.");
        return c;
    }

    /**
     * 승인·거절. 화면 API({@code POST /api/ai/tasks/{id}/approval})가 부른다. 거절은 사유 필수.
     * 팀장이 자기가 요청한 건을 자기가 승인해도 된다 (decisions.md 1.5).
     */
    Map<String, Object> decide(String taskId, String approvalId, String decision, String reason, String approverId) {
        if ("APPROVED".equals(decision) && !new TaskEventLog(db, json).latest(taskId, "TASK_BATCH_CONTEXT").isEmpty())
            throw ApiError.invalid("개별 검토는 실행 승인이 아닙니다.", "task 전체 승인 및 실행을 사용하세요.");
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

    record TaskReview(String requestId, String reviewId, String taskProblem, Map<String, String> memos) {}

    /** 현재 작업의 초안을 한 트랜잭션으로 처리한다. AI의 품목 문제(problem)는 변경하지 않는다. */
    List<Map<String, Object>> reviewTask(String taskId, String requester, boolean send, TaskReview body) {
        if (body.requestId() == null || body.requestId().isBlank() || body.requestId().length() > 100
                || body.reviewId() == null || body.memos() == null || body.memos().isEmpty()
                || body.memos().entrySet().stream().anyMatch(e -> e.getKey() == null || e.getValue() == null))
            throw ApiError.invalid("작업 검토 정보가 없습니다.", "작업을 다시 불러온 뒤 전송하세요.");
        String problem = Objects.toString(body.taskProblem(), "").trim();
        if (send && problem.isBlank()) throw ApiError.invalid("전체 문제 설명을 입력해 주세요.", "작업 전체의 문제를 적어 주세요.");
        if (problem.length() > 4000 || body.memos().values().stream().anyMatch(v -> v.length() > 4000))
            throw ApiError.invalid("문제 설명과 메모는 각각 4,000자까지 입력할 수 있습니다.", "입력 내용을 줄여 주세요.");
        String decision = send ? "PENDING" : "WITHDRAWN";
        return tx.execute(s -> {
            var coordination = new CoordinationStore(db, json);
            coordination.requireSolution(taskId, coordination.lockLinked(taskId));
            Map<String, Object> task = lockTask(taskId);
            if (!requester.equals(task.get("user_id")))
                throw new ApiError("ROLE_NOT_ALLOWED", "작업 요청자만 검토할 수 있습니다.", "본인의 작업에서 요청하세요.");
            List<Map<String, Object>> all = db.queryForList("select approval_id from approval where task_id = ? order by requested_at, approval_id",
                    String.class, taskId).stream().map(this::card).toList();
            List<Map<String, Object>> replay = all.stream().filter(c -> body.requestId().equals(c.get("reviewRequestId"))).toList();
            if (!replay.isEmpty()) {
                Set<String> ids = new LinkedHashSet<>();
                for (Map<String, Object> c : replay) {
                    ids.add((String) c.get("approval_id"));
                    if (!decision.equals(c.get("reviewDecision")) || !body.reviewId().equals(c.get("reviewId"))
                            || !problem.equals(c.get("taskProblem")) || !Objects.equals(body.memos().get(c.get("approval_id")), c.get("memo")))
                        throw reviewConflict("이미 접수한 요청과 내용이 다릅니다.");
                }
                if (!ids.equals(body.memos().keySet())) throw reviewConflict("이미 접수한 작업의 카드 구성이 다릅니다.");
                return replay; // 응답 유실 뒤 같은 요청을 보내도 이미 실행된 카드를 되돌리지 않는다.
            }
            if (Set.of("DONE", "FAILED").contains(task.get("status"))) throw reviewConflict("이미 종료된 작업입니다.");
            Map<String, Object> ready = reviewReady(taskId);
            List<Map<String, Object>> drafts = all.stream().filter(c -> "DRAFT".equals(c.get("status"))).toList();
            Set<String> ids = new LinkedHashSet<>();
            drafts.forEach(c -> ids.add((String) c.get("approval_id")));
            if (ready == null || !body.reviewId().equals(ready.get("reviewId")) || drafts.isEmpty()
                    || !ids.equals(body.memos().keySet()) || !(ready.get("approvalIds") instanceof List<?> readyIds)
                    || !readyIds.containsAll(ids))
                throw reviewConflict("해결안을 준비 중이거나 카드 구성이 바뀌었습니다. 내용을 다시 확인해 주세요.");
            for (String id : ids) {
                String envelope = write(m("format", "task-review-v1", "taskProblem", problem,
                        "personalMemo", body.memos().get(id), "requestId", body.requestId(),
                        "reviewId", body.reviewId(), "decision", decision));
                db.update("update approval set status = ?, memo = ? where approval_id = ? and status = 'DRAFT'", decision, envelope, id);
            }
            if (send) coordination.submitted(taskId);
            return drafts.stream().map(c -> card((String) c.get("approval_id"))).toList();
        });
    }

    Map<String, Object> reviewReady(String taskId) {
        List<String> rows = db.queryForList("select payload::text from task_event where task_id = ? and type = 'TASK_REVIEW_READY' order by seq desc limit 1",
                String.class, taskId);
        return rows.isEmpty() ? null : read(rows.getFirst());
    }

    private Map<String, Object> lockTask(String taskId) {
        List<Map<String, Object>> rows = db.queryForList("select user_id, status from ai_task where task_id = ? for update", taskId);
        if (rows.isEmpty()) throw new ApiError("NOT_FOUND", "작업을 찾을 수 없습니다.", "작업 목록을 확인해 주세요.");
        return rows.getFirst();
    }

    private static ApiError reviewConflict(String message) {
        return new ApiError("APPROVAL_NOT_PENDING", message, "화면을 갱신하고 작업 전체를 다시 확인하세요. 작성 중인 메모는 유지됩니다.");
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
        // 예전 일반 문자열 메모는 그대로 둔다. 버전이 맞는 검토 봉투만 해석한다.
        if (c.get("memo") instanceof String memo) {
            try {
                if (readAny(memo) instanceof Map<?, ?> note && "task-review-v1".equals(note.get("format"))
                        && note.get("taskProblem") instanceof String && note.get("personalMemo") instanceof String) {
                    c.put("taskProblem", note.get("taskProblem"));
                    c.put("memo", note.get("personalMemo"));
                    c.put("reviewRequestId", note.get("requestId"));
                    c.put("reviewId", note.get("reviewId"));
                    c.put("reviewDecision", note.get("decision"));
                    c.put("previousApprovalId", note.get("previousApprovalId"));
                    c.put("proposalId", note.get("proposalId"));
                }
            } catch (IllegalStateException ignored) { /* 기존 메모 */ }
        }
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
