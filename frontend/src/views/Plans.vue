<script setup lang="ts">
import { ref, computed } from "vue";
import { useWorkspace } from "../stores/workspace";
import type { Plan } from "../types";
import Drawer from "../components/Drawer.vue";
import Badge from "../components/Badge.vue";
import PlanCalendar from "../components/PlanCalendar.vue";
import IssueTable from "../components/IssueTable.vue";
import { scenarios } from "../mocks/fixtures";
import { mode, request, toIssues } from "../services/api";
const w = useWorkspace();
const q = ref(""),
  status = ref(""),
  product = ref(""),
  from = ref(""),
  to = ref(""),
  view = ref("캘린더");
const selected = ref<Plan>();
const date = ref(""),
  qty = ref(1),
  preview = ref(""),
  previewError = ref("");
const rows = computed(() =>
  w.data!.plans.filter(
    (p) =>
      (p.product + p.id).includes(q.value) &&
      (!product.value || p.product === product.value) &&
      (!status.value || p.status === status.value) &&
      (!from.value || p.date >= from.value) &&
      (!to.value || p.date <= to.value),
  ),
);
// API 모드는 실제 계산 결과, 목데이터 모드는 준비된 시연 결과
const apiResult = ref<{ issues: ReturnType<typeof toIssues>; policy_version: string }>();
const result = computed(() =>
  mode === "api" ? apiResult.value : scenarios.find((s) => s.id === preview.value),
);
function open(p: Plan) {
  selected.value = p;
  date.value = p.date;
  qty.value = p.qty;
  preview.value = "";
  previewError.value = "";
  apiResult.value = undefined;
  w.context = `${p.id} · ${p.product}`;
}
function simulate() {
  preview.value = "";
  previewError.value = "";
  const p = selected.value!;
  if (mode !== "mock") {
    // 바꾸려는 값만 가정 변경(whatif)으로 넣어 계산한다. 저장 전이라 DB는 그대로다 (contracts 2.2)
    apiResult.value = undefined;
    const whatif = [
      ...(date.value !== p.date ? [{ op: "reschedule_plan", plan_id: p.id, new_date: date.value }] : []),
      ...(qty.value !== p.qty ? [{ op: "change_plan_qty", plan_id: p.id, new_qty: qty.value }] : []),
    ];
    request<Record<string, unknown>>("/analysis/impact", w.session?.token, "POST", { whatif })
      .then((r) => {
        apiResult.value = {
          issues: toIssues(r, w.data!.items, w.data!.plans),
          policy_version: String(r.policy_version ?? ""),
        };
      })
      .catch((e) => (previewError.value = (e as Error).message));
    return;
  }
  if (w.data?.analysisStale) {
    previewError.value = "변경된 업무 데이터의 재분석 API가 필요합니다.";
    return;
  }
  if (
    ["SC0", "SC1"].includes(w.scenario) &&
    p.id === "PP-05" &&
    date.value === "2026-11-25" &&
    qty.value === 400
  )
    preview.value = "SC1";
  else if (
    ["SC0", "SC3"].includes(w.scenario) &&
    p.id === "PP-02" &&
    date.value === "2027-01-04" &&
    qty.value === 800
  )
    preview.value = "SC3";
  else if (date.value === p.date && qty.value === p.qty)
    preview.value = w.scenario;
  else
    previewError.value =
      "준비되지 않은 변경입니다. 실제 분석 API가 필요하며 예시 결과로 대신하지 않습니다.";
}
async function save() {
  if (
    await w.save({
      type: "plan",
      id: selected.value!.id,
      date: date.value,
      qty: qty.value,
    })
  )
    selected.value = undefined;
}
</script>
<template>
  <div class="toolbar">
    <input
      v-model="q"
      aria-label="제품 또는 계획 검색"
      placeholder="제품명 또는 계획번호 검색"
    /><select v-model="product" aria-label="제품 필터">
      <option value="">모든 제품</option>
      <option
        v-for="name in [...new Set(w.data!.plans.map((p) => p.product))]"
        :key="name"
      >
        {{ name }}
      </option></select
    ><select v-model="status" aria-label="계획 상태">
      <option value="">모든 상태</option>
      <option>확정</option>
      <option>검토</option></select
    ><input v-model="from" type="date" aria-label="시작일" /><span>—</span
    ><input v-model="to" type="date" aria-label="종료일" /><button
      @click="view = view === '표' ? '캘린더' : '표'"
    >
      {{ view === "표" ? "캘린더 보기" : "목록 보기" }}
    </button>
  </div>
  <section class="card flush">
    <div v-if="view === '표'" class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>생산계획 / 제품</th>
            <th>생산일</th>
            <th class="num">생산수량</th>
            <th>상태</th>
            <th>상세</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in rows" :key="p.id">
            <td>
              <b>{{ p.product }}</b
              ><small>{{ p.id }}</small>
            </td>
            <td>{{ p.date }}</td>
            <td class="num">{{ p.qty.toLocaleString() }}대</td>
            <td><Badge :text="p.status" /></td>
            <td><button @click="open(p)">계획 상세 →</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <PlanCalendar
      v-else
      :plans="rows"
      :total="w.data!.plans.length"
      :purchases="w.data!.purchases"
      @select="open"
      @receipt="
        (p) => {
          w.context = `${p.item} · ${p.date} 입고`;
          w.panel = true;
        }
      "
    />
    <div v-if="!rows.length" class="empty">
      조건에 맞는 생산계획이 없습니다.
    </div>
    <footer class="table-footer">
      총 {{ rows.length }}건 · 날짜는 연도를 포함합니다.
    </footer>
  </section>
  <Drawer v-if="selected" title="생산계획 변경" @close="selected = undefined"
    ><span class="eyebrow">{{ selected.id }}</span>
    <h2>{{ selected.product }}</h2>
    <dl>
      <dt>필요일</dt>
      <dd>{{ selected.date }}</dd>
      <dt>연결 주문</dt>
      <dd>주문 API 미연결</dd>
      <dt>관련 위험</dt>
      <dd>
        {{
          w.issues
            .filter((i) => i.plan.includes(selected!.id))
            .map((i) => i.item + " · " + i.kind)
            .join(", ") || "새로운 위험 없음"
        }}
      </dd>
    </dl>
    <details>
      <summary>변경 이력</summary>
      <p
        v-for="h in w.data!.history.filter(
          (h) =>
            h.target.includes(selected!.product) ||
            h.target.includes(selected!.id),
        )"
      >
        {{ h.title }} · {{ h.detail }}
      </p>
    </details>
    <button
      @click="
        w.panel = true;
        selected = undefined;
      "
    >
      이 계획을 AI에 질문
    </button>
    <div class="comparison">
      <div>
        <small>현재 계획</small><b>{{ selected.date }}</b
        ><span>{{ selected.qty }}대</span>
      </div>
      <span>→</span>
      <div>
        <small>변경안 · 미저장</small><b>{{ date }}</b
        ><span>{{ qty }}대</span>
      </div>
    </div>
    <form @submit.prevent="save">
      <label
        >변경 생산일<input
          v-model="date"
          required
          type="date"
          @input="
            preview = '';
            apiResult = undefined;
          " /></label
      ><label
        >변경 수량 (대)<input
          v-model.number="qty"
          type="number"
          required
          min="1"
          step="1"
          @input="
            preview = '';
            apiResult = undefined;
          "
      /></label>
      <div class="action-row">
        <button type="button" @click="simulate">영향 미리보기</button
        ><button
          class="primary"
          :disabled="w.busy || w.session?.role !== 'PLANNER'"
        >
          {{ w.busy ? "저장 중…" : "실제 변경 저장" }}
        </button>
      </div>
    </form>
    <p v-if="previewError" class="warning-box">{{ previewError }}</p>
    <div v-if="result">
      <h3>가상 변경 영향 · 미저장</h3>
      <IssueTable
        :issues="result.issues"
        @select="(i) => (w.context = i.item)"
      />
      <p class="caption">{{ result.policy_version }}</p>
      <RouterLink class="button primary" to="/solutions"
        >해결안 비교로 이동 →</RouterLink
      >
    </div>
    <p v-if="w.error" class="error">{{ w.error }}</p></Drawer
  >
</template>
