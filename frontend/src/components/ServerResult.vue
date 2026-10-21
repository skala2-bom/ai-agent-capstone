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
        <small>{{ r.rule_id }}</small>
      </article></template
    >
    <p v-if="!groups.some(([key]) => records(key).length)" class="caption">
      서버가 구조화된 영향 항목을 제공하지 않았습니다.
    </p>
  </section>
</template>
