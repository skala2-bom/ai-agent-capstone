<script setup lang="ts">
import { computed } from "vue";
import Badge from "./Badge.vue";
const props = defineProps<{ result: unknown }>();
const data = computed(() =>
  props.result && typeof props.result === "object"
    ? (props.result as Record<string, unknown>)
    : {},
);
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
    <h3>영향 결과</h3>
    <p class="caption">정책 버전: {{ data.policy_version || "서버 미제공" }}</p>
    <template v-for="[key, label] in groups"
      ><article
        v-for="(r, n) in records(key)"
        :key="key + n"
        class="server-impact"
      >
        <Badge :text="label" /><b>{{ r.item_id }} · {{ r.qty }}개</b
        ><small>{{ r.plan_id }} · {{ r.plan_date }}</small>
        <p>예상재고 {{ r.projected_stock }} / 기준 {{ r.threshold }}</p>
        <small>{{ r.rule_id }}</small>
      </article></template
    >
    <p v-if="!groups.some(([key]) => records(key).length)" class="caption">
      서버가 구조화된 영향 항목을 제공하지 않았습니다.
    </p>
    <RouterLink to="/solutions" class="button wide">해결안 확인 →</RouterLink>
  </section>
</template>
