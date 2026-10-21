<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useWorkspace } from "../stores/workspace";
import type { Approval } from "../types";
import ApprovalNote from "./ApprovalNote.vue";
const props = defineProps<{ taskId: string; compact?: boolean }>();
const w = useWorkspace();
const state = computed(() => w.taskReview(props.taskId));
const drafts = computed(() => (w.data?.approvals ?? []).filter(a => a.taskId === props.taskId && a.status === "DRAFT")
  .sort((a, b) => Date.parse(a.at) - Date.parse(b.at)));
const ready = computed(() => !!state.value.ready && drafts.value.length > 0
  && drafts.value.every(a => state.value.ready!.approvalIds.includes(a.id)));
const locked = computed(() => state.value.busy || !!state.value.attempt);
const pollError = ref("");
let mounted = false;
let timer: ReturnType<typeof setTimeout> | undefined;
async function poll() {
  try {
    await w.loadTaskReview(props.taskId);
    await w.refreshApprovals();
    pollError.value = "";
  } catch (e) { pollError.value = (e as Error).message; }
  if (mounted) timer = setTimeout(poll, 3000);
}
onMounted(() => { mounted = true; void poll(); });
onBeforeUnmount(() => { mounted = false; clearTimeout(timer); });
function title(a: Approval) {
  return (w.data?.options.find(o => o.id === a.optionId)?.name ?? a.id)
    .replace(/\s*\(실제 발주 변경입니다\.\s*협력사 협의·확약 여부를 확인한 뒤 승인하세요\.\)/g, "").trim();
}
</script>
<template>
  <form v-if="w.session?.role === 'BUYER' && (drafts.length || state.attempt)" class="task-review"
    @submit.prevent="w.reviewTask(taskId, true)">
    <label>전체 문제 설명 · 승인 요청 시 필수
      <textarea v-model="state.taskProblem" rows="3" maxlength="4000" :disabled="locked"
        placeholder="이 작업에서 해결하려는 전체 문제와 요청 배경을 적어 주세요." />
    </label>
    <article v-for="a in compact ? [] : drafts" :key="a.id" class="task-review-item">
      <b>{{ title(a) }}</b>
      <ApprovalNote :card="a" />
    </article>
    <p class="caption">현재 검토 중인 해결안 {{ drafts.length }}건을 함께 처리합니다. 팀장은 보낸 해결안을 각각 승인·거절합니다.</p>
    <p v-if="!ready && !state.attempt" role="status">해결안을 모두 준비한 뒤 전송할 수 있습니다.</p>
    <p v-if="pollError" class="error">상태 갱신 실패: {{ pollError }}</p>
    <p v-if="state.error" class="error" role="alert">{{ state.error }}</p>
    <template v-if="state.attempt && !state.busy">
      <p>접수 결과를 확인하지 못했습니다. 같은 내용으로 다시 확인해 주세요.</p>
      <button type="button" @click="w.reviewTask(taskId, state.attempt!.send)">접수 결과 다시 확인</button>
    </template>
    <div v-else class="action-row">
      <button type="button" :disabled="!ready || locked" @click="w.reviewTask(taskId, false)">보내지 않음</button>
      <button class="primary" :disabled="!ready || locked || !state.taskProblem.trim()">
        {{ state.busy ? "처리 중…" : "팀장에게 승인 요청" }}
      </button>
    </div>
  </form>
</template>
<style scoped>
.task-review { margin-top: 14px; padding: 16px; background: #f8fbf4; border: 1px solid #e5eadf; border-radius: 8px; }
.task-review-item { margin: 12px 0; padding: 16px; background: #fff; border: 1px solid #e5e9ee; border-radius: 6px; }
.task-review textarea { width: 100%; box-sizing: border-box; }
.task-review .error { white-space: normal; }
</style>
