<script setup lang="ts">
import { computed } from "vue";
import type { Approval } from "../types";
import { useWorkspace } from "../stores/workspace";
const props = defineProps<{ card: Approval }>();
const w = useWorkspace();
const state = computed(() => w.taskReview(props.card.taskId));
</script>
<template>
  <div class="proposal-review-note">
    <p class="fixed-problem"><strong>품목별 문제</strong><br />{{ card.problem || "서버가 품목별 문제를 제공하지 않았습니다." }}</p>
    <label>개인 메모
      <textarea :value="state.memos[card.id] ?? card.memo ?? ''" rows="2" maxlength="4000"
        :disabled="state.busy || !!state.attempt" placeholder="협력사 확인 내용, 내 판단이나 우려 사항"
        @input="state.memos[card.id] = ($event.target as HTMLTextAreaElement).value" />
    </label>
  </div>
</template>
<style scoped>
.fixed-problem { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; }
textarea { width: 100%; box-sizing: border-box; }
</style>
