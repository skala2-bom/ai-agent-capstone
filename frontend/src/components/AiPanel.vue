<script setup lang="ts">
import { ref, onBeforeUnmount, computed, watch, nextTick } from "vue";
import ServerResult from "./ServerResult.vue";
import AnswerBlocks from "./AnswerBlocks.vue";
import { sourceCards } from "../services/answer";
import { useWorkspace } from "../stores/workspace";
import { mode, request } from "../services/api";
import { connect, EventAccumulator } from "../services/sse";
import { scenarios } from "../mocks/fixtures";
import type { AgentEvent } from "../types";
import { Sparkles, ArrowUp, PanelRightClose } from "lucide-vue-next";
const w = useWorkspace();
const question = ref("");
// 보낸 질문 (말풍선). 입력칸은 보내면 비운다 — 일반 채팅처럼 (decisions 6.35)
const asked = ref("");
// 지난 대화 기록 (decisions 6.36): 새 질문을 보내거나 다른 작업을 열면 지금 대화를 위로 올린다.
// 화면 기록일 뿐 AI가 앞 대화를 기억하는 것은 아니다 — 질문마다 따로 처리된다
interface Turn { question: string; answer: string; status: string; taskId?: string }
const history = ref<Turn[]>([]);
let currentTask: string | undefined;
const body = ref<HTMLElement>();
function archive() {
  if (!asked.value && !answer.value) return;
  history.value = [...history.value, { question: asked.value, answer: answer.value, status: status.value, taskId: currentTask }].slice(-20);
}
function reopen(i: number) {
  const t = history.value[i];
  history.value = history.value.filter((_, j) => j !== i);
  if (t.taskId) void openExisting(t.taskId);
}
function resetAll() {
  clear(); history.value = []; currentTask = undefined;
}
const sending = ref(false);
const answer = ref("");
const status = ref("분석 준비");
const err = ref("");
const events = ref<AgentEvent[]>([]);
const running = ref(false);
const result = ref<Record<string, unknown>>();
const sources = ref<unknown>();
const docs = computed(() => sourceCards(sources.value));
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
  EXECUTED: "실행 완료", REJECTED: "거절", WITHDRAWN: "보내지 않음", BLOCKED: "실행 보류", REGISTRATION_UNKNOWN: "승인 등록 확인 필요", EXECUTION_UNKNOWN: "실행 결과 확인 필요" };
function progress(e: AgentEvent) {
  return Object.values(proposals.value).find((p) => p.approvalId === e.approvalId);
}
function cardStatus(e: AgentEvent) {
  const p = progress(e);
  if (p && p.proposalStatus !== "PENDING") return labels[p.proposalStatus!] ?? p.proposalStatus;
  return approvalStates.value[e.approvalId!] ?? "승인 대기";
}
// 요청자 검토를 채팅창에서 (decisions 6.34): 카드마다 문제 정의·메모를 적어 보내거나 보내지 않는다.
// 승인 대기함과 같은 API(w.review)라 어느 쪽에서 해도 상태가 같다
const draftProblem = ref<Record<string, string>>({});
const draftMemo = ref<Record<string, string>>({});
/** backend 카드 상태. 아직 목록에 없으면 요청자 기준으로 짐작한다 — 구매 요청은 초안, 팀장 요청은 승인 대기 */
function cardState(e: AgentEvent) {
  return w.data?.approvals.find((a) => a.id === e.approvalId)?.status
    ?? (w.session?.role === "APPROVER" ? "PENDING" : "DRAFT");
}
function cardLabel(e: AgentEvent) {
  const s = cardState(e);
  if (w.session?.role !== "APPROVER" && (s === "DRAFT" || s === "PENDING" || s === "WITHDRAWN"))
    return { DRAFT: "검토 전 · 보낼지 정하세요", PENDING: "팀장 승인 대기", WITHDRAWN: "보내지 않음" }[s];
  return cardStatus(e);
}
async function reviewCard(e: AgentEvent, send: boolean) {
  const id = e.approvalId!;
  if (!w.data?.approvals.some((a) => a.id === id)) await w.refreshApprovals();
  await w.review(id, send, draftProblem.value[id] ?? e.problem ?? "", draftMemo.value[id] ?? "");
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
  status.value = "분석 준비"; err.value = ""; asked.value = "";
}
// 업무를 바꾸면 이전 답변은 지운다. 입력 중이던 질문 글은 남긴다 — 질문을 쓰고 나서 업무를 고를 수도 있다
watch(() => w.context, () => { archive(); clear(); currentTask = undefined; w.activeTask = undefined; });
// "선택한 업무" 목록: 데이터의 모든 생산계획·발주·자재 (decisions 6.32).
// value는 각 화면에서 항목을 누를 때 넣는 문구와 같다 — 그래야 화면에서 누른 업무가 여기서 그대로 선택돼 보인다.
// 보이는 글자에만 날짜·수량을 더한다. AI에게 보내는 문구에 수량을 넣으면 질문의 가정 변경으로 잘못 읽을 수 있다
const contextGroups = computed(() => {
  const d = w.data;
  if (!d) return [];
  return [
    { label: "생산계획", options: d.plans.map((p) => ({ value: `${p.id} · ${p.product}`,
      text: `${p.id} · ${p.product} — ${p.date} · ${p.qty.toLocaleString()}대` })) },
    { label: "발주", options: d.purchases.map((p) => ({ value: `${p.id} · ${p.item}`,
      text: `${p.id} · ${p.item} — ${p.qty.toLocaleString()}개 · ${p.date} 입고` })) },
    { label: "자재·제품", options: d.items.map((i) => ({ value: `${i.id} · ${i.name}`, text: `${i.id} · ${i.name}` })) },
  ];
});
const knownContexts = computed(() => new Set(contextGroups.value.flatMap((g) => g.options.map((o) => o.value))));
// 로그아웃·계정 전환이면 기록까지 지운다 — 다른 사람에게 앞 사람의 대화가 보이면 안 된다
window.addEventListener("close-agent", resetAll);
onBeforeUnmount(() => { clear(); window.removeEventListener("close-agent", resetAll); });

function subscribe(taskId: string, g: number) {
  const acc = new EventAccumulator(taskId);
  stop = connect(taskId, w.session?.token, (e) => {
    if (g !== generation || !acc.accept(e)) return;
    events.value = [...acc.events]; proposals.value = { ...acc.proposals };
    answer.value = acc.answer; status.value = acc.status;
    if (e.type === "DONE") { result.value = e.result as Record<string, unknown>; sources.value = e.sources; }
    if (e.type === "APPROVAL_REQUIRED" && e.approvalId) {
      draftProblem.value[e.approvalId] ??= e.problem ?? "";
      void w.refreshApprovals().catch(() => {});   // 카드 상태(초안 여부)를 바로 알도록
    }
    if (e.type === "FAILED" || e.type === "WORKFLOW_NOTICE") err.value = e.message ?? "처리 상태를 확인해 주세요.";
    if (acc.terminal) { running.value = false; stop(); }
  }, (m) => { if (g === generation) err.value = m; });
}
interface TaskDetail { taskId: string; userId: string; trigger: string; status: string; answer?: string; question?: string;
  result?: Record<string, unknown>; sources?: unknown; }
async function openExisting(taskId: string) {
  if (taskId !== currentTask) archive();
  currentTask = taskId;
  clear();
  const g = generation;
  running.value = true; status.value = "작업 불러오는 중";
  async function fetchDetail() {
    try {
      const task = await request<TaskDetail>(`/ai/tasks/${encodeURIComponent(taskId)}`, w.session?.token);
      if (g !== generation) return;
      answer.value = task.answer ?? ""; result.value = task.result; sources.value = task.sources;
      asked.value = task.question ?? "";
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
/** Enter = 전송, Shift+Enter = 줄바꿈. 한글 조합 중 Enter는 글자 확정이라 보내지 않는다 (안 그러면 끝 글자가 잘리거나 두 번 간다) */
function onEnter(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229 || e.shiftKey) return;
  e.preventDefault();
  void ask();
}
async function ask() {
  const text = question.value.trim();
  if (!text || sending.value) return;
  archive();
  currentTask = undefined;
  clear();
  const g = generation;
  asked.value = w.context ? `${w.context}\n${text}` : text;
  question.value = "";
  void nextTick(() => { if (body.value) body.value.scrollTop = body.value.scrollHeight; });
  if (mode === "mock") {
    answer.value = "자유 질문은 실제 분석 API가 필요합니다. 업무 화면에서 준비된 시연 결과를 확인하세요.";
    status.value = "시연 안내"; return;
  }
  running.value = true; status.value = "접수 중"; sending.value = true;
  try {
    const { taskId } = await request<{ taskId: string }>("/ai/tasks", w.session?.token, "POST", { question: asked.value });
    if (g === generation) { currentTask = taskId; w.activeTask = taskId; }
  } catch (e) {
    // 접수에 실패하면 쓴 글을 돌려놓는다 — 다시 쓰지 않아도 되게
    if (g === generation) { err.value = (e as Error).message; running.value = false; question.value = text; }
  } finally {
    sending.value = false;
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
    <div ref="body" class="ai-body">
      <span class="eyebrow">선택한 업무</span>
      <select v-model="w.context" class="context" aria-label="선택한 업무">
        <option value="">선택 없음</option>
        <!-- 결품 항목처럼 목록에 없는 업무를 화면에서 눌렀을 때도 그대로 보이게 -->
        <option v-if="w.context && !knownContexts.has(w.context)" :value="w.context">{{ w.context }}</option>
        <optgroup v-for="g in contextGroups" :key="g.label" :label="g.label">
          <option v-for="o in g.options" :key="o.value" :value="o.value">{{ o.text }}</option>
        </optgroup>
      </select>
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
        <RouterLink v-if="w.approver" class="button primary wide" to="/solutions"
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
      <!-- 지난 대화 (decisions 6.36). 카드·근거는 [다시 열기]로 그 작업을 열어 본다 -->
      <div v-for="(t, i) in history" :key="i" class="chat-turn">
        <div v-if="t.question" class="chat-question">{{ t.question }}</div>
        <AnswerBlocks v-if="t.answer" :text="t.answer" />
        <p v-else class="caption">{{ t.status }}</p>
        <button v-if="t.taskId" class="text-button" @click="reopen(i)">이 작업 다시 열기 →</button>
      </div>
      <div v-if="asked" class="chat-question">{{ asked }}</div>
      <div v-if="status !== '분석 준비'" class="progress" role="status">
        {{ status }}
      </div>
      <AnswerBlocks :text="answer" />
      <div v-if="err" class="error">
        {{ err }}<button v-if="w.activeTask" @click="openExisting(w.activeTask)">현재 작업 다시 연결</button>
      </div>
      <p v-if="readOnlyTask && running" class="caption">요청자의 실시간 분석은 요청자 화면에서 볼 수 있습니다. 이곳에서는 완료 결과를 자동으로 불러옵니다.</p>
      <ServerResult
        v-if="displayedImpact"
        :result="displayedImpact"
        :title="result?.intent === 'ADJUST' && Array.isArray(result?.executions) && result.executions.length ? '실행 후 상태' : undefined"
      />
      <p v-if="result?.intent === 'ADJUST' && result.post_execution_impact === null" class="warning-box">실행 후 영향 결과를 확인하지 못했습니다. 업무 데이터를 다시 확인해 주세요.</p>
      <article v-for="p in Object.values(proposals).filter(x => !x.approvalId)" :key="p.proposalId" class="card">
        <b>{{ p.itemId }} · {{ labels[p.proposalStatus!] ?? p.proposalStatus }}</b><p>{{ p.summary }}</p><p>{{ p.message }}</p>
      </article>
      <template v-if="readOnlyTask && Array.isArray(result?.proposals)">
        <article v-for="p in result.proposals" :key="p.proposal_id" class="card">
          <b>{{ p.option?.item_id }} · {{ labels[p.status] ?? p.status }}</b><p>{{ p.option?.summary }}</p><p>{{ p.reason }}</p>
        </article>
      </template>
      <details v-if="docs.length" class="sources">
        <summary>근거 문서 {{ docs.length }}건 · 원문 보기</summary>
        <article v-for="d in docs" :key="d.uri" class="source">
          <b>{{ d.title ?? d.doc_id }}</b>
          <small
            >제{{ d.clause_no }}조 · {{ d.version
            }}<template v-if="d.effective_date"> · {{ d.effective_date }} 시행</template></small
          >
          <blockquote v-if="d.excerpt">{{ d.excerpt }}</blockquote>
          <code>{{ d.uri }}</code>
        </article>
      </details>
      <article
        v-for="e in events.filter((x) => x.type === 'APPROVAL_REQUIRED')"
        :key="e.approvalId"
        class="card"
      >
        <b>{{ cardLabel(e) }} · {{ e.itemId ?? e.approvalId }}</b>
        <p>{{ e.summary }}</p>
        <p v-if="progress(e)?.message">{{ progress(e)?.message }}</p>
        <!-- 요청자 검토 (decisions 6.33·6.34): 채팅창에서 바로 문제 정의·메모를 달아 팀장에게 보낼지 정한다 -->
        <form
          v-if="w.session?.role !== 'APPROVER' && cardState(e) === 'DRAFT'"
          class="draft-review"
          @submit.prevent="reviewCard(e, true)"
        >
          <label
            >문제 정의 · 필수<textarea
              :value="draftProblem[e.approvalId!] ?? e.problem ?? ''"
              rows="2"
              @input="draftProblem[e.approvalId!] = ($event.target as HTMLTextAreaElement).value"
          /></label>
          <label
            >메모 · 내 판단<textarea
              v-model="draftMemo[e.approvalId!]"
              rows="2"
              placeholder="예: 협력사와 구두로 선납 가능 확인함"
          /></label>
          <div class="action-row">
            <button type="button" :disabled="w.deciding[e.approvalId!]" @click="reviewCard(e, false)">보내지 않음</button
            ><button class="primary" :disabled="w.deciding[e.approvalId!] || !(draftProblem[e.approvalId!] ?? e.problem ?? '').trim()">
              {{ w.deciding[e.approvalId!] ? "보내는 중…" : "팀장에게 승인 요청" }}
            </button>
          </div>
        </form>
        <p v-else-if="w.session?.role !== 'APPROVER' && cardState(e) !== 'DRAFT'" class="caption">
          {{ w.data?.approvals.find((a) => a.id === e.approvalId)?.memo ? "보낸 메모: " + w.data?.approvals.find((a) => a.id === e.approvalId)?.memo : "" }}
        </p>
        <template v-if="cardStatus(e) === '승인 대기' && w.session?.role === 'APPROVER' && cardState(e) === 'PENDING'"
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
    </div>
    <form class="ai-input" @submit.prevent="ask">
      <label for="question">선택한 업무에 질문</label
      ><textarea
        id="question"
        v-model="question"
        placeholder="선택한 업무의 영향과 근거를 확인해줘. (Enter 전송 · Shift+Enter 줄바꿈)"
        @keydown.enter="onEnter"
        rows="3"
      />
      <div>
        <small>{{
          mode === "mock" ? "데모 · 자유 질문 미연결" : "업무 분석"
        }}</small
        ><button
          class="primary icon-button"
          :disabled="sending || !question.trim()"
          aria-label="분석 요청"
        >
          <ArrowUp :size="18" />
        </button>
      </div>
    </form>
  </aside>
</template>
