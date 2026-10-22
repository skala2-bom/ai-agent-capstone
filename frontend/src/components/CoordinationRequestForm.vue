<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useWorkspace } from "../stores/workspace";
import { useCoordination } from "../stores/coordination";
import { request } from "../services/api";
import type { CoordinationRequest, RequestPreview } from "../services/coordination";
import Drawer from "./Drawer.vue";
import ServerResult from "./ServerResult.vue";
const props = defineProps<{taskId: string; parentRequestId?: string}>();
const emit = defineEmits<{close: []; sent: [id: string]}>();
const w = useWorkspace(), c = useCoordination(), router = useRouter();
const preview = ref<RequestPreview>(), message = ref(""), consent = ref(false), busy = ref(false), error = ref("");
let attempt: Record<string, unknown> | undefined;
async function load() {
  busy.value = true; error.value = ""; consent.value = false; attempt = undefined;
  try { preview.value = await request(`/coordination/preview?taskId=${encodeURIComponent(props.taskId)}`, w.session?.token); }
  catch (e) { preview.value = undefined; error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function send() {
  if (!preview.value || !message.value.trim() || !consent.value || busy.value) return;
  busy.value = true; error.value = "";
  attempt ??= {sourceTaskId: props.taskId, message: message.value.trim(), expectedFingerprint: preview.value.fingerprint,
    commandId: crypto.randomUUID(), parentRequestId: props.parentRequestId ?? c.resubmitParentId ?? null};
  try {
    const r = await request<CoordinationRequest>("/coordination/requests", w.session?.token, "POST", attempt);
    c.resubmitParentId = undefined; await c.refresh(); emit("sent", r.id);
    await router.push({path: "/requests", query: {id: r.id}});
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
onMounted(load);
</script>
<template>
  <Drawer title="부서에 변경 요청" @close="emit('close')">
    <p v-if="busy" role="status">처리 중…</p>
    <p v-if="error" class="error" role="alert">{{ error }} <button @click="load" :disabled="busy">최신 내용 다시 확인</button></p>
    <template v-if="preview">
      <h3>{{ preview.direction === 'PLAN_TO_BUY' ? '구매팀에 해결 요청' : '생산팀에 일정 조정 요청' }}</h3>
      <div v-for="p in preview.planChanges" :key="p.planId" class="card">
        <b>{{ p.planId }}</b><p>{{ p.before.plan_date }} · {{ p.before.qty }}개 → {{ p.after.plan_date }} · {{ p.after.qty }}개</p>
      </div>
      <div v-for="f in preview.facts" :key="f.poId" class="card">
        <b>확정된 입고 지연 · {{ f.poId }}</b><p>{{ f.before.due_date }} → {{ f.after.due_date }} · {{ f.after.qty }}개</p><p>{{ f.reason }}</p>
      </div>
      <ServerResult :result="preview.analysis" title="현재 데이터로 확인한 문제" />
      <label>요청 내용<textarea v-model="message" maxlength="4000" rows="4" :disabled="busy || !!attempt" placeholder="변경 목적과 해결이 필요한 내용을 작성하세요." /></label>
      <label class="consent"><input v-model="consent" type="checkbox" :disabled="busy" />
        {{ preview.direction === 'PLAN_TO_BUY' ? '위 생산 수량·날짜 변경에 동의합니다. 구매팀장 최종 실행 시 발주와 생산계획을 함께 반영합니다.' : '확정된 지연 사실을 전달하고 생산계획 조정을 요청합니다. 생산계획은 담당 생산관리자가 확인 후 변경합니다.' }}</label>
      <button class="primary" :disabled="busy || !consent || !message.trim()" @click="send">{{ attempt ? '동일 요청 전송 다시 확인' : '동의하고 요청 보내기' }}</button>
    </template>
  </Drawer>
</template>
<style scoped>
.consent { display:flex; align-items:flex-start; gap:10px; margin:18px 0; line-height:1.7; }
.consent input { width:auto; margin-top:6px; }
.card { margin-bottom:12px; }
textarea { width:100%; }
</style>
