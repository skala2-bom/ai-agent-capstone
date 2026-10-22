<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useWorkspace } from "../stores/workspace";
import TaskReview from "../components/TaskReview.vue";
import ApprovalNote from "../components/ApprovalNote.vue";
import ProposalTitle from "../components/ProposalTitle.vue";
import ApprovalConfirm from "../components/ApprovalConfirm.vue";
import ProposalReview from "../components/ProposalReview.vue";
import TaskBatchActions from "../components/TaskBatchActions.vue";
import Drawer from "../components/Drawer.vue";
import Badge from "../components/Badge.vue";
import { mode, request } from "../services/api";
import type { Approval } from "../types";
const w = useWorkspace();
const selectedId = ref<string>();
const reason = ref("");
const confirmation = ref<{ id: string; decision: "APPROVED" | "REJECTED"; summary: string; reason: string }>();
const filter = ref("");
const pollError = ref("");
// 거절 뒤 AI 재검토 결과 (decisions 6.30): approvalId → {code, item_name, message, next_action, detail}
interface Outcome { code: string; item_name?: string; message: string; next_action: string; detail?: string | null }
const outcomes = ref<Record<string, Outcome>>({});
const outcomeLabel: Record<string, string> = {
  REPROPOSED: "재제안 생성", NO_ALTERNATIVE: "대안 없음", REVIEW_FAILED: "검토 실패", LIMIT: "재제안 한도",
  MANUAL: "직접 수정안 생성",
};
const outcomeTone: Record<string, string> = {
  REPROPOSED: "success", NO_ALTERNATIVE: "warning", REVIEW_FAILED: "danger", LIMIT: "warning",
  MANUAL: "success",
};
/** 거절 카드가 있는 작업만 진행 상태를 읽는다. 작업이 아직 안 끝나도 거절 건의 결과는 먼저 보인다 */
async function loadOutcomes() {
  if (mode !== "api" || !w.session) return;
  const tasks = [...new Set((w.data?.approvals ?? []).filter((a) => a.status === "REJECTED").map((a) => a.taskId))];
  for (const id of tasks) {
    const t = await request<{ progress?: { approvalId?: string; outcome?: Outcome }[] }>(
      `/ai/tasks/${encodeURIComponent(id)}/approval-progress`, w.session.token);
    for (const p of t.progress ?? []) if (p.approvalId && p.outcome) outcomes.value[p.approvalId] = p.outcome;
  }
}
const selected = computed(() => w.data?.approvals.find((a) => a.id === selectedId.value));
const visibleApprovals = computed(() => (w.data?.approvals ?? []).filter(a =>
  w.session?.role !== "APPROVER" || (a.status !== "DRAFT" && (a.status !== "WITHDRAWN" || w.batchStates[a.taskId]?.mode === "BATCH"))));
const rows = computed(() => visibleApprovals.value.filter((a) => !filter.value || a.status === filter.value));
const optionsById = computed(() => new Map(w.data?.options.map((o) => [o.id, o]) ?? []));
function approvalTitle(text = "") {
  return text.replace(/\s*\(실제 발주 변경입니다\.\s*협력사 협의·확약 여부를 확인한 뒤 승인하세요\.\)/g, "").trim();
}
function withReproposals(all: Approval[]) {
  const byId = new Map(all.map((a) => [a.id, a]));
  const children = new Map<string, Approval[]>();
  const childIds = new Set<string>();
  for (const a of all) {
    // 이전 요청 ID로 연결하고, ID 필드가 없는 옛 카드는 제목의 연결 정보를 읽는다.
    const previousId = a.previousApprovalId ?? optionsById.value.get(a.optionId)?.name
      .match(/^\[재제안 \d+차 · 이전 요청 ([^\]\s]+)\]/)?.[1];
    if (previousId && previousId !== a.id && byId.has(previousId)) {
      const siblings = children.get(previousId) ?? [];
      siblings.push(a);
      children.set(previousId, siblings);
      childIds.add(a.id);
    }
  }
  const ordered: Approval[] = [];
  const visited = new Set<string>();
  function append(a: Approval) {
    if (visited.has(a.id)) return;
    visited.add(a.id);
    ordered.push(a);
    for (const child of children.get(a.id) ?? []) append(child);
  }
  for (const a of all) if (!childIds.has(a.id)) append(a);
  // 잘못된 순환 참조가 있어도 카드가 누락되지 않도록 한다.
  for (const a of all) append(a);
  return ordered;
}
const groups = computed(() => {
  const approvals = visibleApprovals.value;
  return [...new Set(approvals.map((a) => a.taskId))].map((taskId) => {
    const all = approvals.filter((a) => a.taskId === taskId)
      .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
    const task = w.data?.history.find((h) => h.id === taskId);
    return {
      taskId,
      taskProblem: [...all].reverse().find(a => a.taskProblem)?.taskProblem,
      all,
      rows: withReproposals(all).filter((a) => !filter.value || a.status === filter.value),
      // 필터·재제안에 따라 작업 순서가 바뀌지 않도록 생성 시각을 기준으로 한다.
      createdAt: Date.parse(task?.at ?? "") || Date.parse(all[0]?.at ?? "") || 0,
    };
  }).filter((group) => group.rows.length)
    .sort((a, b) => b.createdAt - a.createdAt);
});
const option = computed(() => w.data?.options.find((o) => o.id === selected.value?.optionId));
function requestDecision(decision: "APPROVED" | "REJECTED") {
  if (!selected.value || !option.value || w.session?.role !== "APPROVER"
    || selected.value.status !== "PENDING" || w.deciding[selected.value.id]) return;
  if (decision === "REJECTED" && !reason.value.trim()) return;
  confirmation.value = { id: selected.value.id, decision, summary: approvalTitle(option.value.name), reason: reason.value };
}
async function decide() {
  const pending = confirmation.value;
  confirmation.value = undefined;
  if (!pending || w.session?.role !== "APPROVER") return;
  if (w.data?.approvals.find(a => a.id === pending.id)?.status !== "PENDING") return;
  await w.decide(pending.id, pending.decision, pending.reason);
}
let timer: ReturnType<typeof setTimeout> | undefined;
let mounted = false;
async function poll() {
  if (!mounted) return;
  if (document.visibilityState !== "hidden") {
    try { await w.refreshApprovals(); await loadOutcomes(); pollError.value = ""; }
    catch (e) { pollError.value = (e as Error).message; }
  }
  if (mounted) timer = setTimeout(poll, 3000);
}
onMounted(() => { mounted = true; void poll(); });
onBeforeUnmount(() => { mounted = false; clearTimeout(timer); });
</script>
<template>
  <p v-if="w.session?.role !== 'APPROVER'" class="caption">
    내가 요청한 건만 보입니다 · 승인·거절은 구매팀장이 합니다
  </p>
  <div class="toolbar">
    <select v-model="filter" aria-label="승인 상태 필터">
      <option value="">모든 상태</option>
      <option v-if="!w.approver" value="DRAFT">검토 전 (초안)</option>
      <option value="PENDING">승인 대기</option>
      <option value="APPROVED">실행 대기</option>
      <option value="REJECTED">거절</option>
      <option value="EXECUTED">실행 완료</option>
      <option v-if="!w.approver" value="WITHDRAWN">보내지 않음</option>
    </select>
  </div>
  <p v-if="pollError" class="error">승인 상태 갱신 실패: {{ pollError }}</p>
  <section class="card flush table-wrap">
    <table>
      <thead>
        <tr>
          <th>대상 / 요청</th>
          <th>요청자</th>
          <th>요청시각</th>
          <th>상태</th>
          <th>작업</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="group in groups" :key="group.taskId">
        <tr class="approval-group"><td colspan="5">
          <p v-if="group.taskProblem" class="task-problem"><strong>전체 문제 설명</strong><br />{{ group.taskProblem }}</p>
          <p v-else-if="!group.all.some(a => a.status === 'DRAFT')" class="caption">작성된 전체 문제 설명이 없습니다.</p>
          <p class="caption">{{ group.all.length }}건<template v-if="group.all.some(a => a.status === 'DRAFT')"> · 검토 전 {{ group.all.filter(a => a.status === 'DRAFT').length }}건</template> · 승인 대기 {{ group.all.filter(a => a.status === 'PENDING').length }}건 · 실행 완료 {{ group.all.filter(a => a.status === 'EXECUTED').length }}건</p>
          <button v-if="mode === 'api' && group.all.some(a => a.requesterId === w.session?.userId)" @click="w.openTask(group.taskId)">작업 결과</button>
          <TaskBatchActions :task-id="group.taskId" />
          <TaskReview v-if="mode === 'api' && w.session?.role === 'BUYER' && (group.all.some(a => a.status === 'DRAFT') || w.taskReviews[group.taskId]?.attempt)"
            :task-id="group.taskId" compact />
        </td></tr>
        <template v-for="a in group.rows" :key="a.id">
        <tr :class="{ reproposal: a.previousApprovalId }">
          <td>
            <ProposalTitle :text="optionsById.get(a.optionId)?.name" />
            <ApprovalNote v-if="a.status === 'DRAFT' && w.session?.role === 'BUYER' && mode === 'api'" :card="a" />
          </td>
          <td>{{ a.requester }}</td>
          <td>
            {{
              new Date(a.at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })
            }}
          </td>
          <td><ProposalReview v-if="w.batchStates[a.taskId]?.mode === 'BATCH'" :approval="a" compact /><Badge v-else :text="a.status" /></td>
          <td>
            <button
              @click="
                selectedId = a.id;
                reason = '';
              "
            >
              승인 상세
            </button>
          </td>
        </tr>
        <tr v-if="a.status === 'REJECTED' && outcomes[a.id]" class="outcome-row">
          <td colspan="5">
            <!-- 재제안이 생겼으면 바로 아래 카드가 설명이다. 한 줄만 (6.46 가독성) -->
            <div v-if="outcomes[a.id].code === 'REPROPOSED'" class="outcome success compact">↳ 재제안을 아래에 만들었습니다</div>
            <div v-else class="outcome" :class="outcomeTone[outcomes[a.id].code]">
              <b>↳ {{ outcomes[a.id].item_name }} · {{ outcomeLabel[outcomes[a.id].code] ?? outcomes[a.id].code }}</b>
              <p>{{ outcomes[a.id].message }}</p>
              <p><span>다음 행동</span> {{ outcomes[a.id].next_action }}</p>
              <small v-if="outcomes[a.id].detail">{{ outcomes[a.id].detail }}</small>
            </div>
          </td>
        </tr>
        </template>
        </template>
      </tbody>
    </table>
    <div v-if="!rows.length" class="empty">
      <h3>{{ w.approver ? "대기 중인 요청이 없습니다" : "내가 요청한 승인이 없습니다" }}</h3>
      <p v-if="mode === 'mock'">
        생산관리에서 1순위 선정 후, 구매 담당자가 발주 변경을 요청하면 이곳에
        표시됩니다.
      </p>
      <p v-else-if="w.session?.role === 'PLANNER'">
        발주 변경 요청은 구매 담당자가 합니다. 생산관리 담당자는 AI 답변의 해결안을 보고 구매에 요청하세요.
      </p>
      <p v-else>
        AI 채팅에서 "부족한 부품 해결 방법 만들어 줘"처럼 요청하면, AI가 검증한 해결안이 승인 카드로 여기 올라옵니다.
      </p>
    </div>
  </section>
  <Drawer
    v-if="selected && option"
    title="발주 변경 승인 상세"
    @close="selectedId = undefined"
    ><Badge v-if="w.batchStates[selected.taskId]?.mode !== 'BATCH'" :text="selected.status" />
    <h2><ProposalTitle :text="option.name" /></h2>
    <dl>
      <dt>요청자</dt>
      <dd>{{ selected.requester }}</dd>
      <dt>요청시각</dt>
      <dd>
        {{
          new Date(selected.at).toLocaleString("ko-KR", {
            timeZone: "Asia/Seoul",
          })
        }}
      </dd>
      <dt>대상 발주</dt>
      <dd>{{ option.po }}</dd>
      <!-- 사유가 제목 문장과 같으면 위 제목으로 충분하다 (6.46 가독성) -->
      <template v-if="approvalTitle(option.reason) !== approvalTitle(option.name)"
        ><dt>선정 사유</dt>
        <dd>{{ approvalTitle(option.reason) }}</dd></template
      >
      <template v-if="option.role !== 'PLANNER'"
        ><dt>추가비용</dt>
        <dd>{{ option.addedCost.toLocaleString() }}원</dd></template
      >
    </dl>
    <!-- 요청자가 쓴 문제 정의·메모 (초안이 아니면 팀장·요청자 모두 본다) -->
    <div v-if="selected.problem || selected.taskProblem || selected.memo" class="card request-note">
      <h3>요청 내용</h3>
      <dl>
        <template v-if="selected.taskProblem"><dt>전체 문제 설명</dt><dd>{{ selected.taskProblem }}</dd></template>
        <template v-if="selected.problem"><dt>품목별 문제</dt><dd>{{ selected.problem }}</dd></template>
        <template v-if="selected.memo"><dt>{{ selected.previousApprovalId ? "이전 요청의 구매 메모" : "메모" }}</dt><dd>{{ selected.memo }}</dd></template>
      </dl>
    </div>
    <h3>변경 전후</h3>
    <template v-if="option.action === 'split'"
      ><div class="split-original">
        <small>원 발주 · {{ option.po }}</small
        ><b>{{ selected.beforeQty }}개 · {{ selected.beforeDate }}</b>
      </div>
      <div class="split-lines">↓ 분할</div>
      <div class="two-column">
        <div class="split-child">
          <small>원 발주 잔량</small
          ><b>{{ (selected.beforeQty ?? 0) - option.qty }}개</b
          ><span>{{ selected.beforeDate }}</span>
        </div>
        <div class="split-child emphasized">
          <small>신규 분할 발주</small><b>{{ option.qty }}개</b
          ><span>{{ option.date }}</span>
        </div>
      </div></template
    >
    <div v-else class="card">
      <template v-if="option.action === 'cancel'">{{ option.po }} · 발주 취소</template>
      <template v-else-if="option.action === 'qty'">{{ option.po }} · {{ selected.beforeQty }}개 → {{ option.qty }}개<br />입고일 {{ option.date }} 유지</template>
      <template v-else-if="option.action === 'manual'">{{ option.po }} · {{ selected.beforeQty }}개 → {{ option.qty }}개<br />입고일 {{ selected.beforeDate }} → {{ option.date }}</template>
      <template v-else>{{ option.po }} · {{ option.qty }}개<br />입고일 {{ selected.beforeDate }} → {{ option.date }}</template>
    </div>
    <!-- API 모드: AI가 검색해 실제로 읽은 조항 (승인 카드의 evidence). AI 패널 근거 카드와 같은 모양 -->
    <details v-if="mode === 'api'" open class="evidence sources">
      <summary>근거 문서 {{ selected.evidence?.length ?? 0 }}건</summary>
      <article v-for="(d, index) in selected.evidence" :key="d.uri ?? index" class="source">
        <b>{{ d.title ?? d.doc_id }}</b>
        <small
          >제{{ d.clause_no }}조 · {{ d.version
          }}<template v-if="d.effective_date"> · {{ d.effective_date }} 시행</template></small
        >
        <blockquote v-if="d.excerpt ?? d.content">{{ d.excerpt ?? d.content }}</blockquote>
        <code>{{ d.uri }}</code>
      </article>
      <p v-if="!selected.evidence?.length" class="caption">서버가 제공한 근거가 없습니다.</p>
      <p v-if="option.role !== 'PLANNER' && option.costEvidence">{{ option.costEvidence }}</p>
    </details>
    <p v-else class="caption">시연용 승인 카드입니다.</p>
    <p v-if="selected.status === 'DRAFT'" class="caption">전체 문제 설명과 개인 메모는 작업 카드에서 작성하고, 작업 단위로 전송해 주세요.</p>
    <ProposalReview v-if="w.batchStates[selected.taskId]?.mode === 'BATCH'" :approval="selected" />
    <form
      v-else-if="selected.status === 'PENDING' && w.session?.role === 'APPROVER' && (mode === 'mock' || w.batchStates[selected.taskId]?.mode === 'LEGACY')"
      @submit.prevent="requestDecision('REJECTED')"
    >
      <label
        >거절 사유 · 거절 시 필수<textarea
          v-model="reason"
          rows="3"
          placeholder="대안 재검토에 필요한 사유"
        />
      </label>
      <div class="action-row">
        <button :disabled="w.deciding[selected.id] || !reason.trim()">거절 · 대안 재요청</button
        ><button
          type="button"
          class="primary"
          :disabled="w.deciding[selected.id]"
          @click="requestDecision('APPROVED')"
        >
          {{ w.deciding[selected.id] ? "접수 중…" : "승인 접수" }}
        </button>
      </div>
    </form>
    <div v-if="selected.status === 'APPROVED'">
      <p class="warning-box">
        승인은 접수되었습니다. 실행 완료가 확인되면 자동 갱신됩니다.
      </p>
      <button
        v-if="mode === 'mock' && w.session?.role === 'APPROVER'"
        :disabled="w.deciding[selected.id]"
        @click="
          async () => {
            if (await w.save({ type: 'execution', id: selected!.id }))
              selectedId = undefined;
          }
        "
      >
        시연: 실행 완료 이벤트 수신
      </button>
    </div>
    <p v-if="selected.status === 'REJECTED' && w.batchStates[selected.taskId]?.mode !== 'BATCH'">
      거절 사유: {{ selected.reason }}<br />{{
        mode === "api"
          ? "AI가 기존 후보 중 다른 대안을 검토합니다. 가능한 안이 없으면 보류하며, 수량을 임의로 고치지는 않습니다."
          : "보완 대안이 생성되었습니다. 구매 검수부터 다시 진행하세요."
      }}
    </p>
    <p v-if="selected.status === 'EXECUTED'">발주 변경이 실행되었습니다.</p>
    <p v-if="w.error" class="error">{{ w.error }}</p></Drawer
  >
  <ApprovalConfirm
    v-if="confirmation"
    :decision="confirmation.decision"
    :summary="confirmation.summary"
    :reason="confirmation.reason"
    @cancel="confirmation = undefined"
    @confirm="decide"
  />
</template>
<style scoped>
.approval-group > td {
  background: #f9fcf6;
  border-top: 1px solid #e9eee4;
  border-bottom: 1px solid #e9eee4;
}
</style>

<style scoped>
.task-problem, .request-note dd { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
