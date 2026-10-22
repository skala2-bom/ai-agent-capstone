<script setup lang="ts">
import { computed } from "vue";
import { proposalText } from "../services/proposal-text";
// 해결안 제목: 재제안 표시 + 핵심 한 줄 + 발주 정보·여유 표시 (승인함·채팅 카드 공용)
const props = defineProps<{ text?: string; revision?: number }>();
const t = computed(() => proposalText(props.text));
const revision = computed(() => props.revision ?? t.value.revision);
</script>
<template>
  <span class="proposal-title">
    <span class="title-line"><span v-if="revision" class="chip repropose">재제안 {{ revision }}차</span><b>{{ t.main }}</b></span>
    <span v-if="t.po || t.tags.length" class="title-meta">
      <small v-if="t.po">{{ t.po }}</small>
      <span v-for="tag in t.tags" :key="tag" class="chip" :class="{ manual: tag === '직접 수정' }">{{ tag }}</span>
    </span>
  </span>
</template>
<style scoped>
.proposal-title { display: flex; flex-direction: column; gap: 4px; }
.title-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.title-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.title-meta small { color: var(--muted, #6b7280); }
.chip { font-size: 11px; line-height: 1; padding: 4px 7px; border-radius: 999px; background: #eef2ea; color: #3f5531; white-space: nowrap; }
.chip.repropose { background: #e8eefc; color: #2f4fa8; font-weight: 600; }
.chip.manual { background: #fdf1e3; color: #9a5b14; }
</style>
