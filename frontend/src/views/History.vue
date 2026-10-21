<script setup lang="ts">
import { ref, computed } from "vue";
import { useWorkspace } from "../stores/workspace";
import Badge from "../components/Badge.vue";
import Drawer from "../components/Drawer.vue";
import type { History, ExecutionTrace } from "../types";
import { mode, request } from "../services/api";
import { toTrace, traceLink } from "../services/traces";
const w = useWorkspace(),
  q = ref(""),
  status = ref(""),
  from = ref(""),
  to = ref(""),
  selected = ref<History>();
const traceError = ref("");
let selectionVersion = 0;
async function selectHistory(h: History) {
  selected.value = h;
  traceError.value = "";
  const version = ++selectionVersion;
  if (mode !== "api") return;
  try {
    const detail = await request<{ traces?: ExecutionTrace[] }>(`/ai/tasks/${encodeURIComponent(h.id)}`, w.session?.token);
    if (version === selectionVersion && selected.value?.id === h.id)
      selected.value = { ...h, traces: (detail.traces ?? []).map(toTrace).filter((t): t is ExecutionTrace => !!t) };
  } catch {
    if (version === selectionVersion && selected.value?.id === h.id) traceError.value = "실행 기록을 불러오지 못했습니다. 다시 열어 주세요.";
  }
}
const traces = computed(() => selected.value?.traces ?? (selected.value?.trace ? [selected.value.trace] : []));
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
          <td><button @click="selectHistory(h)">이력 · 근거</button></td>
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
    <button v-if="mode === 'api'" @click="w.openTask(selected.id); selected = undefined">현재 작업 · 승인 진행과 결과 보기</button>
    <h3 v-if="mode === 'mock'">연결 근거</h3>
    <details v-if="mode === 'mock'" open class="evidence">
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
      <p v-if="traceError" role="alert">{{ traceError }}</p>
      <p v-if="!traces.length">저장된 실행 추적이 없습니다.</p>
      <article v-for="trace in traces" :key="trace.traceId" class="evidence">
        <p>{{ trace.executionKind === 'DECISION' ? '승인·거절 후 재개' : '작업 실행' }} · {{ trace.outcome }}</p>
        <p>traceId: {{ trace.traceId }}</p>
        <p>실행 {{ ((trace.durationMs ?? 0) / 1000).toFixed(2) }}초 · 도구 {{ trace.toolCalls ?? 0 }}회 · 오류 {{ trace.toolErrors ?? 0 }}회</p>
        <p>LLM {{ trace.llmCalls ?? 0 }}회 · 입력 {{ trace.inputTokens ?? '미집계' }} / 출력 {{ trace.outputTokens ?? '미집계' }} tokens</p>
        <p>재시도 {{ trace.retryCount ?? 0 }}회 · 대체 모델 {{ trace.fallbackCount ?? 0 }}회 · 대화 요약 {{ trace.summaryCount ?? 0 }}회</p>
        <p>{{ trace.costUsd === null ? '비용은 LangSmith에서 확인' : `모델 비용 $${trace.costUsd ?? 0}` }}</p>
        <a v-if="traceLink(trace.traceUrl)" :href="traceLink(trace.traceUrl)" target="_blank" rel="noopener noreferrer">실행 추적 열기</a>
        <p v-else>{{ trace.tracingEnabled ? '추적 링크 미확인. LangSmith에서 traceId로 검색하세요.' : '외부 추적 비활성화 · 로컬 기록' }}</p>
      </article>
    </details></Drawer
  >
</template>
