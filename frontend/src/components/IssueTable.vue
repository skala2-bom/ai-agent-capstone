<script setup lang="ts">
import type { Issue } from "../types";
import Badge from "./Badge.vue";
defineProps<{ issues: Issue[] }>();
defineEmits<{ select: [issue: Issue] }>();
</script>
<template>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>구분</th>
          <th>영향 품목 / 생산</th>
          <th>필요일</th>
          <th class="num">수량 (개)</th>
          <th>다음 확인</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(i, n) in issues" :key="n">
          <td><Badge :text="i.kind" /></td>
          <td>
            <button class="text-button" @click="$emit('select', i)">
              {{ i.item }}</button
            ><small>{{ i.plan }}</small>
          </td>
          <td class="mono">{{ i.date }}</td>
          <td class="num strong">{{ i.qty.toLocaleString() }}</td>
          <td class="detail-cell">{{ i.detail }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="!issues.length" class="empty">
      변경으로 생긴 신규 문제가 없습니다.
    </div>
  </div>
</template>
