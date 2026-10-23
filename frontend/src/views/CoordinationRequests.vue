<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useWorkspace } from "../stores/workspace";
import { useCoordination } from "../stores/coordination";
import { mode, request } from "../services/api";
import { requestClosed, requestLabels, type CoordinationRequest } from "../services/coordination";
import PlanRequestReview from "../components/PlanRequestReview.vue";
import RemainingPurchaseRequest from "../components/RemainingPurchaseRequest.vue";
import RequestDecision from "../components/RequestDecision.vue";
import ServerResult from "../components/ServerResult.vue";
const w = useWorkspace(), c = useCoordination(), route = useRoute(), router = useRouter();
const filter = ref("all"), error = ref(""), busy = ref(false);
const selected = computed(() => c.items.find(r => r.id === route.query.id));
const mine = computed(() => selected.value?.assigneeId === w.session?.userId);
const rows = computed(() => c.items.filter(r => filter.value === "all" || (filter.value === "sent" ? r.requesterId === w.session?.userId : r.recipientRole === w.session?.role)));
async function open(r: CoordinationRequest) {
  await router.push({path: "/requests", query: {id: r.id}});
  try { await c.read(r); } catch (e) { error.value = (e as Error).message; }
}
async function loadSelected() {
  if (typeof route.query.id !== "string" || mode !== "api") return;
  try { const r = await c.load(route.query.id); await c.read(r); }
  catch (e) { error.value = (e as Error).message; }
}
async function action(name: "claim" | "escalate") {
  if (!selected.value || busy.value) return;
  busy.value = true; error.value = "";
  try {
    await request(`/coordination/requests/${selected.value.id}/${name}`, w.session?.token, "POST", {expectedRevision: selected.value.revision});
    await c.refresh();
  } catch (e) { error.value = (e as Error).message; await c.refresh(); }
  finally { busy.value = false; }
}
function startQuestion(r: CoordinationRequest) {
  c.ask(r, r.direction === "PLAN_TO_BUY" ? "생산팀이 요청한 조건을 유지해서 결품·과잉 해결 방법을 알려 주세요." : "입고 지연으로 영향을 받는 생산계획과 조정할 때 확인할 사항을 알려 주세요.");
}
watch(() => route.query.id, loadSelected);
onMounted(async () => { await c.refresh(); await loadSelected(); });
</script>
<template>
  <p v-if="mode !== 'api'" class="warning-box">부서 요청은 실시간 API 모드에서 사용할 수 있습니다.</p>
  <template v-else>
    <div class="toolbar"><select v-model="filter" aria-label="부서 요청 필터"><option value="all">전체 요청</option><option value="received">받은 요청</option><option value="sent">보낸 요청</option></select><button @click="c.refresh">새로고침</button></div>
    <p v-if="error || c.error" class="error" role="alert">{{ error || c.error }}</p>
    <div class="request-layout">
      <div class="request-list">
        <p v-if="!rows.length" class="empty">받거나 보낸 부서 요청이 없습니다.</p>
        <button v-for="r in rows" :key="r.id" class="request-row" :class="{selected: selected?.id === r.id}" @click="open(r)">
          <span class="row-meta">{{ r.direction === 'PLAN_TO_BUY' ? '생산 → 구매' : '구매 → 생산' }} <b v-if="r.unread">새 알림</b></span>
          <strong>{{ r.message }}</strong>
          <span>{{ requestLabels[r.status] ?? r.status }}<template v-if="r.needsAttention"> · 담당자 확인 필요</template></span>
          <small>{{ r.requesterName }} · {{ new Date(r.createdAt).toLocaleString('ko-KR') }}<br />담당: {{ r.assigneeName ?? '미지정' }}</small>
        </button>
      </div>
      <article v-if="selected" class="request-detail">
        <p class="eyebrow">{{ selected.direction === 'PLAN_TO_BUY' ? '생산계획 변경에 따른 구매 해결 요청' : '입고 지연에 따른 생산계획 조정 요청' }}</p>
        <h2>{{ requestLabels[selected.status] }}</h2>
        <p class="request-message">{{ selected.message }}</p>
        <p class="caption">요청자 {{ selected.requesterName }} · 담당자 {{ selected.assigneeName ?? '미지정' }}</p>
        <RouterLink v-if="selected.parentRequestId && c.items.some(r => r.id === selected!.parentRequestId)" :to="{path:'/requests',query:{id:selected.parentRequestId}}">이전 연결 요청 보기 →</RouterLink>
        <p v-if="selected.linkedRequestId"><RouterLink :to="{path:'/requests',query:{id:selected.linkedRequestId}}">연결된 구매 요청과 처리 결과 보기 →</RouterLink></p>
        <p v-if="selected.linkedOutcome" class="warning-box">연결된 구매 요청: {{ requestLabels[selected.linkedOutcome.status] }} · {{ selected.linkedOutcome.reason }}<br />이 생산 조정 요청은 다시 검토할 수 있습니다.</p>
        <div v-for="p in selected.planChanges" :key="p.planId" class="change-card"><b>{{ p.planId }} · 생산관리 동의 내용</b><p>{{ p.before.plan_date }} · {{ p.before.qty }}개 → {{ p.after.plan_date }} · {{ p.after.qty }}개</p></div>
        <div v-for="f in selected.facts" :key="f.poId" class="change-card"><b>{{ f.poId }} · 확정된 입고 지연</b><p>{{ f.before.due_date }} → {{ f.after.due_date }} · {{ f.after.qty }}개</p><p>{{ f.reason }}</p><small>이 요청이 거절되어도 확정된 지연 사실은 유지됩니다.</small></div>
        <div v-if="requestClosed(selected)" class="outcome" :class="{rejected:selected.status === 'REJECTED'}" role="status">
          <b>{{ requestLabels[selected.status] }}</b><p>{{ selected.decisionReason }}</p><small v-if="selected.decidedName">결정: {{ selected.decidedName }}</small>
          <div v-for="p in selected.outcome?.planChanges" :key="p.planId">{{ p.planId }}: {{ p.before.plan_date }} · {{ p.before.qty }}개 → {{ p.after.plan_date }} · {{ p.after.qty }}개</div>
          <p v-if="selected.status !== 'COMPLETED'">{{ selected.appliedPlanChanges?.length ? '추가 처리는 종료됐으며, 먼저 저장한 생산계획은 유지됩니다.' : '이 요청으로 인한 생산계획·발주 변경은 실행되지 않았습니다.' }}</p>
        </div>
        <p v-if="selected.needsAttention" class="warning-box">해결 작업을 확인해 주세요. 대안 없음이나 처리 오류는 최종 거절이 아닙니다. 직접 검토하거나 최종 결정 권한자에게 처리를 요청할 수 있습니다.</p>
        <div v-if="selected.appliedPlanChanges?.length" class="change-card">
          <b>이미 저장한 생산계획</b>
          <p v-for="p in selected.appliedPlanChanges" :key="p.planId">{{ p.planId }}: {{ p.before.plan_date }} · {{ p.before.qty }}개 → {{ p.after.plan_date }} · {{ p.after.qty }}개</p>
          <small>이 변경은 저장 완료입니다. 이후 구매 요청이 거절되거나 취소되어도 되돌리지 않습니다.</small>
        </div>
        <details><summary>요청 시 분석된 문제</summary><ServerResult :result="selected.analysis" /></details>
        <template v-if="!requestClosed(selected)">
          <button v-if="!selected.assigneeId && selected.recipientRole === w.session?.role" class="primary" :disabled="busy" @click="action('claim')">내가 담당하기</button>
          <template v-if="mine">
            <RemainingPurchaseRequest v-if="selected.status === 'PARTIALLY_COMPLETED'" :key="selected.id" :item="selected" />
            <button class="primary" @click="startQuestion(selected)">{{ selected.direction === 'PLAN_TO_BUY' ? 'AI에게 해결 방법 묻기' : 'AI에게 생산 변경안 묻기' }}</button>
            <button v-if="selected.solutionTaskId" @click="w.openTask(selected.solutionTaskId)">연결된 해결 작업 보기</button>
            <button v-if="selected.direction === 'PLAN_TO_BUY' && !selected.managerVisible" :disabled="busy" @click="action('escalate')">팀장에게 요청 검토 전달</button>
            <p v-if="selected.direction === 'PLAN_TO_BUY'" class="caption">해결안은 기존처럼 메모를 작성해 승인 요청하세요. 대안이 없어도 팀장에게 요청 자체의 최종 검토를 전달할 수 있습니다.</p>
            <PlanRequestReview v-if="selected.direction === 'BUY_TO_PLAN' && selected.status !== 'WAITING_LINKED'" :key="selected.id + String(route.query.analysisTaskId ?? '')" :item="selected" @changed="c.refresh" />
          </template>
          <RouterLink v-if="w.session?.role === 'APPROVER' && selected.solutionTaskId" to="/approvals">연결된 승인 대기함 보기 →</RouterLink>
          <RequestDecision :key="selected.id" :item="selected" @changed="c.refresh" />
        </template>
        <template v-else-if="selected.status !== 'COMPLETED' && selected.requesterId === w.session?.userId">
          <button v-if="selected.direction === 'PLAN_TO_BUY'" @click="c.reanalyze(selected)">조건을 바꿔 다시 분석</button>
          <RouterLink v-else to="/purchasing">입고 정보와 추가 조달 대책 확인 →</RouterLink>
        </template>
      </article>
      <div v-else class="empty">요청을 선택하면 전달 내용과 처리 결과를 확인할 수 있습니다.</div>
    </div>
  </template>
</template>
<style scoped>
.request-layout { display:grid; grid-template-columns:minmax(230px, 330px) minmax(0,1fr); gap:20px; }
.request-row { width:100%; text-align:left; display:flex; flex-direction:column; align-items:stretch; gap:10px; padding:18px; margin-bottom:10px; background:white; white-space:normal; }
.request-row.selected { background:#f7fbf2; border-color:#b8ce9d; }
.request-row strong { display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
.row-meta { display:flex; justify-content:space-between; gap:8px; color:#576e40; }
.row-meta b { font-size:11px; padding:2px 6px; background:#edf5e3; border-radius:4px; white-space:nowrap; }
.request-detail { padding:24px; border:1px solid #dfe5dc; border-radius:10px; background:white; min-width:0; }
.request-message { white-space:pre-wrap; line-height:1.8; } .change-card,.outcome { background:#f8fbf5; border-radius:8px; padding:16px; margin:16px 0; }
.outcome.rejected { background:#fff3f1; } .outcome p { white-space:pre-wrap; }
small { color:#697688; } button { margin:8px 8px 8px 0; } details { margin:20px 0; }
@media(max-width:900px) { .request-layout { grid-template-columns:1fr; } }
</style>
