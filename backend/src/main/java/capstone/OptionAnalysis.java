package capstone;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.*;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import static capstone.InternalApi.m;

/**
 * analysis 모듈 — 결품 해결 대안 만들기와 비교 (contracts.md 2.3, decisions.md 6.14).
 *
 * <p>순서
 * <ol>
 *   <li>whatif를 적용한 상태에서 결품을 구한다 ({@link ImpactAnalysis})</li>
 *   <li>발주로 못 푸는 결품(orderable=false)의 부품마다, 첫 결품일보다 늦게 들어오는 OPEN 발주로 대안을 만든다
 *       — 필요한 만큼만 떼어 당기기(SPLIT_PO), 발주 전체를 당기기(DUE_DATE_CHANGE)</li>
 *   <li>리드타임상 불가능한 대안은 버린다 (LT-001, 발주일 기준)</li>
 *   <li>남은 대안을 <b>하나씩 실제로 적용해 다시 계산</b>한다. 대상 결품이 풀리는지(resolves),
 *       다른 곳에 새 결품이 생기는지(side_effects)를 계산 결과로 채운다</li>
 *   <li>비용·위험을 supplier_metric으로 매기고, 정렬해서 첫 번째를 추천한다</li>
 * </ol>
 *
 * <p>대안의 {@code tool}·{@code args}는 변경 도구 호출 그대로다. 모델이 고른 대안을 그대로 승인 카드에
 * 올리고 그대로 실행하므로 모델이 인자를 다시 만들면서 숫자가 틀어질 여지가 없다 (contracts.md 2.3).
 */
// ponytail: 대안마다 DB를 다시 읽어 계산한다 (부품당 발주 2~3건 × 2종). 대안이 수십 개로 늘면 State를 복사해 쓴다
@Component
class OptionAnalysis {

    private static final DateTimeFormatter MD = DateTimeFormatter.ofPattern("M/d");
    /** 이보다 오래 당기면 위험을 한 단계 올린다. 2주 넘게 당기면 협력사가 생산 일정을 다시 짜야 한다고 봤다 */
    static final int LONG_PULL_DAYS = 14;
    static final List<String> RISK = List.of("LOW", "MID", "HIGH");

    private final JdbcTemplate db;
    private final ImpactAnalysis impact;

    OptionAnalysis(JdbcTemplate db, ImpactAnalysis impact) {
        this.db = db;
        this.impact = impact;
    }

    /**
     * @param whatif 결품을 만든 가정 변경. 비우면 지금 상태 (변경이 이미 DB에 반영된 경우)
     * @param items  이 부품의 결품만 푼다. 비우면 모든 결품
     */
    Map<String, Object> compare(List<Map<String, Object>> whatif, Set<String> items) {
        ImpactAnalysis.State st = impact.load(whatif);
        Map<String, Object> judged = impact.judge(st);
        List<Map<String, Object>> before = shortages(judged);
        Set<String> beforeKeys = new HashSet<>();
        before.forEach(s -> beforeKeys.add(key(s)));

        // 대상은 orderable=false 결품만이다. true는 "지금 발주하면 제때 온다"는 정상 안내라서
        // 이미 낸 발주를 건드릴 이유가 없다 (기획안 1.3 "추가 발주 필요는 실수가 아니라 정상 안내").
        // 부품별로 묶는다. 같은 부족분이 여러 날 이어져도 대안 하나로 같이 풀어야 한다
        Map<String, List<Map<String, Object>>> byItem = new LinkedHashMap<>();
        for (Map<String, Object> s : before)
            if (!(boolean) s.get("orderable") && (items.isEmpty() || items.contains((String) s.get("item_id"))))
                byItem.computeIfAbsent((String) s.get("item_id"), k -> new ArrayList<>()).add(s);

        Map<String, String> names = new HashMap<>();
        db.query("select item_id, name from item", rs -> { names.put(rs.getString(1), rs.getString(2)); });
        Map<String, Map<String, Object>> metrics = new HashMap<>();
        db.queryForList("select * from supplier_metric")
                .forEach(r -> metrics.put(r.get("supplier_id") + "|" + r.get("item_id"), r));

        List<Map<String, Object>> options = new ArrayList<>();
        for (var e : byItem.entrySet()) {
            String item = e.getKey();
            LocalDate need = LocalDate.parse((String) e.getValue().getFirst().get("plan_date"));   // 첫 결품일
            int qty = e.getValue().stream().mapToInt(s -> -(int) s.get("projected_stock")).max().orElseThrow();
            for (ImpactAnalysis.Po po : st.pos().values()) {
                if (!po.item().equals(item) || !po.due().isAfter(need)) continue;
                if (po.id().contains("~")) continue;   // whatif 안에서만 있는 가상 분할 발주. 실제로 부를 수 없다
                Map<String, Object> metric = metrics.get(po.supplier() + "|" + item);
                int lead = (int) metric.get("lead_time_days");
                if (need.isBefore(ImpactAnalysis.earliestReceipt(po.orderDate(), lead))) continue;   // LT-001 불가

                long days = ChronoUnit.DAYS.between(need, po.due());
                int perUnitDay = (int) metric.get("expedite_cost_per_day");
                String head = "%s(%s %,d개, %s 입고)".formatted(po.id(), names.get(item), po.qty(), po.due().format(MD));
                if (qty < po.qty())
                    options.add(option(whatif, beforeKeys, item, "SPLIT_PO",
                            "%s에서 %,d개만 떼어 %s에 먼저 받는다".formatted(head, qty, need.format(MD)),
                            "split_po", m("po_id", po.id(), "split_qty", qty, "new_due_date", need.toString()),
                            qty * days * perUnitDay + (int) metric.get("split_fee"), metric, days));
                options.add(option(whatif, beforeKeys, item, "DUE_DATE_CHANGE",
                        "%s 전량을 %s에 받는다".formatted(head, need.format(MD)),
                        "request_due_date_change", m("po_id", po.id(), "new_due_date", need.toString()),
                        po.qty() * days * perUnitDay, metric, days));
            }
        }

        options.addAll(excessOptions(whatif, st, judged, beforeKeys, items, names, metrics));

        // 결품 대안이 먼저 → 결품을 푸는 것 → 부작용 없는 것 → 위험 낮은 것 → 싼 것
        options.sort(Comparator
                .comparing((Map<String, Object> o) -> !"INV-001".equals(o.get("target_rule")))
                .thenComparing(o -> !(boolean) o.get("resolves"))
                .thenComparing(o -> !((List<?>) o.get("side_effects")).isEmpty())
                .thenComparing(o -> RISK.indexOf((String) o.get("risk")))
                .thenComparing(o -> (long) o.get("added_cost")));
        // 추천은 부품마다 하나: 정렬상 첫 번째이면서 결품을 다 풀고 부작용이 없는 것.
        // 그런 대안이 없는 부품은 추천하지 않는다. 모델이 "그나마 나은 것"을 정답처럼 말하지 않게
        Set<String> recommendedItems = new HashSet<>();
        for (int i = 0; i < options.size(); i++) {
            Map<String, Object> o = options.get(i);
            o.put("option_id", "OPT-" + (i + 1));
            boolean usable = (boolean) o.get("resolves") && ((List<?>) o.get("side_effects")).isEmpty();
            // 부품·문제(결품/과잉)마다 하나. 같은 부품에 결품과 과잉이 같이 있으면 각각 추천한다
            o.put("recommended", usable && recommendedItems.add(o.get("item_id") + "|" + o.get("target_rule")));
        }
        return m("policy_version", ImpactAnalysis.POLICY_VERSION,
                "options", options,
                // 결품 부품이 하나면 그 추천안, 여럿이면 첫 번째 추천안. 전부 보려면 options의 recommended=true.
                // SC2처럼 부품이 여럿이면 추천안을 모두 승인해야 결품이 다 풀린다
                "recommended_option_id", options.stream().filter(o -> (boolean) o.get("recommended"))
                        .map(o -> o.get("option_id")).findFirst().orElse(null));
    }

    /** 대안 하나를 실제로 적용해 다시 계산하고, 결과와 비용·위험을 붙인다. */
    private Map<String, Object> option(List<Map<String, Object>> whatif, Set<String> beforeKeys, String item, String type,
                                       String summary, String tool, Map<String, Object> args, long cost,
                                       Map<String, Object> metric, long days) {
        return option("INV-001", whatif, beforeKeys, item, type, summary, tool, args, cost, metric, days, null, "LOW");
    }

    /**
     * 대안 하나를 실제로 적용해 다시 계산하고, 결과와 비용·위험을 붙인다.
     *
     * @param rule INV-001(결품 대안) 또는 INV-003(과잉 대안). 푼 것의 기준이 다르다:
     *             결품은 "발주로 못 푸는 결품이 안 남음", 과잉은 "그 부품의 과잉이 안 남음".
     *             과잉 대안은 같은 부품에 새 결품이 생기는 것도 부작용이다 (줄이거나 취소하다 모자라게 되는 경우)
     */
    private Map<String, Object> option(String rule, List<Map<String, Object>> whatif, Set<String> beforeKeys, String item,
                                       String type, String summary, String tool, Map<String, Object> args, long cost,
                                       Map<String, Object> metric, long days, Long saved, String fixedRisk) {
        List<Map<String, Object>> ops = new ArrayList<>(whatif);
        Map<String, Object> op = new LinkedHashMap<>(args);
        op.put("op", tool);   // whatif 연산 이름 = 변경 도구 이름 (decisions.md 6.12)
        ops.add(op);
        Map<String, Object> result = impact.analyze(ops);
        List<Map<String, Object>> after = shortages(result);
        boolean excess = "INV-003".equals(rule);

        // 대상 부품에 발주로 못 푸는 결품이 하나도 안 남아야 푼 것이다. orderable 결품은 남아도 된다 (위 주석)
        @SuppressWarnings("unchecked") List<Map<String, Object>> excesses = (List<Map<String, Object>>) result.get("excesses");
        boolean resolves = excess
                ? excesses.stream().noneMatch(x -> item.equals(x.get("item_id")))
                : after.stream().noneMatch(s -> item.equals(s.get("item_id")) && !(boolean) s.get("orderable"));
        List<Map<String, Object>> sideEffects = after.stream()
                .filter(s -> (excess || !item.equals(s.get("item_id"))) && !beforeKeys.contains(key(s)))
                .map(s -> m("item_id", s.get("item_id"), "plan_id", s.get("plan_id"),
                        "plan_date", s.get("plan_date"), "projected_stock", s.get("projected_stock")))
                .toList();
        Map<String, Object> o = m("option_id", null, "type", type, "target_rule", rule, "item_id", item, "summary", summary,
                "tool", tool, "args", args,
                "added_cost", cost,
                "cost_level", costLevel(cost),   // 생산관리 담당자는 금액 대신 이것만 본다 (기획안 2.4)
                "risk", fixedRisk != null && excess ? fixedRisk : risk(((Number) metric.get("on_time_rate")).doubleValue(), days),
                "feasible", true,          // LT-001을 통과한 것만 남기므로 항상 true
                "resolves", resolves,      // 이 대안만으로 대상 부품의 발주로 못 푸는 결품이 모두 풀리나
                "side_effects", sideEffects,
                "recommended", false,      // 아래 compare에서 부품마다 하나만 true로 바꾼다
                "evidence", List.of());    // backend는 근거 문서를 모른다. agent가 search_docs 결과로 채운다
        if (saved != null) o.put("saved_amount", saved);   // 과잉 대안: 줄인 만큼 덜 사는 금액 (원, 마스킹 대상)
        return o;
    }

    /**
     * 과잉(INV-003) 해결 대안 — SC2 플러그 케이스, SC3 옛 케이스·코인전지 (기획안 해결 예시).
     *
     * <p>과잉이 잡힌 날까지 들어오는 OPEN 발주로 두 가지를 만든다:
     * <ul>
     *   <li><b>줄이기</b>: 과잉량만큼 수량을 줄인다 (change_po_qty). 다 줄어들면 취소(cancel_po).
     *       줄인 수량이 MOQ보다 작아지면 줄이기 대신 취소만 낸다 — 변경 API가 BELOW_MOQ로 막는다</li>
     *   <li><b>미루기</b>: 그 부품을 다음에 쓰는 생산일로 입고를 늦춘다 (request_due_date_change). 다음 사용이 없으면 안 낸다</li>
     * </ul>
     * 줄이기·취소는 더 쓸 데가 생기면 결품이 날 수 있어서, 다시 계산해 같은 부품의 새 결품도 부작용으로 본다.
     * 비용은 0으로 둔다 — 취소·감량 수수료는 계약 조건 문서(근거)에 있고 지금 평가지표에는 칸이 없다. 대신 덜 사는 금액(saved_amount)을 보여 준다.
     */
    private List<Map<String, Object>> excessOptions(List<Map<String, Object>> whatif, ImpactAnalysis.State st,
                                                    Map<String, Object> judged, Set<String> beforeKeys, Set<String> items,
                                                    Map<String, String> names, Map<String, Map<String, Object>> metrics) {
        @SuppressWarnings("unchecked") List<Map<String, Object>> excesses = (List<Map<String, Object>>) judged.get("excesses");
        Map<String, List<ImpactAnalysis.Use>> uses = impact.uses(st);
        Map<String, Integer> moq = new HashMap<>();
        db.query("select item_id, moq from item where type = 'PART'", rs -> { moq.put(rs.getString(1), rs.getInt(2)); });
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> x : excesses) {
            String item = (String) x.get("item_id");
            if (!items.isEmpty() && !items.contains(item)) continue;
            LocalDate date = LocalDate.parse((String) x.get("date"));
            int excess = (int) x.get("qty");
            Optional<LocalDate> nextUse = uses.getOrDefault(item, List.of()).stream()
                    .map(ImpactAnalysis.Use::date).filter(d -> d.isAfter(date)).min(Comparator.naturalOrder());
            for (ImpactAnalysis.Po po : st.pos().values()) {
                if (!po.item().equals(item) || po.id().contains("~") || po.due().isAfter(date)) continue;
                Map<String, Object> metric = metrics.get(po.supplier() + "|" + item);
                long unitPrice = ((Number) metric.get("unit_price")).longValue();
                String head = "%s(%s %,d개, %s 입고)".formatted(po.id(), names.get(item), po.qty(), po.due().format(MD));
                int newQty = po.qty() - excess;
                if (newQty >= moq.getOrDefault(item, 0) && newQty > 0)
                    out.add(option("INV-003", whatif, beforeKeys, item, "CHANGE_QTY",
                            "%s을 %,d개로 줄인다 (%,d개 덜 산다)".formatted(head, newQty, excess),
                            "change_po_qty", m("po_id", po.id(), "new_qty", newQty), 0, metric, 0, excess * unitPrice, "LOW"));
                else
                    out.add(option("INV-003", whatif, beforeKeys, item, "CANCEL_PO",
                            "%s을 취소한다".formatted(head),
                            "cancel_po", m("po_id", po.id()), 0, metric, 0, po.qty() * unitPrice, "LOW"));
                if (nextUse.isPresent() && nextUse.get().isAfter(po.due()))
                    out.add(option("INV-003", whatif, beforeKeys, item, "DUE_DATE_CHANGE",
                            "%s 입고를 다음 사용일 %s로 미룬다".formatted(head, nextUse.get().format(MD)),
                            "request_due_date_change", m("po_id", po.id(), "new_due_date", nextUse.get().toString()),
                            0, metric, 0, null, null));
            }
        }
        return out;
    }

    /** 30만 원 미만 LOW · 100만 원 미만 MID · 그 이상 HIGH. 기준은 decisions.md 6.15 */
    static final long COST_LOW_UNDER = 300_000, COST_MID_UNDER = 1_000_000;

    static String costLevel(long cost) {
        return cost < COST_LOW_UNDER ? "LOW" : cost < COST_MID_UNDER ? "MID" : "HIGH";
    }

    /**
     * 위험 = 협력사가 당긴 날짜를 못 지킬 가능성.
     * 납기 준수율 90% 이상 LOW · 80% 이상 MID · 그 밑 HIGH, 2주 넘게 당기면 한 단계 위.
     */
    static String risk(double onTimeRate, long days) {
        int level = onTimeRate >= 90 ? 0 : onTimeRate >= 80 ? 1 : 2;
        if (days > LONG_PULL_DAYS) level++;
        return RISK.get(Math.min(level, 2));
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> shortages(Map<String, Object> result) {
        return (List<Map<String, Object>>) result.get("shortages");
    }

    private static String key(Map<String, Object> s) {
        return s.get("item_id") + "|" + s.get("plan_id") + "|" + s.get("plan_date");
    }
}
