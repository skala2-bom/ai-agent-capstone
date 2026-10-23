package capstone;

import java.time.Instant;
import java.util.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.oauth2.jwt.Jwt;
import static capstone.InternalApi.m;
import static capstone.TaskBatchExecution.*;

/** A request is anchored to its owner's analysis task. No new tables or task-status values. */
final class CoordinationStore {
    static final String STATE = "COORDINATION_REQUEST", LINK = "COORDINATION_LINK";
    static final Set<String> TERMINAL = Set.of("COMPLETED", "REJECTED", "CANCELLED");
    final JdbcTemplate db;
    final TaskEventLog events;
    CoordinationStore(JdbcTemplate db, ObjectMapper json) { this.db = db; events = new TaskEventLog(db, json); }
    Map<String, Object> get(String id) {
        var r = events.latest(id, STATE);
        if (r.isEmpty()) throw new ApiError("NOT_FOUND", "부서 요청이 없습니다.", "요청함을 다시 불러오세요.");
        return r;
    }
    Map<String, Object> linked(String taskId) {
        var link = events.latest(taskId, LINK);
        return link.isEmpty() ? new LinkedHashMap<>() : get((String) link.get("coordinationId"));
    }
    void lock(String id) { lock(id, new HashSet<>()); }
    private void lock(String id, Set<String> seen) {
        if (!seen.add(id) || seen.size() > 20) throw conflict("연결 요청 관계를 확인할 수 없습니다.");
        var r = events.latest(id, STATE);
        String parent = Objects.toString(r.get("parentRequestId"), "");
        if (!parent.isBlank()) lock(parent, seen);
        events.lock(id);
    }
    /** Always lock request before the linked task, including approval registration and execution. */
    Map<String, Object> lockLinked(String taskId) {
        var link = events.latest(taskId, LINK);
        if (link.isEmpty()) return new LinkedHashMap<>();
        String id = (String) link.get("coordinationId"); lock(id);
        return get(id);
    }
    static void active(Map<String, Object> r) {
        if (!r.isEmpty() && TERMINAL.contains(r.get("status"))) throw conflict("이미 종료된 부서 요청입니다: " + r.get("status"));
    }
    static void visible(Map<String, Object> r, Jwt user) {
        String role = user.getClaimAsString("role");
        if (user.getSubject().equals(r.get("requesterId")) || role.equals(r.get("recipientRole"))
                || (role.equals("APPROVER") && Boolean.TRUE.equals(r.get("managerVisible")))) return;
        throw new ApiError("ROLE_NOT_ALLOWED", "전달받은 부서 요청만 볼 수 있습니다.", "본인의 요청함을 확인하세요.");
    }
    static void assigned(Map<String, Object> r, Jwt user) {
        visible(r, user); active(r);
        if (!user.getSubject().equals(r.get("assigneeId")) || !user.getClaimAsString("role").equals(r.get("recipientRole")))
            throw new ApiError("ROLE_NOT_ALLOWED", "이 요청의 담당자만 처리할 수 있습니다.", "먼저 담당 접수를 하세요.");
    }
    static void revision(Map<String, Object> r, Object expected) {
        if (number(expected) != number(r.get("revision"))) throw conflict("요청 내용이나 담당 상태가 바뀌었습니다. 다시 확인하세요.");
    }
    Map<String, Object> save(Map<String, Object> current, Map<String, Object> changes) {
        var r = new LinkedHashMap<>(current); r.putAll(changes);
        r.put("type", STATE); r.put("revision", number(current.get("revision")) + 1);
        r.put("updatedAt", Instant.now().toString());
        return events.append((String) r.get("id"), r);
    }
    String name(String id) { return db.queryForObject("select name from app_user where user_id=?", String.class, id); }
    boolean same(Object a, Object b) {
        // JDBC/JSON round trips may use Long vs Integer for the same integral value.
        try { return events.json.readTree(events.write(a)).equals(events.json.readTree(events.write(b))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    List<String> scope(Map<String, Object> impact) {
        Set<String> scope = new LinkedHashSet<>();
        for (var x : maps(impact.get("shortages"))) if (!Boolean.TRUE.equals(x.get("orderable"))) scope.add("INV-001|" + x.get("item_id"));
        for (var x : maps(impact.get("excesses"))) scope.add("INV-003|" + x.get("item_id"));
        return new ArrayList<>(scope);
    }
    Map<String, Object> remaining(Map<String, Object> impact, List<String> scope) {
        return m("policy_version", impact.get("policy_version"),
                "shortages", maps(impact.get("shortages")).stream().filter(x -> !Boolean.TRUE.equals(x.get("orderable")) && scope.contains("INV-001|" + x.get("item_id"))).toList(),
                "excesses", maps(impact.get("excesses")).stream().filter(x -> scope.contains("INV-003|" + x.get("item_id"))).toList(), "warnings", List.of());
    }
    boolean improves(Map<String, Object> before, Map<String, Object> after, List<String> scope) {
        for (String key : scope) {
            String field = key.startsWith("INV-001|") ? "shortages" : "excesses";
            java.util.function.ToLongFunction<Map<String, Object>> quantity = x -> field.equals("shortages") ? Math.abs(number(x.get("projected_stock"))) : number(x.get("qty"));
            long a = maps(remaining(before, List.of(key)).get(field)).stream().mapToLong(quantity).sum();
            long b = maps(remaining(after, List.of(key)).get(field)).stream().mapToLong(quantity).sum();
            if (b < a) return true;
        }
        return false;
    }
    void requireSavedPlans(Map<String, Object> r) {
        for (var p : maps(r.get("appliedPlanChanges"))) {
            var rows = db.queryForList("select plan_id,product_id,plan_date::text as plan_date,qty,status from production_plan where plan_id=?", p.get("planId"));
            if (rows.isEmpty() || !same(rows.getFirst(), p.get("after")))
                throw conflict("먼저 저장한 생산계획이 변경됐습니다. 현재 계획을 다시 검토하세요.");
        }
    }
    void requireSolution(String taskId, Map<String, Object> r) {
        active(r);
        if (!r.isEmpty() && (!"PLAN_TO_BUY".equals(r.get("direction")) || !taskId.equals(r.get("solutionTaskId"))))
            throw conflict("이 작업은 발주 실행용 해결 작업이 아닙니다. 연결된 부서 요청을 확인하세요.");
    }
    void submitted(String taskId) {
        var r = lockLinked(taskId);
        if (!r.isEmpty()) {
            requireSolution(taskId, r);
            if (!Boolean.TRUE.equals(r.get("managerVisible"))) save(r, m("managerVisible", true, "status", "WAITING_REVIEW"));
        }
    }
    Map<String, Object> finish(Map<String, Object> r, String status, String reason, String actor, Object result) {
        active(r);
        var saved = save(r, m("status", status, "decisionReason", reason, "decidedBy", actor,
                "decidedName", name(actor), "decidedAt", Instant.now().toString(), "outcome", result));
        String parentId = Objects.toString(r.get("parentRequestId"), "");
        if (!parentId.isBlank()) {
            var parent = get(parentId);
            if ("WAITING_LINKED".equals(parent.get("status")) && r.get("id").equals(parent.get("linkedRequestId"))) {
                var combined = new LinkedHashMap<>(map(result));
                if (!maps(parent.get("appliedPlanChanges")).isEmpty()) combined.put("planChanges", parent.get("appliedPlanChanges"));
                if ("COMPLETED".equals(status)) save(parent, m("status", "COMPLETED", "decidedBy", actor,
                        "decidedName", name(actor), "decidedAt", Instant.now().toString(), "outcome", combined,
                        "decisionReason", maps(parent.get("appliedPlanChanges")).isEmpty() ? "연결된 구매 요청에서 발주와 생산계획 변경을 함께 완료했습니다." : "생산계획을 먼저 저장한 뒤, 연결된 구매 요청에서 남은 문제를 해결했습니다."));
                else save(parent, m("status", maps(parent.get("appliedPlanChanges")).isEmpty() ? "IN_PROGRESS" : "PARTIALLY_COMPLETED", "linkedOutcome", m("status", status, "reason", reason)));
            }
        }
        return saved;
    }
    Map<String, Object> view(Map<String, Object> source, Jwt user) {
        visible(source, user);
        var r = new LinkedHashMap<>(source);
        r.remove("fingerprint"); r.remove("creationCommand");
        var reads = db.queryForList("select payload::text from task_event where task_id=? and type='COORDINATION_READ' and payload->>'userId'=? order by seq desc limit 1",
                String.class, r.get("id"), user.getSubject());
        long readRevision = reads.isEmpty() ? -1 : number(events.read(reads.getFirst()).get("revision"));
        r.put("unread", readRevision < number(r.get("revision")));
        r.put("needsAttention", !TERMINAL.contains(r.get("status")) && !map(r.get("linkedOutcome")).isEmpty());
        String solution = (String) r.get("solutionTaskId");
        if (solution != null) {
            var statuses = db.queryForList("select status from ai_task where task_id=?", String.class, solution);
            var batch = events.latest(solution, "TASK_BATCH_STATE");
            r.put("solutionStatus", statuses.isEmpty() ? null : statuses.getFirst());
            // Do not forward internal model text, costs or chat transcripts to the other department.
            boolean attention = statuses.contains("FAILED") || (statuses.contains("DONE") && events.latest(solution, "TASK_BATCH_RESULT").isEmpty()) || "BLOCKED".equals(batch.get("status"))
                    || db.queryForObject("select exists(select 1 from task_event where task_id=? and type='PROPOSAL_PROGRESS' and payload->'outcome'->>'code' in ('NO_ALTERNATIVE','REVIEW_FAILED','LIMIT'))", Boolean.class, solution);
            r.put("needsAttention", Boolean.TRUE.equals(r.get("needsAttention")) || (!TERMINAL.contains(r.get("status")) && attention));
        }
        return r;
    }
    Map<String, Object> agentContext(Map<String, Object> r, String mode) {
        return m("id", r.get("id"), "direction", r.get("direction"), "status", r.get("status"), "mode", mode,
                "message", r.get("message"), "planOps", r.get("planOps"), "facts", r.get("facts"),
                "requestKind", r.get("requestKind"), "issueScope", scope(map(r.get("analysis"))));
    }
}
