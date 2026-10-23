<script setup lang="ts">
import { computed } from "vue";
import Badge from "./Badge.vue";
const props = defineProps<{ result: unknown; title?: string }>();
// agent의 DONE.result는 {intent, whatif, impact}이고 결품·주의·과잉은 impact 안에 있다 (contracts.md 3.2).
// impact가 없으면 result 자체를 본다 — 영향 분석 결과를 바로 넣어 주는 경우도 그대로 그려지게.
// 해결안을 실행했으면 처음 분석(impact)이 아니라 실행 후 상태(post_execution_impact)를 보여 준다 (contracts 3.2)
const raw = computed(() =>
  props.result && typeof props.result === "object"
    ? (props.result as Record<string, unknown>)
    : {},
);
const after = computed(() => {
  const p = raw.value.post_execution_impact;
  return Array.isArray(raw.value.executions) && raw.value.executions.length && p && typeof p === "object"
    ? (p as Record<string, unknown>)
    : undefined;
});
const data = computed(() => {
  const r = raw.value;
  if (after.value) return after.value;
  return r.impact && typeof r.impact === "object"
    ? (r.impact as Record<string, unknown>)
    : r;
});
const groups = [
  ["shortages", "결품"],
  ["warnings", "주의"],
  ["excesses", "과잉"],
] as const;
// 결품 근거 (decisions 6.50): 재고 + 그날까지 입고 − 그날까지 소요 = 예상재고
type Basis = { on_hand: number; receipts: { qty: number }[]; uses: { plan_id: string; qty: number }[] };
function basisText(r: Record<string, unknown>) {
  const b = r.basis as Basis | undefined;
  if (!b || !Array.isArray(b.receipts) || !Array.isArray(b.uses)) return "";
  const sum = (xs: { qty: number }[]) => xs.reduce((n, x) => n + x.qty, 0).toLocaleString();
  return `재고 ${b.on_hand.toLocaleString()} + 입고 ${sum(b.receipts)} − 소요 ${sum(b.uses)} (${b.uses
    .map((u) => `${u.plan_id} ${u.qty.toLocaleString()}`)
    .join(" · ")})`;
}
// 장기화 대비 1·4·12주 비교 (contracts 49). 통보 기준 결과(impact)는 위에 그대로, 비교는 아래 표로
type Short = { item_id: string; item_name?: string; plan_id: string; plan_date: string; product_name?: string; qty: number };
type Scenario = { weeks: number; due_dates: Record<string, string>; impact: { shortages?: Short[] } };
const scenarios = computed(() =>
  after.value || !Array.isArray(raw.value.scenarios) ? [] : (raw.value.scenarios as Scenario[]),
);
function scenarioRows(sc: Scenario, i: number) {
  const seen = new Set(
    scenarios.value.slice(0, i).flatMap((p) => (p.impact.shortages ?? []).map((s) => `${s.item_id}|${s.plan_id}`)),
  );
  return (sc.impact.shortages ?? []).map((s) => ({ ...s, isNew: i > 0 && !seen.has(`${s.item_id}|${s.plan_id}`) }));
}
function records(key: string) {
  const x = data.value[key];
  return Array.isArray(x)
    ? (x.filter((v) => v && typeof v === "object") as Record<string, unknown>[])
    : [];
}
</script>
<template>
  <section>
    <h3>{{ title ?? (after ? "실행 후 상태" : "영향 결과") }}</h3>
    <p class="caption">정책 버전: {{ data.policy_version || "서버 미제공" }}</p>
    <template v-for="[key, label] in groups"
      ><article
        v-for="(r, n) in records(key)"
        :key="key + n"
        class="server-impact"
      >
        <Badge :text="label" /><b
          >{{ r.item_name ?? r.item_id }}<template v-if="r.qty != null"> · {{ Number(r.qty).toLocaleString() }}개</template></b
        ><small
          >{{ r.product_name ? `${r.product_name} (${r.plan_id})` : (r.plan_id ?? "") }} ·
          {{ r.plan_date ?? r.date }}</small
        >
        <p>예상재고 {{ r.projected_stock }} / 기준 {{ r.threshold }}</p>
        <small v-if="basisText(r)" class="caption">{{ basisText(r) }}</small>
        <small>{{ r.rule_id }}</small>
      </article></template
    >
    <p v-if="!groups.some(([key]) => records(key).length)" class="caption">
      서버가 구조화된 영향 항목을 제공하지 않았습니다.
    </p>
    <template v-if="scenarios.length">
      <h3>장기화 대비 비교</h3>
      <p class="caption">4주·12주는 협력사가 확정한 날짜가 아닌 장기화 대비 가정입니다.</p>
      <table class="scenario-table">
        <thead>
          <tr><th>지연</th><th>입고 예정</th><th>결품</th></tr>
        </thead>
        <tbody>
          <tr v-for="(sc, i) in scenarios" :key="sc.weeks">
            <td>{{ sc.weeks }}주</td>
            <td>{{ Object.values(sc.due_dates).join(", ") }}</td>
            <td>
              <span v-if="!scenarioRows(sc, i).length">없음</span>
              <div v-for="s in scenarioRows(sc, i)" :key="`${s.item_id}|${s.plan_id}`">
                <Badge v-if="s.isNew" text="새로" />
                {{ s.product_name ?? s.plan_id }} ({{ s.plan_id }}, {{ s.plan_date }}) ·
                {{ s.item_name ?? s.item_id }} {{ Number(s.qty).toLocaleString() }}개
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>
