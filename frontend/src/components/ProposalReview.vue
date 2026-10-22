<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import { useWorkspace } from "../stores/workspace";
import type { Approval } from "../types";
import { batchLocked, reviewLabel } from "../services/task-batch";
import ApprovalConfirm from "./ApprovalConfirm.vue";
const props = defineProps<{ approval: Approval; compact?: boolean }>();
const w = useWorkspace(), router = useRouter();
const state = computed(() => w.batchStates[props.approval.taskId]);
const entry = computed(() => state.value?.entries?.find(e => e.approvalId === props.approval.id));
const locked = computed(() => w.session?.role !== "APPROVER" || !!w.batchPending[props.approval.taskId] || batchLocked(state.value) || !entry.value);
const confirm = ref<"APPROVED" | "REJECTED">();
const reason = ref("");
const editing = ref(false);
async function decide() {
  const decision = confirm.value; confirm.value = undefined;
  if (decision) await w.sendBatch(props.approval.taskId, decision === "APPROVED" ? "CONFIRM" : "REPLAN", props.approval.id, {reason: reason.value});
}
async function modify() {
  if (await w.sendBatch(props.approval.taskId, "NEEDS_EDIT", props.approval.id)) editing.value = true;
}
function manual() {
  if (entry.value) void router.push({path: "/purchasing", query: {
    taskId: props.approval.taskId, approvalId: props.approval.id, poId: entry.value.poId,
  }});
}
</script>
<template>
  <template v-if="compact">
    <button v-if="entry && approval.status === 'PENDING' && w.approver" class="review-check"
      :class="{checked: entry.reviewStatus === 'CONFIRMED'}" :disabled="locked"
      :aria-label="`${entry.summary} · ${reviewLabel(entry)}`"
      @click="entry.reviewStatus === 'CONFIRMED' ? w.sendBatch(approval.taskId, 'UNCONFIRM', approval.id) : confirm = 'APPROVED'">
      {{ reviewLabel(entry) }}
    </button>
    <span v-else class="badge">{{ reviewLabel(entry) }}</span>
  </template>
  <section v-else class="proposal-review">
    <p><strong>{{ reviewLabel(entry) }}</strong><template v-if="entry?.source === 'MANUAL'"> · 직접 작성한 수정안</template></p>
    <p v-if="!entry" class="caption">새 제안으로 교체된 이전 요청입니다.</p>
    <p v-if="entry?.outcome" class="warning-box">{{ entry.outcome.message }}</p>
    <template v-if="entry && w.approver && !['COMPLETED', 'CANCELLED', 'REJECTED'].includes(state?.status ?? '')">
      <div class="action-row">
        <button class="primary" :disabled="locked || approval.status !== 'PENDING'"
          @click="entry.reviewStatus === 'CONFIRMED' ? w.sendBatch(approval.taskId, 'UNCONFIRM', approval.id) : confirm = 'APPROVED'">
          {{ entry.reviewStatus === 'CONFIRMED' ? '검토 해제' : '승인' }}
        </button>
        <button :disabled="locked || entry.reviewStatus === 'REPLANNING'" @click="modify">수정 필요</button>
      </div>
      <p class="caption">승인은 검토 완료 표시입니다. 실제 변경은 task에서 함께 실행합니다.</p>
      <div v-if="editing || entry.reviewStatus === 'EXCLUDED'" class="revision-choices">
        <label>수정 사유 · AI 재요청 시 필수<textarea v-model="reason" rows="3" maxlength="4000" placeholder="변경이 필요한 이유나 조건을 적어 주세요." /></label>
        <div class="action-row">
          <button :disabled="locked || !reason.trim() || approval.status !== 'PENDING'" @click="confirm = 'REJECTED'">AI에게 재요청</button>
          <button :disabled="locked" @click="manual">직접 수정</button>
        </div>
        <p class="caption">✕인 원안은 실행에서 제외됩니다. 해당 문제가 남으면 task 전체 실행이 보류됩니다.</p>
      </div>
    </template>
  </section>
  <ApprovalConfirm v-if="confirm" :decision="confirm" :purpose="confirm === 'APPROVED' ? 'review' : undefined"
    :summary="entry?.summary ?? ''" :reason="reason" @confirm="decide" @cancel="confirm = undefined" />
</template>
<style scoped>
.review-check { white-space: nowrap; font-size: 12px; }
.review-check.checked { background: #f0f7e5; color: #344c25; border-color: #c5dbac; }
.proposal-review { margin: 16px 0; }
.revision-choices { padding: 16px; border: 1px solid var(--border); border-radius: 8px; background: #fafbf8; }
</style>
