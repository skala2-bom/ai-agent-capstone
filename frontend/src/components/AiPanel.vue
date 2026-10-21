<script setup lang="ts">
import { ref, onBeforeUnmount, computed, watch } from "vue";
import ServerResult from "./ServerResult.vue";
import { segments, sourceCards } from "../services/answer";
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
const result = ref<unknown>();
const sources = ref<unknown>();
const blocks = computed(() => segments(answer.value));
const docs = computed(() => sourceCards(sources.value));
// 문서 질문(QA)은 impact가 null이다. 그때 빈 "영향 결과"를 그리지 않는다
const hasImpact = computed(() => {
  const r = result.value as Record<string, unknown> | undefined;
  return !!r && (r.intent !== "QA" || !!r.impact);
});
const approvalReasons = ref<Record<string, string>>({});
const approvalStates = ref<Record<string, string>>({});
const submitting = ref(false);
let stop = () => {};
let generation = 0;
const sc = computed(() => scenarios.find((s) => s.id === w.scenario)!);
async function decide(e: AgentEvent, decision: "APPROVED" | "REJECTED") {
  if (submitting.value) return;
  const reason = approvalReasons.value[e.approvalId!] ?? "";
  if (decision === "REJECTED" && !reason.trim()) return;
  submitting.value = true;
  try {
    await request(
      `/ai/tasks/${encodeURIComponent(e.taskId)}/approval`,
      w.session?.token,
      "POST",
      { approvalId: e.approvalId, decision, reason: reason || null },
    );
    approvalStates.value[e.approvalId!] =
      decision === "APPROVED"
        ? "승인 접수 · 실행 결과 대기"
        : "거절 접수 · 대안 재분석 대기";
  } catch (error) {
    err.value = (error as Error).message;
  } finally {
    submitting.value = false;
  }
}
function clear() {
  generation++;
  approvalReasons.value = {};
  approvalStates.value = {};
  stop();
  running.value = false;
  answer.value = "";
  result.value = undefined;
  sources.value = undefined;
  events.value = [];
  status.value = "분석 준비";
  err.value = "";
}
watch(
  () => w.context,
  () => {
    clear();
    question.value = "";
  },
);
window.addEventListener("close-agent", clear);
onBeforeUnmount(() => {
  clear();
  window.removeEventListener("close-agent", clear);
});
async function ask() {
  clear();
  const g = generation;
  if (!question.value.trim()) return;
  if (mode === "mock") {
    answer.value =
      "자유 질문은 실제 분석 API가 필요합니다. 아래 선택된 시연 결과를 확인하거나 업무 화면에서 준비된 변경을 미리 보세요.";
    status.value = "시연 안내";
    return;
  }
  running.value = true;
  status.value = "접수 중";
  try {
    const { taskId } = await request<{ taskId: string }>(
      "/ai/tasks",
      w.session?.token,
      "POST",
      { question: `${w.context}\n${question.value}` },
    );
    if (g !== generation) return;
    const acc = new EventAccumulator(taskId);
    status.value = "접수";
    stop = connect(
      taskId,
      w.session?.token,
      (e) => {
        if (g !== generation || !acc.accept(e)) return;
        events.value = [...acc.events];
        answer.value = acc.answer;
        status.value = acc.status;
        if (e.type === "DONE") {
          result.value = e.result;
          sources.value = e.sources;
        }
        if (e.type === "FAILED")
          err.value = e.message ?? "분석에 실패했습니다.";
        if (acc.terminal) {
          running.value = false;
          stop();
        }
      },
      (m) => {
        err.value = m;
      },
    );
  } catch (e) {
    if (g === generation) {
      err.value = (e as Error).message;
      running.value = false;
    }
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
      <div v-if="status !== '분석 준비'" class="progress" role="status">
        {{ status }}
      </div>
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
      <div v-if="err" class="error">
        {{ err }}<button @click="ask">다시 분석</button>
      </div>
      <ServerResult v-if="hasImpact" :result="result" />
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
        <b>승인 대기 · {{ e.approvalId }}</b>
        <p>{{ e.summary }}</p>
        <p v-if="approvalStates[e.approvalId!]">
          {{ approvalStates[e.approvalId!] }}
        </p>
        <template v-else-if="w.session?.role === 'APPROVER'"
          ><label
            >거절 사유<textarea
              v-model="approvalReasons[e.approvalId!]"
              rows="2"
            /></label
          ><button
            :disabled="submitting || !approvalReasons[e.approvalId!]?.trim()"
            @click="decide(e, 'REJECTED')"
          >
            거절</button
          ><button :disabled="submitting" @click="decide(e, 'APPROVED')">
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
