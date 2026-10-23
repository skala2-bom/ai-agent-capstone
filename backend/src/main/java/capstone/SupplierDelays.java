package capstone;

import java.time.LocalDate;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import static capstone.InternalApi.m;
import static capstone.TaskBatchExecution.*;

/** Shared receipt fact/audit write. Caller owns the transaction and user confirmation. */
class SupplierDelays {
    final JdbcTemplate db;
    final Approvals approvals;
    SupplierDelays(JdbcTemplate db, Approvals approvals) { this.db = db; this.approvals = approvals; }

    Map<String, Object> current(String id) {
        var rows = db.queryForList("select po_id,item_id,qty,due_date::text as due_date,status from purchase_order where po_id=? for update", id);
        if (rows.isEmpty()) throw new ApiError("NOT_FOUND", "발주 " + id + "을(를) 찾을 수 없습니다.", "발주 번호를 확인하세요.");
        if (!"OPEN".equals(rows.getFirst().get("status"))) throw new ApiError("PO_NOT_OPEN", "열려 있는 발주가 아닙니다.", "최신 정보로 다시 분석하세요.");
        return rows.getFirst();
    }

    Map<String, Object> record(String id, LocalDate due, String reason, String actor) {
        var before = current(id);
        db.update("update purchase_order set due_date=? where po_id=?", due, id);
        var after = new LinkedHashMap<>(before); after.put("due_date", due.toString());
        approvals.history("PURCHASE_ORDER", id, "supplier_delay", before, after, reason, actor, null);
        return m("poId", id, "before", before, "after", after, "reason", reason);
    }

    List<Map<String, Object>> pending(Map<String, Object> result, CoordinationStore store) {
        var metadata = map(result.get("receipt_delay"));
        var changes = maps(metadata.get("changes"));
        var ops = maps(result.get("whatif"));
        if (!Boolean.TRUE.equals(metadata.get("reported")) || changes.isEmpty() || changes.size() > 10 || changes.size() != ops.size())
            throw conflict("확정된 입고 지연을 발주 번호·날짜와 함께 분석한 뒤 전송하세요. 가정 분석은 저장할 수 없습니다.");
        List<Map<String, Object>> facts = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (var change : changes) {
            String id = Objects.toString(change.get("poId"), "");
            if (!seen.add(id)) throw conflict("같은 발주가 중복되었습니다. 다시 분석하세요.");
            var before = current(id);
            if (!store.same(before, change.get("before"))) throw conflict("입고 정보가 분석 이후 바뀌었습니다. 최신 정보로 다시 분석하세요.");
            LocalDate due;
            try { due = LocalDate.parse(Objects.toString(map(change.get("after")).get("due_date"))); }
            catch (RuntimeException e) { throw conflict("새 입고일이 올바르지 않습니다."); }
            if (!due.isAfter(LocalDate.parse((String) before.get("due_date"))) || !due.isAfter(InternalApi.TODAY))
                throw conflict("기존 입고일 이후의 확정 지연만 전송할 수 있습니다.");
            var after = new LinkedHashMap<>(before); after.put("due_date", due.toString());
            var expectedOp = m("op", "request_due_date_change", "po_id", id, "new_due_date", due.toString());
            if (!store.same(after, change.get("after")) || ops.stream().filter(o -> store.same(o, expectedOp)).count() != 1)
                throw conflict("분석한 입고 조건과 전송할 조건이 다릅니다. 다시 분석하세요.");
            facts.add(m("poId", id, "itemName", db.queryForObject("select name from item where item_id=?", String.class, before.get("item_id")),
                    "before", before, "after", after));
        }
        return facts;
    }
}
