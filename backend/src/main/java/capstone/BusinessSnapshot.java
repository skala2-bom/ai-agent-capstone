package capstone;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;

/** Used inside a transaction: validation and writes see the same business state. */
final class BusinessSnapshot {
    static void lock(JdbcTemplate db) {
        db.execute("set local lock_timeout = '5s'");
        db.execute("lock table item, inventory, production_plan, purchase_order, bom, supplier_metric in share row exclusive mode");
    }
    static String fingerprint(JdbcTemplate db, TaskEventLog events) {
        List<Object> state = new ArrayList<>();
        for (String table : List.of("item", "inventory", "production_plan", "purchase_order", "bom", "supplier_metric")) {
            var rows = db.queryForList("select row_to_json(t)::text from " + table + " t", String.class);
            Collections.sort(rows); state.add(List.of(table, rows));
        }
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                .digest(events.write(state).getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
}
