<script setup lang="ts">
import { ref, onBeforeUnmount, computed, watch } from "vue";
import ServerResult from "./ServerResult.vue";
import { useWorkspace } from "../stores/workspace";
import { mode, request } from "../services/api";
import { connect, EventAccumulator } from "../services/sse";
import { scenarios } from "../mocks/fixtures";
import type { AgentEvent } from "../types";
import { Sparkles, ArrowUp, PanelRightClose } from "lucide-vue-next";
const w = useWorkspace();
const question = ref("");
const answer = ref("");
const status = ref("분석 준비");
const err = ref("");
const events = ref<AgentEvent[]>([]);
const running = ref(false);
const result = ref<Record<string, unknown>>();
const sources = ref<unknown>();
const approvalReasons = ref<Record<string, string>>({});
const approvalStates = ref<Record<string, string>>({});
const submitting = ref<Record<string, boolean>>({});
const proposals = ref<Record<string, AgentEvent>>({});
const readOnlyTask = ref(false);
let stop = () => {};
let timer: ReturnType<typeof setTimeout> | undefined;
let generation = 0;
const sc = computed(() => scenarios.find((s) => s.id === w.scenario)!);
const displayedImpact = computed(() => result.value?.intent === "ADJUST"
  && "post_execution_impact" in result.value ? result.value.post_execution_impact : result.value?.impact);
const labels: Record<string, string> = { PREPARED: "검증 완료", PENDING: "승인 대기", EXECUTING: "실행 중",
  EXECUTED: "실행 완료", REJECTED: "거절", BLOCKED: "실행 보류", REGISTRATION_UNKNOWN: "승인 등록 확인 필요", EXECUTION_UNKNOWN: "실행 결과 확인 필요" };
function progress(e: AgentEvent) {
  return Object.values(proposals.value).find((p) => p.approvalId === e.approvalId);
}
function cardStatus(e: AgentEvent) {
  const p = progress(e);
  if (p && p.proposalStatus !== "PENDING") return labels[p.proposalStatus!] ?? p.proposalStatus;
  return approvalStates.value[e.approvalId!] ?? "승인 대기";
}
async function decide(e: AgentEvent, decision: "APPROVED" | "REJECTED") {
  const id = e.approvalId!;
  if (submitting.value[id] || cardStatus(e) !== "승인 대기") return;
  const reason = approvalReasons.value[id] ?? "";
  if (decision === "REJECTED" && !reason.trim()) return;
  const g = generation;
  submitting.value[id] = true;
  try {
    await request(`/ai/tasks/${encodeURIComponent(e.taskId)}/approval`, w.session?.token, "POST",
      { approvalId: id, decision, reason: reason || null });
    if (g === generation) approvalStates.value[id] = decision === "APPROVED" ? "승인 접수 · 실행 대기" : "거절 접수";
  } catch (error) {
    if (g === generation) err.value = (error as Error).message;
  } finally {
    if (g === generation) submitting.value[id] = false;
  }
}
function clear() {
  generation++;
  stop(); clearTimeout(timer);
  approvalReasons.value = {}; approvalStates.value = {}; submitting.value = {}; proposals.value = {};
  running.value = false; readOnlyTask.value = false;
  answer.value = ""; result.value = undefined; sources.value = undefined; events.value = [];
  status.value = "분석 준비"; err.value = "";
}
watch(() => w.context, () => { clear(); w.activeTask = undefined; question.value = ""; });
window.addEventListener("close-agent", clear);
onBeforeUnmount(() => { clear(); window.removeEventListener("close-agent", clear); });

function subscribe(taskId: string, g: number) {
  const acc = new EventAccumulator(taskId);
  stop = connect(taskId, w.session?.token, (e) => {
    if (g !== generation || !acc.accept(e)) return;
    events.value = [...acc.events]; proposals.value = { ...acc.proposals };
    answer.value = acc.answer; status.value = acc.status;
    if (e.type === "DONE") { result.value = e.result as Record<string, unknown>; sources.value = e.sources; }
    if (e.type === "FAILED" || e.type === "WORKFLOW_NOTICE") err.value = e.message ?? "처리 상태를 확인해 주세요.";
    if (acc.terminal) { running.value = false; stop(); }
  }, (m) => { if (g === generation) err.value = m; });
}
interface TaskDetail { taskId: string; userId: string; trigger: string; status: string; answer?: string;
  result?: Record<string, unknown>; sources?: unknown; }
async function openExisting(taskId: string) {
  clear();
  const g = generation;
  running.value = true; status.value = "작업 불러오는 중";
  async function fetchDetail() {
    try {
      const task = await request<TaskDetail>(`/ai/tasks/${encodeURIComponent(taskId)}`, w.session?.token);
      if (g !== generation) return;
      answer.value = task.answer ?? ""; result.value = task.result; sources.value = task.sources;
      if (task.userId === w.session?.userId || task.trigger === "SYSTEM") {
        subscribe(taskId, g); // Full SSE replay restores every proposal without a new task.
        return;
      }
      readOnlyTask.value = true;
      status.value = ({DONE: "완료", FAILED: "실패", WAITING_APPROVAL: "승인 대기", RUNNING: "진행 중", QUEUED: "접수"} as Record<string, string>)[task.status] ?? task.status;
      running.value = !["DONE", "FAILED"].includes(task.status);
      if (running.value) timer = setTimeout(fetchDetail, 3000);
    } catch (e) {
      if (g === generation) { err.value = (e as Error).message; running.value = false; }
    }
  }
  await fetchDetail();
}
watch(() => w.activeTask, (id) => { if (id && mode === "api") void openExisting(id); }, { immediate: true });
async function ask() {
  if (!question.value.trim()) return;
  clear();
  const g = generation;
  if (mode === "mock") {
    answer.value = "자유 질문은 실제 분석 API가 필요합니다. 업무 화면에서 준비된 시연 결과를 확인하세요.";
    status.value = "시연 안내"; return;
  }
  running.value = true; status.value = "접수 중";
  try {
    const { taskId } = await request<{ taskId: string }>("/ai/tasks", w.session?.token, "POST", { question: `${w.context}\n${question.value}` });
    if (g === generation) w.activeTask = taskId;
  } catch (e) {
    if (g === generation) { err.value = (e as Error).message; running.value = false; }
  }
}
</script>
<template>
  <aside class="ai-panel">
    <header>
      <h3><Sparkles :size="17" /> 업무 분석</h3>
      <button
        class="icon-button"
        aria-label="AI 패널 접기"
        @click="w.panel = false"
      >
        <PanelRightClose :size="18" />
      </button>
    </header>
    <div class="ai-body">
      <span class="eyebrow">선택한 업무</span>
      <div class="context">{{ w.context || "업무 항목을 선택하세요" }}</div>
      <span class="badge success">{{
        mode === "mock" ? "시연 결과" : "실시간 API"
      }}</span
      ><template v-if="mode === 'mock'"
        ><h3>{{ sc.name }}</h3>
        <p>{{ sc.change }}</p>
        <p v-if="w.data?.analysisStale" class="warning-box">
          변경 전 분석입니다. 재분석이 필요합니다.
        </p>
        <div class="ai-summary">
          <b>{{
            w.issues.length ? "변경 영향 확인이 필요합니다" : "신규 문제 없음"
          }}</b>
          <p v-for="(i, n) in w.issues" :key="n">
            {{ i.item }} · {{ i.kind }}
            {{ i.qty.toLocaleString() }}개<br /><small
              >{{ i.date }} · {{ i.plan }}</small
            >
          </p>
        </div>
        <p class="caption">
          {{ w.data?.policyVersion }}<br />사전 정의된 예시 · 실시간 계산 아님
        </p>
        <RouterLink class="button primary wide" to="/solutions"
          >해결안 비교하기 →</RouterLink
        >
        <details class="evidence">
          <summary>판정 근거 확인</summary>
          <p>기획안 A1~A3 시연 결과 / INV-001, INV-002, ECN-001</p>
          <p>
            예상재고가 0 미만이면 결품, 안전재고 미만이면 주의입니다. SC3 과잉은
            이전 30일 정책 결과입니다.
          </p>
        </details></template
      >
      <div v-if="status !== '분석 준비'" class="progress" role="status">
        {{ status }}
      </div>
      <p class="answer">{{ answer }}</p>
      <div v-if="err" class="error">
        {{ err }}<button v-if="w.activeTask" @click="openExisting(w.activeTask)">현재 작업 다시 연결</button>
      </div>
      <p v-if="readOnlyTask && running" class="caption">요청자의 실시간 분석은 요청자 화면에서 볼 수 있습니다. 이곳에서는 완료 결과를 자동으로 불러옵니다.</p>
      <ServerResult v-if="displayedImpact" :result="displayedImpact" />
      <p v-if="result?.intent === 'ADJUST' && result.post_execution_impact === null" class="warning-box">실행 후 영향 결과를 확인하지 못했습니다. 업무 데이터를 다시 확인해 주세요.</p>
      <article v-for="p in Object.values(proposals).filter(x => !x.approvalId)" :key="p.proposalId" class="card">
        <b>{{ p.itemId }} · {{ labels[p.proposalStatus!] ?? p.proposalStatus }}</b><p>{{ p.summary }}</p><p>{{ p.message }}</p>
      </article>
      <template v-if="readOnlyTask && Array.isArray(result?.proposals)">
        <article v-for="p in result.proposals" :key="p.proposal_id" class="card">
          <b>{{ p.option?.item_id }} · {{ labels[p.status] ?? p.status }}</b><p>{{ p.option?.summary }}</p><p>{{ p.reason }}</p>
        </article>
      </template>
      <details v-if="sources">
        <summary>서버 근거 · 원문</summary>
        <pre>{{ JSON.stringify(sources, null, 2) }}</pre>
      </details>
      <article
        v-for="e in events.filter((x) => x.type === 'APPROVAL_REQUIRED')"
        :key="e.approvalId"
        class="card"
      >
        <b>{{ cardStatus(e) }} · {{ e.itemId ?? e.approvalId }}</b>
        <p>{{ e.summary }}</p>
        <p v-if="progress(e)?.message">{{ progress(e)?.message }}</p>
        <template v-if="cardStatus(e) === '승인 대기' && w.session?.role === 'APPROVER'"
          ><label
            >거절 사유<textarea
              v-model="approvalReasons[e.approvalId!]"
              rows="2"
            /></label
          ><button
            :disabled="submitting[e.approvalId!] || !approvalReasons[e.approvalId!]?.trim()"
            @click="decide(e, 'REJECTED')"
          >
            거절</button
          ><button :disabled="submitting[e.approvalId!]" @click="decide(e, 'APPROVED')">
            승인 접수
          </button></template
        ><RouterLink to="/approvals">승인 대기함 열기</RouterLink>
      </article>
      <details v-if="events.length">
        <summary>수신 이벤트 · 개발 상세</summary>
        <p v-for="e in events" :key="e.seq">
          {{ e.seq }} · {{ e.type }} {{ e.tool }} {{ e.traceId }}
        </p>
      </details>
    </div>
    <form class="ai-input" @submit.prevent="ask">
      <label for="question">선택한 업무에 질문</label
      ><textarea
        id="question"
        v-model="question"
        placeholder="선택한 업무의 영향과 근거를 확인해줘."
        rows="3"
      />
      <div>
        <small>{{
          mode === "mock" ? "데모 · 자유 질문 미연결" : "업무 분석"
        }}</small
        ><button
          class="primary icon-button"
          :disabled="running || !question.trim()"
          aria-label="분석 요청"
        >
          <ArrowUp :size="18" />
        </button>
      </div>
    </form>
  </aside>
</template>
