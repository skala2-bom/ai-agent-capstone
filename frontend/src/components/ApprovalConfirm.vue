<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps<{
  decision: "APPROVED" | "REJECTED";
  summary: string;
  reason?: string;
  purpose?: "review" | "execute" | "cancel" | "request-reject" | "plan-execute";
}>();
const emit = defineEmits<{ confirm: []; cancel: [] }>();
const dialog = ref<HTMLDialogElement>();
const approve = computed(() => props.decision === "APPROVED");
const title = computed(() => props.purpose === "review" ? "이 제안의 검토를 완료할까요?"
  : props.purpose === "execute" ? "검토한 수정안을 함께 실행할까요?"
  : props.purpose === "cancel" ? "이 작업을 전체 취소할까요?"
  : props.purpose === "request-reject" ? "부서 요청을 최종 거절할까요?"
  : props.purpose === "plan-execute" ? "생산계획 변경을 확정할까요?"
  : approve.value ? "이 해결안을 승인할까요?" : "이 해결안을 거절할까요?");
function keepFocus(event: KeyboardEvent) {
  if (event.key !== "Tab") return;
  const buttons = dialog.value?.querySelectorAll<HTMLButtonElement>("button");
  if (!buttons?.length) return;
  const first = buttons[0], last = buttons[buttons.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus();
  }
}
onMounted(() => dialog.value?.showModal());
onBeforeUnmount(() => dialog.value?.close());
</script>

<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="approval-confirm"
      :aria-label="title"
      @cancel.prevent="emit('cancel')"
      @keydown.stop="keepFocus"
    >
      <h2>{{ title }}</h2>
      <p class="confirmation-summary">{{ summary }}</p>
      <p v-if="purpose === 'review'">검토 완료 표시만 남깁니다. 발주 변경은 task의 승인 및 실행에서 진행합니다.</p>
      <p v-else-if="purpose === 'execute'">현재 데이터로 전체 문제 해결 여부를 재검증합니다. 통과한 경우에만 선택한 변경을 함께 반영합니다.</p>
      <p v-else-if="purpose === 'cancel'">이 task의 제안과 수동 수정안을 취소합니다. 발주 변경은 실행하지 않습니다.</p>
      <p v-else-if="purpose === 'request-reject'">요청을 종료하고 작성한 사유를 요청 부서에 전달합니다. 확정된 입고 지연 사실은 유지됩니다.</p>
      <p v-else-if="purpose === 'plan-execute'">현재 데이터로 재검증한 뒤 생산계획을 함께 변경합니다. 발주·입고 정보는 변경하지 않습니다.</p>
      <p v-else-if="approve">승인하면 검증 후 발주 변경이 실행됩니다. 내용을 확인해 주세요.</p>
      <template v-else>
        <p>아래 사유로 거절하며, AI가 대안을 다시 검토합니다.</p>
        <div class="confirmation-reason"><strong>거절 사유</strong><p>{{ reason }}</p></div>
      </template>
      <div class="confirmation-actions">
        <button type="button" autofocus @click="emit('cancel')">돌아가기</button>
        <button
          type="button"
          :class="approve ? 'primary' : 'reject-confirm'"
          @click="emit('confirm')"
        >{{ purpose === "request-reject" ? "최종 거절 및 사유 전달" : purpose === "plan-execute" ? "생산계획 변경 확정" : purpose === "review" ? "검토 완료" : purpose === "execute" ? "전체 승인 및 실행" : purpose === "cancel" ? "전체 취소" : approve ? "승인하기" : "거절하기" }}</button>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.approval-confirm {
  width: min(440px, calc(100vw - 32px));
  max-height: calc(100dvh - 48px);
  margin: auto;
  padding: 24px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #fff;
  color: #26313e;
  box-shadow: 0 16px 48px #152a3b26;
  overflow: auto;
}
.approval-confirm::backdrop { background: #152a3b66; }
h2 { margin: 0 0 16px; font-size: 18px; }
p { line-height: 1.6; overflow-wrap: anywhere; }
.confirmation-summary { padding: 14px; border-radius: 8px; background: #f9fcf6; white-space: pre-wrap; }
.confirmation-reason { padding: 14px; border-radius: 8px; background: #f7f8fa; }
.confirmation-reason p { margin: 8px 0 0; white-space: pre-wrap; }
.confirmation-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 24px; }
.reject-confirm { background: #fff1f0; border-color: #f1c8c5; color: #a6352d; font-weight: 600; }
.reject-confirm:hover { background: #ffe4e1; }
</style>
