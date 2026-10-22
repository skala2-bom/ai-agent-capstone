<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useWorkspace } from "../stores/workspace";
import { batchLocked } from "../services/task-batch";
import ApprovalConfirm from "./ApprovalConfirm.vue";
import RequestDecision from "./RequestDecision.vue";
import { useCoordination } from "../stores/coordination";
const props = defineProps<{ taskId: string }>();
const w = useWorkspace();
const coordination = useCoordination();
const state = computed(() => w.batchStates[props.taskId]);
const linked = computed(() => coordination.items.find(r => r.id === state.value?.coordinationId));
watch(() => state.value?.coordinationId, id => { if (id) void coordination.load(id).catch(() => {}); }, {immediate:true});
const choice = ref<"execute" | "cancel">();
const summary = computed(() => [...(state.value?.entries ?? []).map(e => `${e.reviewStatus === 'CONFIRMED' ? '✓ 실행' : '✕ 제외'} · ${e.summary}`),
  ...(state.value?.planChanges ?? []).map(p => `생산계획 함께 변경 · ${p.planId}: ${p.before.plan_date} · ${p.before.qty}개 → ${p.after.plan_date} · ${p.after.qty}개`)].join('\n'));
const labels: Record<string, string> = {REVIEW: "제안 검토", BLOCKED: "실행 보류", EXECUTING: "전체 재검증 · 처리 중", UNKNOWN: "실행 결과 확인 필요", COMPLETED: "전체 실행 완료", CANCELLED: "작업 전체 취소", REJECTED: "부서 요청 최종 거절", PREPARING: "제안 준비 중"};
async function submit() {
  const action = choice.value; choice.value = undefined;
  if (action) await w.sendBatch(props.taskId, action === "execute" ? "EXECUTE" : "CANCEL");
}
</script>
<template>
  <div v-if="state?.mode === 'BATCH'" class="batch-actions">
    <div v-if="linked" class="card"><b>생산관리 요청 · {{ linked.requesterName }}</b><p>{{ linked.message }}</p>
      <p v-for="p in linked.planChanges" :key="p.planId">{{ p.planId }}: {{ p.before.plan_date }} · {{ p.before.qty }}개 → {{ p.after.plan_date }} · {{ p.after.qty }}개</p>
      <RouterLink :to="{path:'/requests',query:{id:linked.id}}">부서 요청 상세 보기 →</RouterLink></div>
    <p><strong>{{ labels[state.status] ?? state.status }}</strong>
      <template v-if="state.status === 'COMPLETED'"> · 실행 {{ state.entries?.filter(e => e.reviewStatus === 'EXECUTED').length ?? 0 }}건</template>
      <template v-else-if="!['CANCELLED', 'REJECTED'].includes(state.status)">
        · ✓ {{ state.entries?.filter(e => e.reviewStatus === 'CONFIRMED').length ?? 0 }}건
        · ✕ {{ state.entries?.filter(e => e.reviewStatus === 'EXCLUDED').length ?? 0 }}건
      </template></p>
    <p v-if="w.batchPending[taskId]" role="status">{{ w.batchPending[taskId]?.action === 'REPLAN' ? 'AI가 새 대안을 검토하고 있습니다…' : '검토 요청을 처리하고 있습니다…' }}</p>
    <p v-if="state.error || w.batchErrors[taskId]" class="error" role="alert">{{ w.batchErrors[taskId] || state.error }}</p>
    <p v-if="state.requestReason" class="caption">{{ state.requestReason }}</p>
    <template v-if="w.approver">
      <button v-if="['UNKNOWN', 'EXECUTING'].includes(state.status) && state.lastCommand" :disabled="!!w.batchPending[taskId]"
        @click="w.sendBatch(taskId, state.lastCommand!.action, undefined, {}, state.lastCommand)">실행 결과 다시 확인</button>
      <div v-else-if="!['COMPLETED', 'CANCELLED', 'REJECTED'].includes(state.status)" class="action-row">
        <button class="primary" :disabled="!state.canExecute || !!w.batchPending[taskId] || batchLocked(state)" @click="choice = 'execute'">승인 및 실행</button>
        <button :disabled="!!w.batchPending[taskId] || batchLocked(state)" @click="choice = 'cancel'">전체 취소</button>
      </div>
    </template>
    <p v-if="!['COMPLETED', 'CANCELLED', 'REJECTED'].includes(state.status)" class="caption">선택한 수정안을 함께 검증하고, task의 문제가 모두 해결되는 경우에만 실행합니다.</p>
    <RequestDecision v-if="linked" :item="linked" @changed="w.refreshApprovals" />
  </div>
  <ApprovalConfirm v-if="choice" :decision="choice === 'execute' ? 'APPROVED' : 'REJECTED'" :purpose="choice"
    :summary="summary" @confirm="submit" @cancel="choice = undefined" />
</template>
<style scoped>
.batch-actions { margin-top: 12px; }
.batch-actions p { margin: 8px 0; }
</style>
