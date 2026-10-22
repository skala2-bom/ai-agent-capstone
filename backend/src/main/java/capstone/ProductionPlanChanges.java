package capstone;

import java.time.LocalDate;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import static capstone.InternalApi.m;
import static capstone.TaskBatchExecution.*;

/** No independent transaction: the caller commits plans, POs and audit records together. */
final class ProductionPlanChanges {
    final JdbcTemplate db;
    final Approvals approvals;
    ProductionPlanChanges(JdbcTemplate db, Approvals approvals) { this.db = db; this.approvals = approvals; }

    List<Map<String, Object>> normalize(Object input) {
        if (!(input instanceof List<?> raw) || raw.isEmpty() || raw.size() > 100)
            throw conflict("생산계획 변경을 1~100개 입력하세요.");
        List<Map<String, Object>> result = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Object value : raw) {
            var op = map(value);
            String name = Objects.toString(op.get("op"), ""), id = Objects.toString(op.get("plan_id"), "");
            if (!Set.of("reschedule_plan", "change_plan_qty").contains(name) || id.isBlank()
                    || !seen.add(name + "|" + id)) throw conflict("계획별 날짜·수량 변경을 하나씩만 입력하세요.");
            String field = name.equals("reschedule_plan") ? "new_date" : "new_qty";
            if (!op.keySet().equals(Set.of("op", "plan_id", field))) throw conflict("생산계획의 날짜·수량만 변경할 수 있습니다.");
            Object next;
            if (field.equals("new_qty")) {
                if (!(op.get(field) instanceof Number n) || n.doubleValue() != n.longValue() || n.longValue() < 1 || n.longValue() > Integer.MAX_VALUE)
                    throw conflict("생산 수량은 1 이상의 정수여야 합니다.");
                next = ((Number) op.get(field)).intValue();
            } else {
                try {
                    LocalDate date = LocalDate.parse(String.valueOf(op.get(field)));
                    if (!date.isAfter(InternalApi.TODAY)) throw new IllegalArgumentException();
                    next = date.toString();
                } catch (Exception e) { throw conflict("생산일은 기준일 이후의 YYYY-MM-DD 날짜여야 합니다."); }
            }
            result.add(m("op", name, "plan_id", id, field, next));
        }
        result.sort(Comparator.comparing(x -> x.get("plan_id") + "|" + x.get("op")));
        return result;
    }

    List<Map<String, Object>> preview(List<Map<String, Object>> ops) {
        Map<String, Map<String, Object>> rows = new TreeMap<>();
        for (var op : ops) {
            String id = (String) op.get("plan_id");
            if (!rows.containsKey(id)) {
                var found = db.queryForList("select plan_id, product_id, plan_date::text as plan_date, qty, status from production_plan where plan_id=?", id);
                if (found.isEmpty() || !"PLANNED".equals(found.getFirst().get("status")))
                    throw conflict("진행 가능한 생산계획이 아닙니다: " + id);
                var before = found.getFirst();
                rows.put(id, m("planId", id, "before", before, "after", new LinkedHashMap<>(before)));
            }
            var after = map(rows.get(id).get("after"));
            if ("reschedule_plan".equals(op.get("op"))) after.put("plan_date", op.get("new_date"));
            else after.put("qty", op.get("new_qty"));
        }
        return new ArrayList<>(rows.values());
    }

    List<Map<String, Object>> apply(List<Map<String, Object>> ops, String reason, String actor) {
        var changes = preview(ops);
        for (var change : changes) {
            var before = map(change.get("before")); var after = map(change.get("after"));
            if (before.equals(after)) continue;
            db.update("update production_plan set plan_date=?::date,qty=? where plan_id=?", after.get("plan_date"), after.get("qty"), change.get("planId"));
            approvals.history("PRODUCTION_PLAN", (String) change.get("planId"), "coordination_plan_change", before, after, reason, actor, null);
        }
        return changes;
    }
}
