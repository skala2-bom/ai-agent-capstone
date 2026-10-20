<script setup lang="ts">
import { ref, computed } from "vue";
import { useWorkspace } from "../stores/workspace";
import type { Approval } from "../types";
import Drawer from "../components/Drawer.vue";
import Badge from "../components/Badge.vue";
import { mode } from "../services/api";
const w = useWorkspace(),
  selected = ref<Approval>(),
  reason = ref(""),
  filter = ref("");
const rows = computed(() =>
  w.data!.approvals.filter((a) => !filter.value || a.status === filter.value),
);
const option = computed(() =>
  w.data!.options.find((o) => o.id === selected.value?.optionId),
);
async function decide(decision: "APPROVED" | "REJECTED") {
  if (
    await w.save({
      type: "approval",
      id: selected.value!.id,
      decision,
      reason: reason.value,
    })
  )
    selected.value = undefined;
}
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
        <tr v-for="a in rows" :key="a.id">
          <td>
            <b>{{ w.data?.options.find((o) => o.id === a.optionId)?.name }}</b
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
                selected = a;
                reason = '';
              "
            >
              승인 상세
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="!rows.length" class="empty">
      <h3>대기 중인 요청이 없습니다</h3>
      <p>
        생산관리에서 1순위 선정 후, 구매 담당자가 발주 변경을 요청하면 이곳에
        표시됩니다.
      </p>
      <RouterLink class="button" to="/solutions"
        >해결안 비교로 이동 →</RouterLink
      >
    </div>
  </section>
  <Drawer
    v-if="selected && option"
    title="발주 변경 승인 상세"
    @close="selected = undefined"
    ><Badge :text="selected.status" />
    <h2>{{ option.name }}</h2>
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
      <dd>{{ option.reason }}</dd>
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
      {{ option.po }} → {{ option.qty }}개 · {{ option.date }}
      <p>{{ option.impact }}</p>
    </div>
    <details open class="evidence">
      <summary>근거 확인 · 가상 공급계약 v1 / 5.1</summary>
      <p>
        분할 납품은 협력사 확약과 별도 비용 검토 후 승인받아 실행합니다. (시연용
        원문)
      </p>
      <p v-if="option.role !== 'PLANNER'">{{ option.costEvidence }}</p>
    </details>
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
        <button :disabled="w.busy || !reason.trim()">거절 · 대안 재요청</button
        ><button
          type="button"
          class="primary"
          :disabled="w.busy"
          @click="decide('APPROVED')"
        >
          {{ w.busy ? "접수 중…" : "승인 접수" }}
        </button>
      </div>
    </form>
    <div v-if="selected.status === 'APPROVED'">
      <p class="warning-box">
        승인은 접수되었으며 발주는 아직 변경되지 않았습니다.
      </p>
      <button
        v-if="mode === 'mock' && w.session?.role === 'APPROVER'"
        :disabled="w.busy"
        @click="
          async () => {
            if (await w.save({ type: 'execution', id: selected!.id }))
              selected = undefined;
          }
        "
      >
        시연: 실행 완료 이벤트 수신
      </button>
    </div>
    <p v-if="selected.status === 'REJECTED'">
      거절 사유: {{ selected.reason }}<br />보완 대안이 생성되었습니다. 구매
      검수부터 다시 진행하세요.
    </p>
    <p v-if="w.error" class="error">{{ w.error }}</p></Drawer
  >
</template>
