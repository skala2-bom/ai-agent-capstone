<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useWorkspace } from "../stores/workspace";
import { useCoordination } from "../stores/coordination";
import { request } from "../services/api";
import { planOperations, type CoordinationRequest, type PlanPreview } from "../services/coordination";
import ServerResult from "./ServerResult.vue";
import ApprovalConfirm from "./ApprovalConfirm.vue";
const props = defineProps<{item: CoordinationRequest}>();
const emit = defineEmits<{changed: []}>();
const w = useWorkspace(), c = useCoordination(), route = useRoute();
const rows = ref<{id: string; product: string; date: string; qty: number; originalDate: string; originalQty: number}[]>([]);
const preview = ref<PlanPreview>(), error = ref(""), busy = ref(false), reason = ref(""), confirming = ref(false);
const attempt = ref<Record<string, unknown>>();
const ops = computed(() => planOperations(rows.value));
const summary = computed(() => (preview.value?.changes ?? []).map(p => `${p.planId}: ${p.before.plan_date} · ${p.before.qty}개 → ${p.after.plan_date} · ${p.after.qty}개`).join('\n'));
async function reload() {
  preview.value = undefined; attempt.value = undefined; busy.value = true; error.value = "";
  try {
    const data = await request<{plans: {plan_id: string; product_id: string; plan_date: string; qty: number; status: string}[]}>("/plans", w.session?.token);
    rows.value = data.plans.filter(p => p.status === "PLANNED").map(p => ({id: p.plan_id,
      product: w.data?.plans.find(x => x.id === p.plan_id)?.product ?? p.product_id,
      date: p.plan_date, qty: p.qty, originalDate: p.plan_date, originalQty: p.qty}));
    if (typeof route.query.analysisTaskId === "string") {
      const task = await request<{coordination?: CoordinationRequest; result?: {whatif?: Record<string, unknown>[]}}>(`/ai/tasks/${encodeURIComponent(route.query.analysisTaskId)}`, w.session?.token);
      if (task.coordination?.id === props.item.id) for (const op of task.result?.whatif ?? []) {
        const row = rows.value.find(p => p.id === op.plan_id);
        if (row && op.op === "reschedule_plan") row.date = String(op.new_date);
        if (row && op.op === "change_plan_qty") row.qty = Number(op.new_qty);
      }
    }
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
watch(ops, () => { preview.value = undefined; attempt.value = undefined; }, {deep: true});
watch(() => props.item.revision, () => { preview.value = undefined; attempt.value = undefined; });
async function simulate() {
  busy.value = true; error.value = ""; preview.value = undefined;
  try { preview.value = await request(`/coordination/requests/${props.item.id}/plan-preview`, w.session?.token, "POST", {expectedRevision: props.item.revision, whatif: ops.value}); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function execute() {
  confirming.value = false;
  if (!preview.value?.canExecute || busy.value) return;
  busy.value = true; error.value = "";
  attempt.value ??= {commandId: crypto.randomUUID(), expectedRevision: props.item.revision, previewId: preview.value.previewId, reason: reason.value.trim()};
  try {
    await request(`/coordination/requests/${props.item.id}/plan-execute`, w.session?.token, "POST", attempt.value);
    await Promise.all([c.refresh(), w.load()]); emit("changed"); await reload();
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
onMounted(reload);
</script>
<template>
  <section class="plan-request-review">
    <h3>생산계획 변경안</h3>
    <p class="caption">변경할 계획의 날짜·수량을 입력하세요. 검증과 최종 확인 전에는 저장하지 않습니다.</p>
    <div class="table-scroll"><table><thead><tr><th>계획</th><th>생산일</th><th>수량</th></tr></thead><tbody>
      <tr v-for="p in rows" :key="p.id"><td>{{ p.id }} · {{ p.product }}<small>{{ p.originalDate }} · {{ p.originalQty }}개</small></td>
        <td><input v-model="p.date" type="date" :aria-label="`${p.id} 변경 생산일`" :disabled="busy || !!attempt" /></td>
        <td><input v-model.number="p.qty" type="number" min="1" step="1" :aria-label="`${p.id} 변경 수량`" :disabled="busy || !!attempt" /></td></tr>
    </tbody></table></div>
    <button :disabled="busy || !ops.length" @click="simulate">변경안 검증</button>
    <button :disabled="busy" @click="reload">현재 계획 다시 불러오기</button>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <template v-if="preview">
      <ServerResult :result="preview.impact" title="변경 후 영향" />
      <p v-if="preview.error" class="warning-box">{{ preview.error }} 해결이 어려우면 AI와 다른 조건을 검토하거나 요청을 최종 거절하세요.</p>
      <template v-if="preview.canExecute">
        <div v-if="preview.partial" class="partial-notice">
          <b>생산계획은 먼저 저장할 수 있지만 구매 문제가 남아 있습니다.</b>
          <p>저장 후 아래의 ‘남은 문제를 구매팀에 요청’으로 전달하세요. 이 단계에서는 발주를 변경하지 않습니다.</p>
          <ServerResult v-if="preview.remainingImpact" :result="preview.remainingImpact" title="저장 후 구매팀에 요청할 문제" />
        </div>
        <p class="change-summary">{{ summary }}</p>
        <label>변경 사유<textarea v-model="reason" maxlength="4000" :disabled="busy || !!attempt" /></label>
        <button class="primary" :disabled="busy || !reason.trim()" @click="attempt ? execute() : confirming = true">{{ attempt ? '동일 실행 결과 다시 확인' : preview.partial ? '생산계획 먼저 저장' : '생산계획 변경 확정' }}</button>
      </template>
    </template>
    <ApprovalConfirm v-if="confirming" :purpose="preview?.partial ? 'plan-partial' : 'plan-execute'" decision="APPROVED" :summary="summary" :reason="reason" @confirm="execute" @cancel="confirming = false" />
  </section>
</template>
<style scoped>
.plan-request-review { margin-top:24px; padding:20px; background:#f9fcf7; border:1px solid #dfe8d7; border-radius:10px; }
.table-scroll { overflow:auto; } table { width:100%; } input[type=number] { width:100px; }
small { display:block; color:var(--muted); } textarea { width:100%; } button { margin:8px 8px 8px 0; }
.change-summary { white-space:pre-wrap; }
</style>
