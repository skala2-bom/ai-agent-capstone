package capstone;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.*;
import static capstone.InternalApi.m;

/** Review state lives in agent checkpoints; authenticated commands and atomic results use existing task_event. */
@RestController
class TaskBatchExecution {
    private final JdbcTemplate db;
    private final TaskEventLog events;
    private final Tasks tasks;
    private final Approvals approvals;
    private final InternalApi internal;
    private final ImpactAnalysis impact;
    static final Set<String> ACTIONS = Set.of("CONFIRM", "UNCONFIRM", "NEEDS_EDIT", "REPLAN", "MANUAL", "EXCLUDE", "EXECUTE", "CANCEL");

    TaskBatchExecution(JdbcTemplate db, ObjectMapper json, Tasks tasks, Approvals approvals,
                       InternalApi internal, ImpactAnalysis impact) {
        this.db = db; this.events = new TaskEventLog(db, json); this.tasks = tasks;
        this.approvals = approvals; this.internal = internal; this.impact = impact;
    }
    @GetMapping("/api/ai/tasks/{id}/batch")
    Map<String, Object> state(@PathVariable String id, @AuthenticationPrincipal Jwt user) {
        visible(id, user);
        Map<String, Object> state = events.latest(id, "TASK_BATCH_STATE");
        if (!state.isEmpty()) return state;
        return m("mode", events.latest(id, "TASK_BATCH_CONTEXT").isEmpty() ? "LEGACY" : "BATCH", "status", "PREPARING");
    }
    @PostMapping("/api/ai/tasks/{id}/batch")
    @ResponseStatus(HttpStatus.ACCEPTED)
    Map<String, Object> command(@PathVariable String id, @RequestBody Map<String, Object> body,
                                @AuthenticationPrincipal Jwt user) {
        Auth.require(user, "APPROVER");
        visible(id, user);
        String action = Objects.toString(body.get("action"), "");
        String requestId = Objects.toString(body.get("requestId"), "");
        if (!ACTIONS.contains(action) || requestId.isBlank() || requestId.length() > 100
                || !(body.get("expectedRevision") instanceof Number))
            throw ApiError.invalid("검토 요청 정보가 없습니다.", "화면을 다시 불러오세요.");
        Map<String, Object> payload = m("requestId", requestId, "action", action,
                "expectedRevision", body.get("expectedRevision"), "approvalId", body.get("approvalId"),
                "reason", Objects.toString(body.get("reason"), "").trim(), "args", body.get("args"), "actorId", user.getSubject());
        Map<String, Object> accepted = events.tx.execute(tx -> {
            events.lock(id);
            var old = events.request(id, "TASK_BATCH_COMMAND", requestId);
            if (!old.isEmpty()) {
                if (!events.json.valueToTree(old.get("command")).equals(events.json.valueToTree(payload)))
                    throw conflict("같은 요청 ID의 내용이 다릅니다.");
                return payload;
            }
            var state = events.latest(id, "TASK_BATCH_STATE");
            if (state.isEmpty() || !Objects.equals(number(state.get("revision")), number(body.get("expectedRevision"))))
                throw conflict("검토 상태가 바뀌었습니다. 최신 상태를 확인하세요.");
            if (Set.of("EXECUTING", "COMPLETED", "CANCELLED", "UNKNOWN").contains(state.get("status")))
                throw conflict("실행 중이거나 종료된 작업입니다.");
            if (!Set.of("EXECUTE", "CANCEL").contains(action)) {
                var card = approvals.card(Objects.toString(body.get("approvalId"), ""));
                if (!id.equals(card.get("task_id")) || !Set.of("PENDING", "REJECTED").contains(card.get("status")))
                    throw conflict("검토 가능한 승인 카드가 아닙니다.");
            }
            if (Set.of("REPLAN", "MANUAL").contains(action) && ((String) payload.get("reason")).isBlank())
                throw ApiError.invalid("수정 사유가 필요합니다.", "AI 재요청 또는 직접 수정 사유를 입력하세요.");
            if (((String) payload.get("reason")).length() > 4000) throw conflict("사유는 4,000자까지 입력할 수 있습니다.");
            events.append(id, m("type", "TASK_BATCH_COMMAND", "requestId", requestId, "command", payload));
            return payload;
        });
        tasks.enqueue(id, "batch", accepted);
        return m("accepted", true, "requestId", requestId);
    }
    private void visible(String id, Jwt user) {
        int n = db.queryForObject("""
                select count(*) from approval where task_id=? and
                (requested_by=? or (? and (status not in ('DRAFT','WITHDRAWN')
                  or (status='WITHDRAWN' and exists(select 1 from task_event e
                      where e.task_id=approval.task_id and e.type='TASK_BATCH_RESULT')))))""", Integer.class,
                id, user.getSubject(), "APPROVER".equals(user.getClaimAsString("role")));
        if (n == 0) throw new ApiError("ROLE_NOT_ALLOWED", "조회 가능한 승인 요청이 없습니다.", "본인의 작업 또는 전달된 승인 요청을 확인하세요.");
    }
    @GetMapping("/internal/tasks/{id}/batch/snapshot")
    Map<String, Object> snapshot(@PathVariable String id) {
        return events.tx.execute(tx -> { lockBusiness(); return m("fingerprint", fingerprint()); });
    }
    /** Rejection is authorized by a persisted manager command, never by model-supplied identity. */
    @PostMapping("/internal/tasks/{id}/batch/reject")
    Map<String, Object> reject(@PathVariable String id, @RequestBody Map<String, String> body) {
        return events.tx.execute(tx -> {
            events.lock(id);
            var cmd = authorized(id, body.get("requestId"));
            if (!Set.of("REPLAN", "MANUAL").contains(cmd.get("action"))) throw conflict("수정 요청이 아닙니다.");
            String approvalId = (String) cmd.get("approvalId");
            var card = approvals.card(approvalId);
            if (!id.equals(card.get("task_id"))) throw conflict("다른 작업의 카드입니다.");
            if ("REJECTED".equals(card.get("status"))) return card;
            return approvals.decide(id, approvalId, "REJECTED", (String) cmd.get("reason"), (String) cmd.get("actorId"));
        });
    }
    @PostMapping("/internal/tasks/{id}/batch/complete")
    Map<String, Object> complete(@PathVariable String id, @RequestBody Map<String, String> body) {
        return events.tx.execute(tx -> {
            events.lock(id);
            String requestId = body.get("requestId");
            var cmd = authorized(id, requestId);
            var old = events.request(id, "TASK_BATCH_RESULT", requestId);
            if (!old.isEmpty()) return old;
            if (!events.latest(id, "TASK_BATCH_RESULT").isEmpty()) throw conflict("이미 종료된 작업입니다.");
            String action = (String) cmd.get("action");
            if (!Set.of("EXECUTE", "CANCEL").contains(action)) throw conflict("task 실행·취소 요청이 아닙니다.");
            var state = events.latest(id, "TASK_BATCH_STATE");
            if (!requestId.equals(state.get("lastRequestId"))
                    || number(state.get("revision")) != number(cmd.get("expectedRevision")) + 1)
                throw conflict("요청 이후 검토 내용이 바뀌었습니다.");
            if ("CANCEL".equals(action)) {
                db.update("update approval set status='WITHDRAWN', reason='팀장 task 전체 취소', decided_by=?, decided_at=now() where task_id=? and status in ('DRAFT','PENDING')",
                        cmd.get("actorId"), id);
                return events.append(id, m("type", "TASK_BATCH_RESULT", "requestId", requestId, "status", "CANCELLED", "executions", List.of()));
            }
            // A short table write lock also covers concurrent manual edits, new plans/receipts, BOM and metric changes.
            // No HTTP/LLM calls occur inside this transaction. Every existing writer is covered without schema changes.
            lockBusiness();
            var context = events.latest(id, "TASK_BATCH_CONTEXT");
            if (!fingerprint().equals(context.get("fingerprint")))
                throw conflict("검토 중 업무 데이터가 변경됐습니다. 새로 분석한 뒤 승인해 주세요.");
            List<Map<String, Object>> entries = maps(state.get("entries"));
            if (entries.isEmpty() || entries.stream().anyMatch(e -> !Set.of("CONFIRMED", "EXCLUDED").contains(e.get("reviewStatus"))))
                throw conflict("아직 검토하지 않았거나 수정 중인 제안이 있습니다.");
            var selected = entries.stream().filter(e -> "CONFIRMED".equals(e.get("reviewStatus"))).toList();
            List<Map<String, Object>> cards = new ArrayList<>(), ops = new ArrayList<>();
            Set<String> poIds = new HashSet<>();
            for (var entry : selected) {
                var card = approvals.card((String) entry.get("approvalId"));
                if (!id.equals(card.get("task_id")) || !"PENDING".equals(card.get("status"))) throw conflict("승인 카드 상태가 바뀌었습니다.");
                var args = map(card.get("args"));
                if (!poIds.add((String) args.get("po_id"))) throw conflict("같은 발주에 여러 변경이 겹칩니다. 수정안을 하나로 정리하세요.");
                cards.add(card); ops.addAll(operations(card));
            }
            var whatif = maps(context.get("whatif"));
            var before = impact.analyze(whatif);
            var combined = new ArrayList<>(whatif); combined.addAll(ops);
            requireSolved(before, impact.analyze(combined), strings(context.get("scope")));
            List<Map<String, Object>> executions = new ArrayList<>();
            for (var card : cards) {
                String approvalId = (String) card.get("approval_id"), tool = (String) card.get("tool");
                Map<String, Object> args = new LinkedHashMap<>(map(card.get("args")));
                String poId = (String) args.remove("po_id"); args.put("approval_id", approvalId);
                db.update("update approval set status='APPROVED', decided_by=?, decided_at=now() where approval_id=? and status='PENDING'",
                        cmd.get("actorId"), approvalId);
                Map<String, Object> result = switch (tool) {
                    case "split_po" -> internal.splitPo(poId, args);
                    case "request_due_date_change" -> internal.changeDueDate(poId, args);
                    case "change_po_qty" -> internal.changePoQty(poId, args);
                    case "cancel_po" -> internal.cancelPo(poId, args);
                    case "update_purchase" -> internal.updatePurchaseApproved(poId, args);
                    default -> throw conflict("지원하지 않는 실행 도구입니다.");
                };
                executions.add(m("approval_id", approvalId, "tool", tool, "args", card.get("args"), "result", result));
            }
            // Reflect operations only once: saved PO changes must not be applied again as hypothetical changes.
            var remaining = whatif.stream().filter(op -> cards.stream().noneMatch(c -> overwrites(c, op))).toList();
            var after = impact.analyze(remaining);
            requireSolved(before, after, strings(context.get("scope")));
            db.update("update approval set status='WITHDRAWN', reason='task 실행에서 제외', decided_by=?, decided_at=now() where task_id=? and status='PENDING'",
                    cmd.get("actorId"), id);
            return events.append(id, m("type", "TASK_BATCH_RESULT", "requestId", requestId, "status", "COMPLETED",
                    "executions", executions, "impact", after, "remainingWhatif", remaining));
        });
    }
    private Map<String, Object> authorized(String id, String requestId) {
        var command = events.request(id, "TASK_BATCH_COMMAND", requestId);
        if (command.isEmpty()) throw conflict("팀장의 실행 요청 기록이 없습니다.");
        return map(command.get("command"));
    }
    private void lockBusiness() {
        db.execute("set local lock_timeout = '5s'");
        db.execute("lock table item, inventory, production_plan, purchase_order, bom, supplier_metric in share row exclusive mode");
    }
    private String fingerprint() {
        List<Object> state = new ArrayList<>();
        for (String table : List.of("item", "inventory", "production_plan", "purchase_order", "bom", "supplier_metric")) {
            var rows = db.queryForList("select row_to_json(t)::text from " + table + " t", String.class);
            Collections.sort(rows); state.add(List.of(table, rows));
        }
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(events.write(state).getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    static List<Map<String, Object>> operations(Map<String, Object> card) {
        var args = map(card.get("args"));
        if ("update_purchase".equals(card.get("tool"))) return List.of(
                m("op", "change_po_qty", "po_id", args.get("po_id"), "new_qty", args.get("new_qty")),
                m("op", "request_due_date_change", "po_id", args.get("po_id"), "new_due_date", args.get("new_due_date")));
        var op = new LinkedHashMap<>(args); op.put("op", card.get("tool")); return List.of(op);
    }
    static boolean overwrites(Map<String, Object> card, Map<String, Object> op) {
        if (!Objects.equals(map(card.get("args")).get("po_id"), op.get("po_id")) || op.get("po_id") == null) return false;
        String tool = (String) card.get("tool"), name = (String) op.get("op");
        return "cancel_po".equals(tool) || tool.equals(name)
                || ("split_po".equals(tool) && "change_po_qty".equals(name))
                || ("update_purchase".equals(tool) && Set.of("change_po_qty", "request_due_date_change").contains(name));
    }
    static void requireSolved(Map<String, Object> before, Map<String, Object> after, List<String> scope) {
        List<Map<String, Object>> unresolved = new ArrayList<>();
        for (var issue : maps(after.get("shortages")))
            if (scope.contains("INV-001|" + issue.get("item_id")) && !Boolean.TRUE.equals(issue.get("orderable"))) unresolved.add(issue);
        for (var issue : maps(after.get("excesses")))
            if (scope.contains("INV-003|" + issue.get("item_id"))) unresolved.add(issue);
        if (!unresolved.isEmpty()) throw conflict("task의 문제가 남아 있어 전체 실행을 보류했습니다: " + unresolved.stream().map(x -> String.valueOf(x.get("item_id"))).distinct().toList());
        for (var issue : maps(after.get("shortages"))) {
            var previous = maps(before.get("shortages")).stream().filter(x -> Objects.equals(x.get("item_id"), issue.get("item_id"))
                    && Objects.equals(x.get("plan_id"), issue.get("plan_id")) && Objects.equals(x.get("plan_date"), issue.get("plan_date"))).findFirst();
            if (previous.isEmpty() || number(issue.get("projected_stock")) < number(previous.get().get("projected_stock")))
                throw conflict("다른 생산의 결품이 생기거나 커집니다: " + issue.get("item_id"));
        }
        for (var issue : maps(after.get("excesses"))) {
            long previous = maps(before.get("excesses")).stream().filter(x -> Objects.equals(x.get("item_id"), issue.get("item_id")))
                    .mapToLong(x -> number(x.get("qty"))).max().orElse(0);
            if (number(issue.get("qty")) > previous) throw conflict("다른 품목의 과잉이 생기거나 커집니다: " + issue.get("item_id"));
        }
    }
    static ApiError conflict(String message) { return new ApiError("TASK_REVIEW_CONFLICT", message, "최신 검토 상태와 수정안을 확인하세요."); }
    static long number(Object x) { return x instanceof Number n ? n.longValue() : -1; }
    @SuppressWarnings("unchecked") static Map<String, Object> map(Object x) { return x instanceof Map<?, ?> ? (Map<String, Object>) x : Map.of(); }
    @SuppressWarnings("unchecked") static List<Map<String, Object>> maps(Object x) { return x instanceof List<?> ? (List<Map<String, Object>>) x : List.of(); }
    @SuppressWarnings("unchecked") static List<String> strings(Object x) { return x instanceof List<?> ? (List<String>) x : List.of(); }
}
