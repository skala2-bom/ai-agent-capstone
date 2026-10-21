package capstone;

import java.time.LocalDate;
import java.util.*;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import static capstone.InternalApi.TODAY;
import static capstone.InternalApi.m;

/**
 * analysis 모듈 — 날짜별 예상 재고와 결품·경고·과잉 판정 (아키텍처 1.2, decisions.md 1절).
 *
 * <p>순서
 * <ol>
 *   <li>DB에서 지금 상태를 읽는다 (재고, PLANNED 생산계획, OPEN 발주, BOM)</li>
 *   <li>가정 변경(whatif)을 <b>메모리에만</b> 적용한다. DB는 건드리지 않는다</li>
 *   <li>생산계획마다 BOM을 구매 부품 단위로 펼쳐 소요를 만든다 (F-02)</li>
 *   <li>부품마다 날짜순으로 P(t) = 초기재고 + 누적입고 − 누적소요 를 굴리며 판정한다 (F-01)</li>
 * </ol>
 *
 * <p>whatif 연산 이름 중 발주 쪽은 <b>변경 도구 이름과 인자를 그대로</b> 쓴다
 * ({@code request_due_date_change}, {@code split_po} …). 그래서 agent의 verify가 compare_options의
 * {@code tool}·{@code args}를 손대지 않고 whatif로 넣어 다시 계산할 수 있다 (contracts.md 2.4).
 */
// ponytail: 부르는 순간마다 전체를 다시 읽고 다시 계산한다. 부품 28 · 계획 7 · 발주 31 규모라 수 ms. 커지면 캐시
@Component
class ImpactAnalysis {

    static final String POLICY_VERSION = "v0.1";
    /** 과잉 관찰 기간의 바닥선. N = max(리드타임, 30) (decisions.md 1.3) */
    static final int EXCESS_MIN_WINDOW = 30;

    record Part(String id, int leadTime, int safety, boolean highValue, Integer unitPrice) {}
    record Plan(String id, String product, LocalDate date, int qty) {}
    record Po(String id, String item, String supplier, int qty, LocalDate orderDate, LocalDate due) {
        Po withQty(int q) { return new Po(id, item, supplier, q, orderDate, due); }
        Po withDue(LocalDate d) { return new Po(id, item, supplier, qty, orderDate, d); }
    }
    record BomLine(String child, int qty, LocalDate from, LocalDate to) {
        boolean validOn(LocalDate d) {
            return (from == null || !d.isBefore(from)) && (to == null || !d.isAfter(to));
        }
    }
    record Use(String planId, LocalDate date, int qty) {}

    /** 계산에 필요한 전부. plans·pos는 whatif가 고치므로 매번 새로 읽는다. */
    record State(Map<String, String> types, Map<String, String> names, Map<String, Part> parts, Map<String, Integer> onHand,
                 Map<String, Plan> plans, Map<String, Po> pos, Map<String, List<BomLine>> bom) {}

    private final JdbcTemplate db;

    ImpactAnalysis(JdbcTemplate db) {
        this.db = db;
    }

    /** whatif가 null이거나 비어 있으면 지금 상태 그대로 판정한다 (seed 기준 0건). */
    Map<String, Object> analyze(List<Map<String, Object>> whatif) {
        return judge(load(whatif));
    }

    /**
     * LT-001: 발주일부터 리드타임이 지나야 받을 수 있고, 오늘·과거 입고는 없다.
     * 이미 낸 발주는 그 발주일, 새 발주는 오늘이 발주일이다 (decisions.md 7.2).
     */
    static LocalDate earliestReceipt(LocalDate orderDate, int leadTime) {
        LocalDate ready = orderDate.plusDays(leadTime);
        return ready.isAfter(TODAY) ? ready : TODAY.plusDays(1);
    }

    /** DB에서 지금 상태를 읽고 whatif를 메모리에 적용한다. */
    State load(List<Map<String, Object>> whatif) {
        Map<String, String> types = new HashMap<>(), names = new HashMap<>();
        Map<String, Part> parts = new TreeMap<>();
        db.query("""
                select i.item_id, i.type, i.lead_time_days, i.safety_stock, i.high_value, m.unit_price, i.name
                from item i left join supplier_metric m on m.supplier_id = i.supplier_id and m.item_id = i.item_id""",
                rs -> {
                    types.put(rs.getString(1), rs.getString(2));
                    names.put(rs.getString(1), rs.getString(7));
                    if ("PART".equals(rs.getString(2)))
                        parts.put(rs.getString(1), new Part(rs.getString(1), rs.getInt(3), rs.getInt(4),
                                rs.getBoolean(5), (Integer) rs.getObject(6)));
                });
        Map<String, Integer> onHand = new HashMap<>();
        db.query("select item_id, on_hand from inventory", rs -> { onHand.put(rs.getString(1), rs.getInt(2)); });
        Map<String, Plan> plans = new LinkedHashMap<>();
        db.query("select plan_id, product_id, plan_date, qty from production_plan where status = 'PLANNED' order by plan_id",
                rs -> { plans.put(rs.getString(1), new Plan(rs.getString(1), rs.getString(2), rs.getObject(3, LocalDate.class), rs.getInt(4))); });
        Map<String, Po> pos = new LinkedHashMap<>();
        db.query("select po_id, item_id, supplier_id, qty, order_date, due_date from purchase_order where status = 'OPEN' order by po_id",
                rs -> { pos.put(rs.getString(1), new Po(rs.getString(1), rs.getString(2), rs.getString(3), rs.getInt(4),
                        rs.getObject(5, LocalDate.class), rs.getObject(6, LocalDate.class))); });
        Map<String, List<BomLine>> bom = new HashMap<>();
        db.query("select parent_id, child_id, qty_per, valid_from, valid_to from bom",
                rs -> { bom.computeIfAbsent(rs.getString(1), k -> new ArrayList<>()).add(new BomLine(rs.getString(2), rs.getInt(3),
                        rs.getObject(4, LocalDate.class), rs.getObject(5, LocalDate.class))); });

        if (whatif != null) for (Map<String, Object> op : whatif) apply(op, plans, pos);
        return new State(types, names, parts, onHand, plans, pos, bom);
    }

    /** 생산계획을 펼친 부품별 소요 (F-02). 대안 비교가 "다음에 언제 쓰나"를 볼 때도 쓴다 */
    Map<String, List<Use>> uses(State st) {
        Map<String, List<Use>> uses = new HashMap<>();
        for (Plan p : st.plans().values())
            explode(p.product(), p.qty(), p, st.bom(), st.types(), uses, new ArrayList<>(List.of(p.product())));
        return uses;
    }

    /** 소요를 만들고 부품마다 판정한다. */
    Map<String, Object> judge(State st) {
        Map<String, List<Use>> uses = uses(st);
        Map<String, TreeMap<LocalDate, Integer>> receipts = new HashMap<>();
        for (Po po : st.pos().values())
            receipts.computeIfAbsent(po.item(), k -> new TreeMap<>()).merge(po.due(), po.qty(), Integer::sum);

        List<Map<String, Object>> shortages = new ArrayList<>(), warnings = new ArrayList<>(), excesses = new ArrayList<>();
        for (Part part : st.parts().values()) {
            List<Use> u = uses.getOrDefault(part.id(), new ArrayList<>());
            u.sort(Comparator.comparing(Use::date).thenComparing(Use::planId));
            TreeMap<LocalDate, Integer> in = receipts.getOrDefault(part.id(), new TreeMap<>());
            judge(part, st.onHand().getOrDefault(part.id(), 0), in, u, shortages, warnings, excesses);
        }
        Comparator<Map<String, Object>> byDate = Comparator.comparing((Map<String, Object> e) -> (String) e.get("plan_date"))
                .thenComparing(e -> (String) e.get("item_id"));
        shortages.sort(byDate);
        warnings.sort(byDate);
        excesses.sort(Comparator.comparing((Map<String, Object> e) -> (String) e.get("date")).thenComparing(e -> (String) e.get("item_id")));
        // 이름 칸: AI가 ID(CASE-SWITCH)를 제멋대로 번역하지 않고, 화면도 사람이 읽는 이름을 쓰게 (decisions 6.27)
        for (List<Map<String, Object>> list : List.of(shortages, warnings, excesses))
            for (Map<String, Object> e : list) {
                e.put("item_name", st.names().get((String) e.get("item_id")));
                Plan plan = st.plans().get((String) e.get("plan_id"));
                if (plan != null) e.put("product_name", st.names().get(plan.product()));
            }
        return m("policy_version", POLICY_VERSION, "shortages", shortages, "warnings", warnings, "excesses", excesses);
    }

    /**
     * 부품 하나의 P(t)를 날짜순으로 굴린다.
     *
     * <ul>
     *   <li>같은 날 입고는 소요보다 먼저 더한다 — "필요일 당일 입고분은 그날 쓸 수 있다" (아키텍처 1.2)</li>
     *   <li>결품·경고는 <b>소요가 있는 날</b>에 생산계획 단위로 판정한다. 그래야 plan_id를 붙일 수 있다</li>
     *   <li>같은 날 결품이 있으면 그날 경고는 버린다 (decisions.md 1.2 "결품만 보여 준다")</li>
     *   <li>다른 날의 결품을 합치지 않는다. 같은 부족분이 다음 생산까지 이어지면 그 날짜에도 다시 나온다</li>
     * </ul>
     */
    private static void judge(Part part, int start, TreeMap<LocalDate, Integer> in, List<Use> uses,
                              List<Map<String, Object>> shortages, List<Map<String, Object>> warnings,
                              List<Map<String, Object>> excesses) {
        TreeSet<LocalDate> days = new TreeSet<>(in.keySet());
        uses.forEach(u -> days.add(u.date()));
        days.add(TODAY);  // 과잉은 오늘 재고부터 본다

        int window = Math.max(part.leadTime(), EXCESS_MIN_WINDOW);
        int p = start;
        Map<String, Object> worstExcess = null;
        for (LocalDate d : days) {
            p += in.getOrDefault(d, 0);
            List<Map<String, Object>> dayWarnings = new ArrayList<>();
            boolean shortToday = false;
            for (Use u : uses) {
                if (!u.date().equals(d)) continue;
                p -= u.qty();
                if (p < 0) {
                    shortToday = true;
                    shortages.add(m("rule_id", "INV-001", "item_id", part.id(), "plan_id", u.planId(),
                            "plan_date", d.toString(), "projected_stock", p, "threshold", 0, "qty", -p,
                            // LT-001: 오늘 새로 발주하면 그날까지 오나. true면 위험이 아니라 "추가 발주" 안내다
                            "orderable", !TODAY.plusDays(part.leadTime()).isAfter(d)));
                } else if (p < part.safety()) {   // 경계는 "미만". 안전재고와 같으면 경고 안 함
                    dayWarnings.add(m("rule_id", "INV-002", "item_id", part.id(), "plan_id", u.planId(),
                            "plan_date", d.toString(), "projected_stock", p, "threshold", part.safety()));
                }
            }
            if (!shortToday) warnings.addAll(dayWarnings);

            // INV-003: 고가 부품만. 과잉량(t) = max(0, P(t) − 안전재고 − (t, t+N]의 소요)
            // P(t)는 사건이 있는 날에만 바뀌고, 그 사이에는 관찰 기간이 늘기만 해서 과잉이 줄기만 한다.
            // 그래서 사건이 있는 날만 보면 최댓값을 놓치지 않는다.
            if (part.highValue() && !d.isBefore(TODAY)) {
                LocalDate end = d.plusDays(window);
                int future = uses.stream().filter(u -> u.date().isAfter(d) && !u.date().isAfter(end)).mapToInt(Use::qty).sum();
                int excess = p - part.safety() - future;
                if (excess > 0 && (worstExcess == null || excess > (int) worstExcess.get("qty")))
                    worstExcess = m("rule_id", "INV-003", "item_id", part.id(), "date", d.toString(),
                            "projected_stock", p, "threshold", part.safety() + future, "qty", excess,
                            "window_days", window,
                            "amount", part.unitPrice() == null ? null : excess * part.unitPrice());
            }
        }
        // 과잉은 부품마다 가장 큰 날 하나만. 매일 내면 같은 재고가 수십 건으로 보인다
        if (worstExcess != null) excesses.add(worstExcess);
    }

    /**
     * BOM을 구매 부품 단위로 펼친다 (F-02). 반제품(ASSY)은 통과하며 개수를 곱해 넘긴다.
     * BOM 줄은 생산일 기준으로 유효한 것만 쓴다 — 도어센서 케이스 설계변경(12/1)이 여기서 갈린다.
     */
    private static void explode(String parent, int qty, Plan plan, Map<String, List<BomLine>> bom,
                                Map<String, String> types, Map<String, List<Use>> uses, List<String> path) {
        for (BomLine line : bom.getOrDefault(parent, List.of())) {
            if (!line.validOn(plan.date())) continue;
            int need = qty * line.qty();
            if (path.contains(line.child())) {
                path.add(line.child());
                throw new ApiError("BOM_CYCLE", "BOM에 순환이 있습니다.", "순환 경로: " + String.join(" → ", path));
            }
            if ("PART".equals(types.get(line.child()))) {
                uses.computeIfAbsent(line.child(), k -> new ArrayList<>()).add(new Use(plan.id(), plan.date(), need));
            } else {
                path.add(line.child());
                explode(line.child(), need, plan, bom, types, uses, path);
                path.removeLast();
            }
        }
    }

    // ------------------------------------------------------------------
    // BOM 조회 (explode_bom · where_used · 화면 BOM) — 영향 분석과 같은 BOM·유효일 규칙을 쓴다
    // ------------------------------------------------------------------

    /**
     * 제품(또는 반제품) 하나를 {@code on} 날짜 기준으로 펼친다.
     * {@code tree}는 단계별 줄(반제품 포함, 화면 트리용), {@code parts}는 구매 부품별 1대당 합계(F-02, 계산과 같은 단위).
     * 설계변경 유효일이 지난 줄은 빠진다 — 도어센서는 11/30까지 옛 케이스, 12/1부터 새 케이스.
     */
    Map<String, Object> bom(String productId, LocalDate on) {
        State st = load(null);
        Map<String, String> names = new HashMap<>();
        db.query("select item_id, name from item", rs -> { names.put(rs.getString(1), rs.getString(2)); });
        String type = st.types().get(productId);
        if (!"FG".equals(type) && !"ASSY".equals(type))
            throw new ApiError("NOT_FOUND", "완제품·반제품 " + productId + "을(를) 찾을 수 없습니다.",
                    "완제품 ID: " + String.join(", ", st.types().entrySet().stream().filter(e -> "FG".equals(e.getValue())).map(Map.Entry::getKey).sorted().toList()));
        List<Map<String, Object>> tree = new ArrayList<>();
        Map<String, Integer> parts = new TreeMap<>();
        walk(productId, 1, 1, on, st, names, tree, parts, new ArrayList<>(List.of(productId)));
        return m("product_id", productId, "name", names.get(productId), "on_date", on.toString(), "tree", tree,
                "parts", parts.entrySet().stream().map(e -> m("item_id", e.getKey(), "name", names.get(e.getKey()), "qty", e.getValue())).toList());
    }

    private static void walk(String parent, int multiplier, int level, LocalDate on, State st, Map<String, String> names,
                             List<Map<String, Object>> tree, Map<String, Integer> parts, List<String> path) {
        for (BomLine line : st.bom().getOrDefault(parent, List.of())) {
            if (!line.validOn(on)) continue;
            if (path.contains(line.child())) {
                path.add(line.child());
                throw new ApiError("BOM_CYCLE", "BOM에 순환이 있습니다.", "순환 경로: " + String.join(" → ", path));
            }
            int qty = multiplier * line.qty();
            String type = st.types().get(line.child());
            tree.add(m("level", level, "parent_id", parent, "item_id", line.child(), "name", names.get(line.child()),
                    "type", type, "qty_per", line.qty(), "qty", qty));
            if ("PART".equals(type)) parts.merge(line.child(), qty, Integer::sum);
            else {
                path.add(line.child());
                walk(line.child(), qty, level + 1, on, st, names, tree, parts, path);
                path.removeLast();
            }
        }
    }

    /** where_used: 이 부품이 {@code on} 날짜에 들어가는 완제품과 1대당 개수. 결품이 어느 제품에 번지는지 볼 때 */
    Map<String, Object> whereUsed(String itemId, LocalDate on) {
        State st = load(null);
        if (!st.types().containsKey(itemId))
            throw new ApiError("NOT_FOUND", "품목 " + itemId + "을(를) 찾을 수 없습니다.", "부품 ID는 get_inventory(type=PART)로 확인하세요.");
        List<Map<String, Object>> products = new ArrayList<>();
        for (var e : new TreeMap<>(st.types()).entrySet()) {
            if (!"FG".equals(e.getValue())) continue;
            Map<String, Object> b = bom(e.getKey(), on);
            @SuppressWarnings("unchecked") List<Map<String, Object>> ps = (List<Map<String, Object>>) b.get("parts");
            ps.stream().filter(p -> itemId.equals(p.get("item_id"))).findFirst()
                    .ifPresent(p -> products.add(m("product_id", e.getKey(), "name", b.get("name"), "qty_per_unit", p.get("qty"))));
        }
        return m("item_id", itemId, "on_date", on.toString(), "products", products);
    }

    // ------------------------------------------------------------------
    // 가정 변경 (whatif)
    // ------------------------------------------------------------------

    static final List<String> OPS = List.of("reschedule_plan", "change_plan_qty",
            "request_due_date_change", "split_po", "change_po_qty", "cancel_po");

    /**
     * 연산 하나를 메모리의 계획·발주에 적용한다. 검사는 "계산이 성립하는가"까지만 한다.
     * 리드타임·승인 같은 실행 가능성은 보지 않는다 — 가정을 해 보는 도구라서
     * "이렇게 하면 어떻게 되나"는 실행할 수 없는 안이어도 답할 수 있어야 한다.
     */
    private static void apply(Map<String, Object> op, Map<String, Plan> plans, Map<String, Po> pos) {
        String name = String.valueOf(op.get("op"));
        switch (name) {
            case "reschedule_plan" -> {
                Plan p = find(plans, op, "plan_id", "생산계획");
                plans.put(p.id(), new Plan(p.id(), p.product(), date(op, "new_date"), p.qty()));
            }
            case "change_plan_qty" -> {
                Plan p = find(plans, op, "plan_id", "생산계획");
                plans.put(p.id(), new Plan(p.id(), p.product(), p.date(), positive(op, "new_qty")));
            }
            case "request_due_date_change" -> {
                Po po = find(pos, op, "po_id", "발주");
                pos.put(po.id(), po.withDue(date(op, "new_due_date")));
            }
            case "split_po" -> {
                Po po = find(pos, op, "po_id", "발주");
                int q = positive(op, "split_qty");
                if (q >= po.qty())
                    throw new ApiError("INVALID_SPLIT_QTY", "분할 수량이 범위를 벗어났습니다.",
                            "split_qty는 1 ~ " + (po.qty() - 1) + " 사이여야 합니다 (원 발주 " + po.qty() + "개).");
                pos.put(po.id(), po.withQty(po.qty() - q));
                String newId = po.id() + "~split";   // 실제 번호는 변경 API가 만든다. 가정 안에서만 쓰는 이름
                pos.put(newId, new Po(newId, po.item(), po.supplier(), q, po.orderDate(), date(op, "new_due_date")));
            }
            case "change_po_qty" -> {
                Po po = find(pos, op, "po_id", "발주");
                pos.put(po.id(), po.withQty(positive(op, "new_qty")));
            }
            case "cancel_po" -> pos.remove(find(pos, op, "po_id", "발주").id());
            default -> throw ApiError.invalid("모르는 가정 변경입니다: " + name,
                    "op는 " + String.join(" · ", OPS) + " 중 하나입니다.");
        }
    }

    private static <T> T find(Map<String, T> rows, Map<String, Object> op, String key, String what) {
        Object id = op.get(key);
        if (id == null) throw ApiError.invalid(op.get("op") + "에 " + key + "가 없습니다.", key + "를 넣어 다시 요청하세요.");
        T row = rows.get(String.valueOf(id));
        if (row != null) return row;
        String needle = String.valueOf(id).toUpperCase();
        List<String> similar = rows.keySet().stream().filter(k -> k.contains(needle)).limit(10).toList();
        throw new ApiError("NOT_FOUND", what + " " + id + "을(를) 찾을 수 없습니다. 진행 중인 것만 가정에 넣을 수 있습니다.",
                "비슷한 ID: " + String.join(", ", similar.isEmpty() ? rows.keySet().stream().limit(20).toList() : similar));
    }

    /** 날짜 인자. 기준일보다 앞이면 안 된다 — 지난 날짜의 생산·입고는 이미 재고에 반영돼 있다. */
    private static LocalDate date(Map<String, Object> op, String key) {
        LocalDate d;
        try {
            d = LocalDate.parse(String.valueOf(op.get(key)));
        } catch (Exception e) {
            throw ApiError.invalid(op.get("op") + "의 " + key + "를 읽지 못했습니다.", "YYYY-MM-DD 형식으로 주세요. 예: 2026-11-25");
        }
        if (d.isBefore(TODAY))
            throw ApiError.invalid(key + "가 기준일(" + TODAY + ")보다 앞입니다.", TODAY + " 이후 날짜로 다시 요청하세요.");
        return d;
    }

    private static int positive(Map<String, Object> op, String key) {
        if (op.get(key) instanceof Number n && n.intValue() > 0 && n.doubleValue() == n.intValue()) return n.intValue();
        throw ApiError.invalid(op.get("op") + "의 " + key + "는 1 이상의 정수여야 합니다.", key + "를 개 단위 정수로 주세요.");
    }
}
