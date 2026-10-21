<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useWorkspace } from "../stores/workspace";
import Drawer from "../components/Drawer.vue";
import Badge from "../components/Badge.vue";
import { mode } from "../services/api";
import type { Approval } from "../types";
const w = useWorkspace();
const selectedId = ref<string>();
const reason = ref("");
const filter = ref("");
const pollError = ref("");
const selected = computed(() => w.data?.approvals.find((a) => a.id === selectedId.value));
const rows = computed(() => (w.data?.approvals ?? []).filter((a) => !filter.value || a.status === filter.value));
const optionsById = computed(() => new Map(w.data?.options.map((o) => [o.id, o]) ?? []));
function approvalTitle(text = "") {
  return text.replace(/\s*\(실제 발주 변경입니다\.\s*협력사 협의·확약 여부를 확인한 뒤 승인하세요\.\)/g, "").trim();
}
function withReproposals(all: Approval[]) {
  const byId = new Map(all.map((a) => [a.id, a]));
  const children = new Map<string, Approval[]>();
  const childIds = new Set<string>();
  for (const a of all) {
    // 승인 API는 이전 요청 ID를 summary에 담으므로, 품목명이 아닌 정확한 요청으로 연결한다.
    const previousId = optionsById.value.get(a.optionId)?.name
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
  const approvals = w.data?.approvals ?? [];
  return [...new Set(approvals.map((a) => a.taskId))].map((taskId) => {
    const all = approvals.filter((a) => a.taskId === taskId)
      .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
    const task = w.data?.history.find((h) => h.id === taskId);
    return {
      taskId,
      all,
      rows: withReproposals(all).filter((a) => !filter.value || a.status === filter.value),
      title: task?.title ?? `발주 변경 요청 · ${taskId.slice(0, 8)}`,
      // 필터·재제안에 따라 작업 순서가 바뀌지 않도록 생성 시각을 기준으로 한다.
      createdAt: Date.parse(task?.at ?? "") || Date.parse(all[0]?.at ?? "") || 0,
    };
  }).filter((group) => group.rows.length)
    .sort((a, b) => b.createdAt - a.createdAt);
});
const option = computed(() => w.data?.options.find((o) => o.id === selected.value?.optionId));
async function decide(decision: "APPROVED" | "REJECTED") {
  if (selected.value) await w.decide(selected.value.id, decision, reason.value);
}
let timer: ReturnType<typeof setTimeout> | undefined;
let mounted = false;
async function poll() {
  if (!mounted) return;
  if (document.visibilityState !== "hidden") {
    try { await w.refreshApprovals(); pollError.value = ""; }
    catch (e) { pollError.value = (e as Error).message; }
  }
  if (mounted) timer = setTimeout(poll, 3000);
}
onMounted(() => { mounted = true; void poll(); });
onBeforeUnmount(() => { mounted = false; clearTimeout(timer); });
</script>
<template>
  <p v-if="w.session?.role !== 'APPROVER'" class="caption">
    조회 전용 · 승인 권한: 구매팀장
  </p>
  <div class="toolbar">
    <select v-model="filter" aria-label="승인 상태 필터">
      <option value="">모든 상태</option>
      <option value="PENDING">승인 대기</option>
      <option value="APPROVED">실행 대기</option>
      <option value="REJECTED">거절</option>
      <option value="EXECUTED">실행 완료</option>
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
          <b>{{ group.title }}</b>
          <p class="caption">{{ group.all.length }}건 · 승인 대기 {{ group.all.filter(a => a.status === 'PENDING').length }}건 · 실행 완료 {{ group.all.filter(a => a.status === 'EXECUTED').length }}건</p>
          <button v-if="mode === 'api'" @click="w.openTask(group.taskId)">작업 결과</button>
        </td></tr>
        <tr v-for="a in group.rows" :key="a.id">
          <td>
            <b>{{ approvalTitle(optionsById.get(a.optionId)?.name) }}</b
            ><small>{{
              w.data?.options.find((o) => o.id === a.optionId)?.item
            }}</small>
          </td>
          <td>{{ a.requester }}</td>
          <td>
            {{
              new Date(a.at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })
            }}
          </td>
          <td><Badge :text="a.status" /></td>
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
        </template>
      </tbody>
    </table>
    <div v-if="!rows.length" class="empty">
      <h3>대기 중인 요청이 없습니다</h3>
      <p>
        구매 담당자가 AI에 해결 방법을 요청하면 검증된 부품별 변경안이 이곳에 표시됩니다.
      </p>
      <RouterLink class="button" to="/solutions"
        >해결안 비교로 이동 →</RouterLink
      >
    </div>
  </section>
  <Drawer
    v-if="selected && option"
    title="발주 변경 승인 상세"
    @close="selectedId = undefined"
    ><Badge :text="selected.status" />
    <h2>{{ approvalTitle(option.name) }}</h2>
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
      <dt>선정 사유</dt>
      <dd>{{ approvalTitle(option.reason) }}</dd>
      <template v-if="option.role !== 'PLANNER'"
        ><dt>추가비용</dt>
        <dd>{{ option.addedCost.toLocaleString() }}원</dd></template
      >
    </dl>
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
      <template v-else>{{ option.po }} · {{ option.qty }}개<br />입고일 {{ selected.beforeDate }} → {{ option.date }}</template>
    </div>
    <template v-if="mode === 'api'">
      <details v-for="(source, index) in selected.evidence" :key="source.uri ?? index" open class="evidence">
        <summary>{{ source.title }} {{ source.version }} · {{ source.clause_no }}</summary>
        <p>{{ source.excerpt ?? source.content }}</p><small>{{ source.uri }}</small>
      </details>
      <p v-if="!selected.evidence?.length" class="caption">서버가 제공한 근거가 없습니다.</p>
    </template>
    <p v-else class="caption">시연용 승인 카드입니다.</p>
    <form
      v-if="selected.status === 'PENDING' && w.session?.role === 'APPROVER'"
      @submit.prevent="decide('REJECTED')"
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
          @click="decide('APPROVED')"
        >
          {{ w.deciding[selected.id] ? "접수 중…" : "승인 접수" }}
        </button>
      </div>
    </form>
    <div v-if="selected.status === 'APPROVED'">
      <p class="warning-box">
        승인은 접수되었습니다. 실행 완료가 확인되면 자동 갱신됩니다. 오래 대기하면 작업 결과에서 보류 여부를 확인하세요.
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
    <p v-if="selected.status === 'REJECTED'">
      거절 사유: {{ selected.reason }}<br />{{
        mode === "api"
          ? "AI가 기존 후보 중 다른 대안을 검토합니다. 가능한 안이 없으면 보류하며, 수량을 임의로 고치지는 않습니다."
          : "보완 대안이 생성되었습니다. 구매 검수부터 다시 진행하세요."
      }}
    </p>
    <p v-if="selected.status === 'EXECUTED'">발주 변경이 실행되었습니다.</p>
    <p v-if="w.error" class="error">{{ w.error }}</p></Drawer
  >
</template>
<style scoped>
.approval-group > td {
  background: #f9fcf6;
  border-top: 1px solid #e9eee4;
  border-bottom: 1px solid #e9eee4;
}
</style>
