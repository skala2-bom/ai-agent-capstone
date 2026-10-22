<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useWorkspace } from "../stores/workspace";
import type { Purchase } from "../types";
import Drawer from "../components/Drawer.vue";
import Badge from "../components/Badge.vue";
import { batchLocked } from "../services/task-batch";
import { mode, request } from "../services/api";
const w = useWorkspace(),
  q = ref(""),
  tab = ref("발주 · 입고"),
  status = ref(""),
  selected = ref<Purchase>(),
  date = ref(""),
  qty = ref(1),
  reason = ref(""),
  passed = ref(0),
  failed = ref(0),
  metric = ref(-1),
  onTime = ref(0),
  quality = ref(0),
  supplier = ref(""),
  from = ref(""),
  to = ref("");
const route = useRoute(), router = useRouter();
const changeKind = ref("manual"), delaySaving = ref(false);
const taskId = computed(() => typeof route.query.taskId === "string" ? route.query.taskId : "");
const approvalId = computed(() => typeof route.query.approvalId === "string" ? route.query.approvalId : "");
const taskEditing = computed(() => !!taskId.value && !!approvalId.value);
const taskLocked = computed(() => taskEditing.value && (batchLocked(w.batchStates[taskId.value]) || !!w.batchPending[taskId.value]));
watch(() => [route.query.poId, w.data?.purchases], () => {
  if (!taskEditing.value) return;
  const purchase = w.data?.purchases.find(p => p.id === route.query.poId);
  if (purchase && selected.value?.id !== purchase.id) open(purchase);
}, {immediate: true});
const rows = computed(() =>
  w.data!.purchases.filter(
    (p) =>
      (p.id + p.item + p.supplier).includes(q.value) &&
      (!status.value || p.status === status.value) &&
      (!supplier.value || p.supplier === supplier.value) &&
      (!from.value || p.date >= from.value) &&
      (!to.value || p.date <= to.value),
  ),
);
function open(p: Purchase) {
  selected.value = p;
  date.value = p.date;
  qty.value = p.qty;
  reason.value = "";
  changeKind.value = "manual";
  w.error = "";
  passed.value = p.passed;
  failed.value = p.failed;
  w.context = `${p.id} · ${p.item}`;
}
const editable = computed(() => !!selected.value && selected.value.received === 0
  && ["OPEN", "입고 예정", "지연 통보"].includes(selected.value.status));
const changed = computed(() => !!selected.value && (date.value !== selected.value.date || qty.value !== selected.value.qty));
async function savePurchase() {
  if (!editable.value || !changed.value || taskLocked.value) return;
  if (taskEditing.value) {
    if (await w.sendBatch(taskId.value, "MANUAL", approvalId.value, {
      reason: reason.value, args: {po_id: selected.value!.id, new_qty: qty.value, new_due_date: date.value},
    })) await router.push("/approvals");
    return;
  }
  if (changeKind.value === "delay" && mode === "api") {
    if (delaySaving.value || !reason.value.trim()) return;
    delaySaving.value = true; w.error = "";
    try {
      const result = await request<{analysis_task_id: string}>(`/purchase-orders/${encodeURIComponent(selected.value!.id)}/due-date`, w.session?.token, "PUT", {due_date: date.value, reason: reason.value.trim()});
      selected.value = undefined; await w.load(); w.openTask(result.analysis_task_id);
    } catch (e) { w.error = (e as Error).message; }
    finally { delaySaving.value = false; }
    return;
  }
  if (
    await w.save({
      type: "purchase",
      id: selected.value!.id,
      date: date.value,
      qty: qty.value,
      reason: reason.value,
    })
  )
    selected.value = undefined;
}
</script>
<template>
  <div class="tabs">
    <button
      v-for="t in ['발주 · 입고', '협력사 × 부품 평가지표']"
      :class="{ active: tab === t }"
      @click="tab = t"
    >
      {{ t }}
    </button>
  </div>
  <template v-if="tab === '발주 · 입고'"
    ><div class="toolbar">
      <input
        v-model="q"
        aria-label="발주 검색"
        placeholder="발주번호, 품목, 협력사 검색"
      /><select v-model="status" aria-label="입고 상태">
        <option value="">모든 상태</option>
        <option>입고 예정</option>
        <option>지연 통보</option>
        <option>검수대기</option>
        <option>검수 완료</option>
      </select>
      <select v-model="supplier" aria-label="협력사 필터">
        <option value="">모든 협력사</option>
        <option
          v-for="name in [...new Set(w.data!.purchases.map((p) => p.supplier))]"
        >
          {{ name }}
        </option></select
      ><input v-model="from" type="date" aria-label="입고 시작일" /><input
        v-model="to"
        type="date"
        aria-label="입고 종료일"
      />
    </div>
    <section class="card flush table-wrap">
      <table>
        <thead>
          <tr>
            <th>발주 / 품목</th>
            <th>협력사</th>
            <th>예정 입고일</th>
            <th class="num">발주량 / 잔량</th>
            <th>상태</th>
            <th>관리</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in rows" :key="p.id">
            <td>
              <b>{{ p.item }}</b
              ><small>{{ p.id }}</small>
            </td>
            <td>{{ p.supplier }}</td>
            <td>{{ p.date }}</td>
            <td class="num">
              {{ p.qty.toLocaleString() }} /
              {{ (p.qty - p.received).toLocaleString() }}
            </td>
            <td><Badge :text="p.status" /></td>
            <td><button :disabled="taskEditing && p.id !== route.query.poId" @click="open(p)">상세 보기</button></td>
          </tr>
        </tbody>
      </table>
      <div v-if="!rows.length" class="empty">조건에 맞는 발주가 없습니다.</div>
    </section></template
  >
  <section v-else class="card">
    <p class="muted">
      납기·품질의 기초자료입니다. 개별 해결안의 비용등급과는 별도로 관리합니다.
    </p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>협력사 / 품목</th>
            <th class="num">납기준수율</th>
            <th class="num">품질점수</th>
            <th>평가 담당 / 일자</th>
            <th>작업</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(m, i) in w.data?.metrics">
            <td>
              {{ m.supplier }}<small>{{ m.item }}</small>
            </td>
            <td class="num">{{ m.onTime }}%</td>
            <td class="num">{{ m.quality }}점</td>
            <td>
              {{ m.by }}<small>{{ m.date }}</small>
            </td>
            <td>
              <button
                :disabled="!w.buyer"
                @click="
                  metric = i;
                  onTime = m.onTime;
                  quality = m.quality;
                "
              >
                직접 수정
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <Drawer v-if="selected" title="발주 · 입고 상세" @close="selected = undefined"
    ><span class="eyebrow">{{ selected.id }}</span>
    <h2>{{ selected.item }} · {{ selected.supplier }}</h2>
    <p v-if="taskEditing" class="warning-box">이 task의 수정안을 작성합니다. 발주는 아직 변경하지 않으며, 승인 대기함에서 검토 후 함께 실행합니다.</p>
    <p v-if="taskEditing && w.batchErrors[taskId]" class="error">{{ w.batchErrors[taskId] }}</p>
    <div class="comparison">
      <div>
        <small>변경 전</small><b>{{ selected.date }}</b><span>{{ selected.qty.toLocaleString() }}개</span>
      </div>
      <span>→</span>
      <div>
        <small>변경 후</small><b>{{ date }}</b><span>{{ qty.toLocaleString() }}개</span>
      </div>
    </div>
    <p v-if="!editable" class="caption">입고 완료·취소된 발주는 수정할 수 없습니다.</p>
    <form @submit.prevent="savePurchase">
      <label v-if="!taskEditing && mode === 'api' && w.session?.role === 'BUYER'">변경 구분<select v-model="changeKind" :disabled="delaySaving" @change="qty = selected!.qty">
        <option value="manual">입고 정보 수동 수정</option><option value="delay">협력사의 확정 지연 통보</option></select></label>
      <p v-if="changeKind === 'delay'" class="caption">지연 사실을 저장하고 영향 분석을 시작합니다. 분석 후 생산팀에 일정 조정을 요청할 수 있습니다.</p>
      <label
        >변경 입고 예정일<input
          v-model="date"
          type="date"
          required
          min="2026-11-02"
          :disabled="!w.buyer || !editable || w.busy || delaySaving" /></label
      ><label
        >입고 예정 수량<input
          v-model.number="qty"
          type="number"
          min="1"
          max="2147483647"
          step="1"
          required
          :disabled="!w.buyer || !editable || w.busy || delaySaving || changeKind === 'delay'" /></label
      ><label
        >변경 사유<textarea v-model="reason" required rows="3" :disabled="!w.buyer || !editable || w.busy" /></label
      ><button
        class="primary"
        :disabled="!w.buyer || !editable || w.busy || delaySaving || taskLocked || !changed || !reason.trim() || !Number.isInteger(qty) || qty < 1 || !date"
      >
        {{ taskEditing ? '수정안 확정' : changeKind === 'delay' ? '지연 사실 저장 및 영향 분석' : '입고 정보 저장' }}
      </button>
    </form>
    <button
      @click="
        w.panel = true;
        selected = undefined;
      "
    >
      영향받는 생산 확인 · AI
    </button>
    <h3>실제 입고 검수</h3>
    <dl>
      <dt>실제 도착 수량</dt>
      <dd>{{ selected.received }}개</dd>
      <dt>검수대기</dt>
      <dd>{{ selected.received - selected.passed - selected.failed }}개</dd>
    </dl>
    <p v-if="!selected.received" class="warning-box">
      아직 도착하지 않은 발주입니다. 입고 완료로 처리할 수 없습니다.
    </p>
    <form
      v-else
      @submit.prevent="
        async () => {
          if (
            await w.save({ type: 'receipt', id: selected!.id, passed, failed })
          )
            selected = undefined;
        }
      "
    >
      <label
        >합격 수량<input
          v-model.number="passed"
          type="number"
          min="0"
          required /></label
      ><label
        >불합격 수량<input
          v-model.number="failed"
          type="number"
          min="0"
          required /></label
      ><button :disabled="!w.buyer || w.busy" class="primary">
        입고 검수 저장
      </button>
    </form>
    <p v-if="w.error" class="error">{{ w.error }}</p></Drawer
  ><Drawer
    v-if="metric >= 0"
    title="협력사 × 부품 평가지표 수정"
    @close="metric = -1"
    ><form
      @submit.prevent="
        async () => {
          if (await w.save({ type: 'metric', index: metric, onTime, quality }))
            metric = -1;
        }
      "
    >
      <label
        >납기준수율 (%)<input
          v-model.number="onTime"
          type="number"
          min="0"
          max="100"
          step="0.1"
          required /></label
      ><label
        >품질점수<input
          v-model.number="quality"
          type="number"
          min="0"
          max="100"
          step="0.1"
          required /></label
      ><button class="primary" :disabled="w.busy">평가지표 저장</button>
    </form>
    <p v-if="w.error" class="error">{{ w.error }}</p></Drawer
  >
</template>
