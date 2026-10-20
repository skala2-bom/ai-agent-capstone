<script setup lang="ts">
defineProps<{ text: string }>();
const labels: Record<string, string> = {
  PENDING: "승인 대기",
  APPROVED: "승인 접수 · 실행 대기",
  REJECTED: "거절",
  EXECUTED: "실행 완료",
};
</script>
<template>
  <span
    class="badge"
    :class="{
      danger: ['결품', '사용 제한', '실패', 'REJECTED', '결품 미해소'].includes(
        text,
      ),
      warning: [
        '주의',
        '과잉',
        '미검수',
        '보완 요청',
        'PENDING',
        '승인 대기',
      ].includes(text),
      success: ['확정', '완료', 'EXECUTED', '추가발주', '결품 해소'].includes(
        text,
      ),
    }"
    >{{ labels[text] || text }}</span
  >
</template>
