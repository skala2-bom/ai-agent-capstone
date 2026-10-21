<script setup lang="ts">
import { computed } from "vue";
import { segments } from "../services/answer";
// AI 답변 본문 (결론 · 색 구역 · 항목). 지금 대화와 지난 대화 기록이 같이 쓴다 (decisions 6.27·6.36)
const props = defineProps<{ text: string }>();
const blocks = computed(() => segments(props.text));
</script>
<template>
  <div v-if="blocks.length" class="answer">
    <template v-for="(b, i) in blocks" :key="i"
      ><p v-if="b.kind === 'lead'" class="answer-lead">{{ b.text }}</p
      ><p v-else-if="b.kind === 'callout'" class="answer-callout">{{ b.text }}</p
      ><h4 v-else-if="b.kind === 'heading'" :class="b.tone">{{ b.text }}</h4
      ><ul v-else-if="b.kind === 'list'">
        <li v-for="(item, j) in b.items" :key="j">
          <small v-if="item.where">{{ item.where }}</small
          ><span>{{ item.what }}</span
          ><em v-if="item.note" :class="item.tone">{{ item.note }}</em>
        </li>
      </ul
      ><p v-else>{{ b.text }}</p></template
    >
  </div>
</template>
