package capstone;

import java.time.Instant;
import java.util.*;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** Existing task_event storage; the task row serializes event sequence allocation across writers. */
class TaskEventLog {
    final JdbcTemplate db;
    final ObjectMapper json;
    final TransactionTemplate tx;
    TaskEventLog(JdbcTemplate db, ObjectMapper json) {
        this.db = db; this.json = json;
        tx = new TransactionTemplate(new DataSourceTransactionManager(Objects.requireNonNull(db.getDataSource())));
    }
    void lock(String taskId) {
        if (db.queryForList("select task_id from ai_task where task_id=? for update", taskId).isEmpty())
            throw new ApiError("NOT_FOUND", "작업이 없습니다.", "승인 대기함을 새로 불러오세요.");
    }
    Map<String, Object> append(String taskId, Map<String, Object> event) {
        return tx.execute(s -> {
            lock(taskId);
            Integer last = db.queryForObject("select coalesce(max(seq),0) from task_event where task_id=?", Integer.class, taskId);
            Map<String, Object> payload = new LinkedHashMap<>(event);
            payload.put("taskId", taskId); payload.put("seq", last + 1); payload.put("ts", Instant.now().toString());
            db.update("insert into task_event(task_id,seq,type,payload) values(?,?,?,?::jsonb)",
                    taskId, last + 1, payload.get("type"), write(payload));
            return payload;
        });
    }
    Map<String, Object> latest(String taskId, String type) {
        String order = "TASK_BATCH_STATE".equals(type)
                ? "coalesce((payload->>'stateVersion')::bigint,0) desc, seq desc" : "seq desc";
        var rows = db.queryForList("select payload::text from task_event where task_id=? and type=? order by " + order + " limit 1",
                String.class, taskId, type);
        return rows.isEmpty() ? new LinkedHashMap<>() : read(rows.getFirst());
    }
    Map<String, Object> request(String taskId, String type, String requestId) {
        var rows = db.queryForList("select payload::text from task_event where task_id=? and type=? and payload->>'requestId'=? order by seq desc limit 1",
                String.class, taskId, type, requestId);
        return rows.isEmpty() ? new LinkedHashMap<>() : read(rows.getFirst());
    }
    String write(Object value) {
        try { return json.writeValueAsString(value); } catch (Exception e) { throw new IllegalStateException(e); }
    }
    Map<String, Object> read(String value) {
        try { return json.readValue(value, new TypeReference<>() {}); } catch (Exception e) { throw new IllegalStateException(e); }
    }
}
