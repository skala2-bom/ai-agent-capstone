<script setup lang="ts">
import { ref } from "vue";
import { useWorkspace } from "../stores/workspace";
import type { OptionView, Grade } from "../types";
import { selectable } from "../services/workflow";
import Badge from "../components/Badge.vue";
import Drawer from "../components/Drawer.vue";
import { mode } from "../services/api";
import { environments } from "../mocks/environments";
const w = useWorkspace();
const selected = ref<OptionView>();
const grade = ref<Grade>("B"),
  note = ref(""),
  priority = ref(1),
  reason = ref("");
function open(o: OptionView) {
  selected.value = o;
  grade.value = o.grade ?? "B";
  note.value = o.note ?? "";
  priority.value = o.priority ?? 1;
  reason.value = o.reason ?? "";
  w.context = `${o.id} · ${o.name}`;
}
async function review(confirm: boolean) {
  if (
    await w.save({
      type: "review",
      id: selected.value!.id,
      grade: grade.value,
      note: note.value,
      confirm,
    })
  )
    selected.value = undefined;
}
async function prioritize() {
  if (
    await w.save({
      type: "priority",
      id: selected.value!.id,
      priority: priority.value,
      reason: reason.value,
    })
  )
    selected.value = undefined;
}
</script>
<template>
  <div class="section-head">
    <h2>
      {{ mode === "api" ? "현재 상태" : environments.find((e) => e.id === w.scenario)?.label }} · 해결안
      <span class="count">{{ w.data?.options.length }}</span>
    </h2>
    <span class="caption">{{ w.data?.options.length }}개 후보</span>
  </div>
  <section class="card flush">
    <div class="table-wrap">
      <table class="options-table">
        <thead>
          <tr>
            <th>해결 방법 / 변경 내용</th>
            <th>생산 영향 · 잔여 위험</th>
            <th>구매 검수</th>
            <th class="center">확정 등급</th>
            <th>생산 우선순위</th>
            <th>작업</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="o in w.data?.options"
            :key="o.id"
            :class="{ chosen: o.priority === 1 }"
          >
            <td>
              <b>{{ o.name }}</b
              ><small>{{ o.id }} · v{{ o.revision }} · {{ o.po }}</small>
              <p>{{ o.qty }}개 · {{ o.date }}</p>
              <Badge :text="o.confirmed ? '협력사 확약' : '확약 필요'" />
            </td>
            <td>
              <Badge
                :text="
                  o.verification === 'PENDING'
                    ? '영향 확인 필요'
                    : o.resolves
                      ? '결품 해소'
                      : '결품 미해소'
                "
              />
              <p>{{ o.impact }}</p>
              <small>{{ o.risk }}</small>
            </td>
            <td>
              <Badge :text="mode === 'api' ? '시스템 계산' : o.review" /><small>{{
                mode === "api" ? "평가지표 기준" : o.reviewRevision ? "검수 v" + o.reviewRevision : "검수 전"
              }}</small
              ><template v-if="o.role !== 'PLANNER'"
                ><p class="num">{{ o.addedCost.toLocaleString() }}원</p>
                <small>추가비용 총액</small></template
              >
            </td>
            <td class="center">
              <span class="grade">{{
                o.review === "확정" ? o.grade : "—"
              }}</span>
            </td>
            <td>
              <b :class="{ teal: o.priority === 1 }">{{
                o.priority ? o.priority + "순위" : "미지정"
              }}</b
              ><small>{{ o.reason || (mode === "api" ? "추천 아님" : "생산관리 결정 대기") }}</small>
            </td>
            <td>
              <button @click="open(o)">
                {{ w.buyer ? "검수 상세" : "선정 상세" }}</button
              ><button
                v-if="w.buyer && o.priority === 1 && o.action !== 'plan'"
                class="primary"
                :disabled="
                  w.busy ||
                  w.data?.approvals.some(
                    (a) => a.optionId === o.id && a.status !== 'REJECTED',
                  )
                "
                @click="w.save({ type: 'request', id: o.id })"
              >
                승인 요청
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="!w.data?.options.length" class="empty">
      {{
        mode === "mock"
          ? "현재 환경에는 검토할 해결안이 없습니다."
          : "지금 발주로 풀어야 할 결품·과잉이 없습니다. 생산계획을 바꾸거나 협력사 지연을 입력하면 여기에 해결안이 나옵니다."
      }}
    </div>
  </section>
  <Drawer
    v-if="selected"
    :title="w.buyer ? '해결안 비용등급 검수' : '생산 우선순위 결정'"
    @close="selected = undefined"
    ><span class="eyebrow"
      >{{ selected.id }} · 후보 v{{ selected.revision }}</span
    >
    <h2>{{ selected.name }}</h2>
    <p>{{ selected.impact }}</p>
    <p>{{ selected.risk }}</p>
    <div class="card" v-if="selected.role !== 'PLANNER'">
      <h3>비용 근거</h3>
      <p>{{ selected.costEvidence }}</p>
      <dl>
        <dt>추가비용 총액</dt>
        <dd>{{ selected.addedCost.toLocaleString() }}원</dd>
        <template v-if="selected.role === 'APPROVER' && mode === 'mock'"
          ><dt>계약 단가</dt>
          <dd>{{ selected.unitPrice.toLocaleString() }}원</dd></template
        >
      </dl>
      <small v-if="mode === 'mock'"
        >시연 견적 · 가상 공급계약 v1 / 5.1: 분할 배송은 별도 견적 확인 및
        승인이 필요합니다.</small
      >
    </div>
    <!-- API 모드: 등급·추천은 시스템이 계산한 값이다. 사람의 검수·우선순위 입력은 목데이터 모드에만 있다 (decisions 5절 14번) -->
    <div v-if="mode === 'api'" class="card">
      <h3>계산 결과</h3>
      <dl>
        <dt>해결 여부</dt>
        <dd>{{ selected.resolves ? "대상 문제 해소 · 다른 생산 영향 없음" : "해소 못 함" }}</dd>
        <dt>영향</dt>
        <dd>{{ selected.impact }}</dd>
        <dt>비용 등급</dt>
        <dd>{{ selected.grade }}</dd>
        <dt>추천</dt>
        <dd>{{ selected.priority === 1 ? selected.reason : "추천 아님" }}</dd>
      </dl>
      <p class="caption">
        실행은 이 화면에서 하지 않습니다. "승인 요청"을 누르면 AI가 다시 검증한 뒤 승인함에 카드를 올립니다.
      </p>
    </div>
    <form v-else-if="w.buyer" @submit.prevent="review(true)">
      <label
        >사내 비용등급 · 직접 평가<select v-model="grade">
          <option>A</option>
          <option>B</option>
          <option>C</option>
          <option>D</option>
        </select></label
      ><label
        >검수 근거 / 보완 사유<textarea
          v-model="note"
          required
          rows="4"
          placeholder="견적과 비용명세를 확인한 근거를 작성하세요."
        />
      </label>
      <div class="action-row">
        <button
          type="button"
          :disabled="w.busy || !note.trim()"
          @click="review(false)"
        >
          보완 요청</button
        ><button class="primary" :disabled="w.busy || !note.trim()">
          검수 확정
        </button>
      </div>
    </form>
    <form v-else-if="mode === 'mock'" @submit.prevent="prioritize">
      <p>
        확정 비용등급 <span class="grade">{{ selected.grade || "—" }}</span>
      </p>
      <p v-if="!selectable(selected)" class="warning-box">
        현재 버전의 검수 확정과 결품 해소가 필요합니다.
      </p>
      <label
        >생산 우선순위<select v-model.number="priority">
          <option :value="1">1순위 · 실행안 선정</option>
          <option :value="2">2순위 · 예비 대안</option>
          <option :value="3">3순위</option>
        </select></label
      ><label
        >선택 사유<textarea
          v-model="reason"
          required
          rows="4"
          placeholder="납기, 다른 제품 영향과 잔여 위험을 고려한 사유"
        /></label
      ><button
        class="primary"
        :disabled="w.busy || !selectable(selected) || !reason.trim()"
      >
        {{ w.busy ? "저장 중…" : "우선순위 저장" }}
      </button>
    </form>
    <p v-if="w.error" class="error">{{ w.error }}</p></Drawer
  >
</template>
