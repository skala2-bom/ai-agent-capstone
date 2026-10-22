<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps<{
  decision: "APPROVED" | "REJECTED";
  summary: string;
  reason?: string;
}>();
const emit = defineEmits<{ confirm: []; cancel: [] }>();
const dialog = ref<HTMLDialogElement>();
const approve = computed(() => props.decision === "APPROVED");
const title = computed(() => approve.value ? "이 해결안을 승인할까요?" : "이 해결안을 거절할까요?");
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
      <p v-if="approve">승인하면 검증 후 발주 변경이 실행됩니다. 내용을 확인해 주세요.</p>
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
        >{{ approve ? "승인하기" : "거절하기" }}</button>
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
