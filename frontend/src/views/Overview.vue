<script setup lang="ts">
import { computed } from "vue";
import {
  ArrowUpRight,
  CalendarDays,
  Truck,
  CheckCircle2,
} from "lucide-vue-next";
import { useWorkspace } from "../stores/workspace";
import { environments } from "../mocks/environments";
import Badge from "../components/Badge.vue";
import { mode } from "../services/api";
import { selectable } from "../services/workflow";
const w = useWorkspace();
const openPurchaseCount = computed(
  () => w.data!.purchases.filter((p) => p.received < p.qty).length,
);
const env = computed(() => environments.find((e) => e.id === w.scenario)!);
const role = computed(() => w.session!.role);
const risks = computed(() => w.issues.filter((i) => i.kind !== "추가발주"));
const plans = computed(() =>
  [...w.data!.plans].sort((a, b) => a.date.localeCompare(b.date)).slice(0, 4),
);
const receipts = computed(() =>
  [...w.data!.purchases]
    .filter((p) => p.received < p.qty)
    .sort(
      (a, b) =>
        Number(b.status === "지연 통보") - Number(a.status === "지연 통보") ||
        a.date.localeCompare(b.date),
    )
    .slice(0, 4),
);
const approvals = computed(() =>
  w.data!.approvals.filter((a) => a.status === "PENDING"),
);
const review = computed(() =>
  w.data!.options.filter(
    (o) => o.review === "미검수" || o.review === "보완 요청",
  ),
);
const priorities = computed(() =>
  w.data!.options.filter((o) => selectable(o) && !o.priority),
);
const requests = computed(() =>
  w.data!.options.filter(
    (o) =>
      o.priority === 1 &&
      !w.data!.approvals.some(
        (a) => a.optionId === o.id && a.status !== "REJECTED",
      ),
  ),
);
function approvalSummary(optionId: string) {
  const o = w.data!.options.find((option) => option.id === optionId);
  if (!o) return "";
  return o.role === "PLANNER"
    ? o.risk
    : `추가비용 ${o.addedCost.toLocaleString()}원 · ${o.risk}`;
}
const todos = computed(() =>
  role.value === "PLANNER"
    ? [
        {
          title: "해결안 우선순위 결정",
          count: priorities.value.length,
          to: "/solutions",
        },
        {
          title: "검토 중인 생산계획",
          count: w.data!.plans.filter((p) => p.status === "검토").length,
          to: "/plans",
        },
      ]
    : role.value === "BUYER"
      ? [
          {
            title: "비용등급 검수",
            count: review.value.length,
            to: "/solutions",
          },
          {
            title: "선정된 해결안 승인 요청",
            count: requests.value.length,
            to: "/solutions",
          },
          {
            title: "실제 입고 검수",
            count: w.data!.purchases.filter((p) => p.status === "검수대기")
              .length,
            to: "/purchasing",
          },
        ]
      : [
          {
            title: "발주 변경 승인",
            count: approvals.value.length,
            to: "/approvals",
          },
          {
            title: "실행 결과 확인",
            count: w.data!.approvals.filter((a) => a.status === "APPROVED")
              .length,
            to: "/approvals",
          },
        ],
);
</script>
<template>
  <div class="overview-meta">
    <span>{{ mode === "api" ? "실제 데이터 · 현재 상태" : `${env.tag} · ${env.label}` }}</span
    ><span>기준일 2026년 11월 2일</span>
  </div>
  <div class="status-strip">
    <div>
      <span class="status-dot" :class="{ risk: risks.length }" /><b>{{
        w.data?.analysisStale
          ? "업무 변경 후 영향 재분석이 필요합니다"
          : risks.length
            ? `${risks.length}건의 변경 영향을 확인하세요`
            : "예정된 생산과 입고가 정상적으로 운영되고 있습니다"
      }}</b>
    </div>
    <RouterLink to="/plans">생산계획 {{ w.data!.plans.length }}건</RouterLink
    ><RouterLink to="/purchasing"
      >입고 예정 {{ openPurchaseCount }}건</RouterLink
    ><span
      >{{ w.data?.analysisStale ? "이전 분석 위험" : "신규 위험" }}
      <b>{{ risks.length }}</b></span
    >
  </div>
  <p v-if="w.data?.analysisStale" class="warning-box">
    업무 데이터가 변경되었습니다. 표시된 영향은 이전 분석 결과이며 재분석이
    필요합니다.
  </p>
  <div class="dashboard-grid">
    <section class="dashboard-primary">
      <template v-if="role === 'APPROVER'"
        ><div class="section-head">
          <h2>
            내 승인을 기다리는 요청
            <span class="count">{{ approvals.length }}</span>
          </h2>
          <RouterLink to="/approvals">승인함 보기 →</RouterLink>
        </div>
        <div class="compact-empty" v-if="!approvals.length">
          <CheckCircle2 :size="19" />
          <div>
            <b>현재 승인 대기 요청이 없습니다</b>
            <p>
              새 요청이 도착하면 변경 내용과 비용을 함께 확인할 수 있습니다.
            </p>
          </div>
        </div>
        <RouterLink v-for="a in approvals" to="/approvals" class="work-row"
          ><div>
            <b>{{ w.data!.options.find((o) => o.id === a.optionId)?.name }}</b
            ><small
              >{{ a.requester }} · {{ approvalSummary(a.optionId) }}</small
            >
          </div>
          <ArrowUpRight :size="16" /></RouterLink></template
      ><template v-if="role === 'BUYER'"
        ><div class="section-head">
          <h2>입고 확인</h2>
          <RouterLink to="/purchasing">발주 · 입고 →</RouterLink>
        </div>
        <div class="card flush">
          <div v-for="p in receipts" :key="p.id" class="work-row">
            <span class="date-chip">{{
              p.date.slice(5).replace("-", ".")
            }}</span>
            <div>
              <RouterLink to="/purchasing"
                ><b>{{ p.item }}</b></RouterLink
              ><small>{{ p.supplier }} · {{ p.qty.toLocaleString() }}개</small>
            </div>
            <Badge :text="p.status" />
          </div></div
      ></template>
      <div class="section-head">
        <h2>
          {{
            risks.length
              ? role === "PLANNER"
                ? "차질이 예상되는 생산"
                : "주요 변경 영향"
              : "가까운 생산 일정"
          }}<span v-if="risks.length" class="count">{{ risks.length }}</span>
        </h2>
        <RouterLink :to="risks.length ? '/solutions' : '/plans'"
          >{{ risks.length ? "해결안 비교" : "캘린더 보기" }} →</RouterLink
        >
      </div>
      <section v-if="risks.length" class="card flush">
        <button
          v-for="(i, n) in risks"
          :key="n"
          class="issue-row"
          @click="
            w.context = `${i.item} · ${i.plan}`;
            w.panel = true;
          "
        >
          <span
            class="issue-marker"
            :class="{ amber: i.kind === '주의' || i.kind === '과잉' }"
          />
          <div>
            <div class="issue-title">
              <b>{{ i.item }}</b
              ><Badge :text="i.kind" />
            </div>
            <p>
              {{ i.plan }} <span>· {{ i.date }}</span>
            </p>
          </div>
          <div class="issue-quantity">
            <b>{{ i.qty.toLocaleString() }}</b
            ><small>{{
              i.kind === "주의" ? "예상재고 (개)" : "영향 수량 (개)"
            }}</small>
          </div>
          <ArrowUpRight :size="16" />
        </button>
        <footer class="table-footer">
          {{ w.data?.policyVersion }} · {{ mode === "api" ? "현재 상태 계산 결과" : "데모 분석 결과" }}
        </footer>
      </section>
      <section v-else class="card flush">
        <RouterLink v-for="p in plans" class="work-row" to="/plans"
          ><span class="date-chip">{{
            p.date.slice(5).replace("-", ".")
          }}</span>
          <div>
            <b>{{ p.product }}</b
            ><small>{{ p.id }} · {{ p.status }}</small>
          </div>
          <b class="num">{{ p.qty.toLocaleString() }}대</b></RouterLink
        >
      </section>
      <div class="section-head">
        <h2>최근 변경</h2>
        <RouterLink to="/history">전체 이력 →</RouterLink>
      </div>
      <div
        v-for="h in w.data!.history.slice(0, 3)"
        :key="h.id"
        class="history-row"
      >
        <span class="activity-dot" />
        <div>
          <b>{{ h.title }}</b>
          <p>{{ h.detail }} · {{ h.target }}</p>
        </div>
        <small>{{
          new Date(h.at).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" })
        }}</small>
      </div>
    </section>
    <aside class="dashboard-secondary">
      <div class="section-head">
        <h2>{{ w.session!.name }}님의 할 일</h2>
        <span class="caption"
          >{{ todos.reduce((n, t) => n + t.count, 0) }}건</span
        >
      </div>
      <div class="todo-list">
        <RouterLink v-for="t in todos" :key="t.title" :to="t.to"
          ><span>{{ t.title }}</span
          ><b>{{ t.count }}</b
          ><ArrowUpRight :size="15"
        /></RouterLink>
      </div>
      <div class="section-head">
        <h2>{{ role === "BUYER" ? "가까운 생산" : "다가오는 일정" }}</h2>
        <RouterLink to="/plans">전체 →</RouterLink>
      </div>
      <div class="upcoming-list">
        <template v-if="role !== 'BUYER'"
          ><RouterLink
            v-for="p in receipts.slice(0, 2)"
            :key="p.id"
            to="/purchasing"
            ><Truck :size="17" />
            <div>
              <small>{{ p.date }} · 입고</small><b>{{ p.item }}</b>
              <p>{{ p.qty.toLocaleString() }}개 · {{ p.supplier }}</p>
            </div></RouterLink
          ></template
        ><RouterLink
          v-for="p in plans.slice(0, role === 'BUYER' ? 4 : 2)"
          :key="p.id"
          to="/plans"
          ><CalendarDays :size="17" />
          <div>
            <small>{{ p.date }} · 생산</small><b>{{ p.product }}</b>
            <p>{{ p.qty.toLocaleString() }}대</p>
          </div></RouterLink
        >
      </div>
    </aside>
  </div>
</template>
