<script setup lang="ts">
import { ref, computed } from "vue";
import { useWorkspace } from "../stores/workspace";
import Badge from "../components/Badge.vue";
import Drawer from "../components/Drawer.vue";
import type { History } from "../types";
const w = useWorkspace(),
  q = ref(""),
  status = ref(""),
  from = ref(""),
  to = ref(""),
  selected = ref<History>();
const rows = computed(() =>
  w.data!.history.filter(
    (h) =>
      (h.title + h.target).includes(q.value) &&
      (!status.value || h.status === status.value) &&
      (!from.value || h.at.slice(0, 10) >= from.value) &&
      (!to.value || h.at.slice(0, 10) <= to.value),
  ),
);
</script>
<template>
  <div class="toolbar">
    <input
      v-model="q"
      aria-label="이력 검색"
      placeholder="품목 · 작업 검색"
    /><select v-model="status" aria-label="작업 상태">
      <option value="">모든 상태</option>
      <option>진행 중</option>
      <option>승인 대기</option>
      <option>실행 대기</option>
      <option>완료</option>
      <option>실패</option></select
    ><input v-model="from" type="date" aria-label="이력 시작일" /><input
      v-model="to"
      type="date"
      aria-label="이력 종료일"
    />
  </div>
  <section class="card flush table-wrap">
    <table>
      <thead>
        <tr>
          <th>작업 / 대상</th>
          <th>상태</th>
          <th>시각</th>
          <th>상세</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="h in rows" :key="h.id">
          <td>
            <b>{{ h.title }}</b
            ><small>{{ h.target }}</small>
          </td>
          <td><Badge :text="h.status" /></td>
          <td>
            {{
              new Date(h.at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })
            }}
          </td>
          <td><button @click="selected = h">이력 · 근거</button></td>
        </tr>
      </tbody>
    </table>
    <div v-if="!rows.length" class="empty">조건에 맞는 이력이 없습니다.</div>
  </section>
  <Drawer v-if="selected" title="작업 이력과 근거" @close="selected = undefined"
    ><Badge :text="selected.status" />
    <h2>{{ selected.title }}</h2>
    <p>{{ selected.target }}</p>
    <p>{{ selected.detail }}</p>
    <h3>연결 근거</h3>
    <details open class="evidence">
      <summary>시연 업무 규정 · v1 · WF-001</summary>
      <p>
        구매 검수로 해결안의 비용등급을 확정하고, 생산관리 담당자가 우선순위를
        결정합니다. 선정된 발주 변경안은 구매팀장 승인 후 실행합니다.
      </p>
      <small>프론트 시연 목계약 · 실제 사내 규정 원문 아님</small>
    </details>
    <details>
      <summary>개발 상세</summary>
      <p>작업 식별자: {{ selected.id }}</p>
      <p>traceId: 서버 연결 후 제공</p>
    </details></Drawer
  >
</template>
