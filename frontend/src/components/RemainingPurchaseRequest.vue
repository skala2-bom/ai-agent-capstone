<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useWorkspace } from "../stores/workspace";
import { useCoordination } from "../stores/coordination";
import { request } from "../services/api";
import type { CoordinationRequest } from "../services/coordination";
import Drawer from "./Drawer.vue";
import ServerResult from "./ServerResult.vue";
const props = defineProps<{item: CoordinationRequest}>();
const w = useWorkspace(), c = useCoordination(), router = useRouter();
const open = ref(false), busy = ref(false), error = ref("");
const preview = ref<{analysis: Record<string, unknown>; fingerprint: string; revision: number}>();
const message = ref("생산계획 변경을 먼저 저장했습니다. 현재 남은 결품·과잉을 기존 발주 조정으로 해결할 수 있는지 검토해 주세요.");
const attempt = ref<Record<string, unknown>>();
async function load() {
  open.value = true; busy.value = true; error.value = ""; preview.value = undefined; attempt.value = undefined;
  try { preview.value = await request(`/coordination/requests/${props.item.id}/remaining-preview`, w.session?.token); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function send() {
  if (!preview.value || !message.value.trim() || busy.value) return;
  busy.value = true; error.value = "";
  attempt.value ??= {commandId: crypto.randomUUID(), expectedRevision: preview.value.revision,
    expectedFingerprint: preview.value.fingerprint, message: message.value.trim()};
  try {
    const child = await request<CoordinationRequest>(`/coordination/requests/${props.item.id}/remaining-request`, w.session?.token, "POST", attempt.value);
    await c.refresh(); open.value = false; await router.push({path: "/requests", query: {id: child.id}});
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
</script>
<template>
  <section class="remaining-request">
    <h3>다음 단계 · 남은 구매 문제 전달</h3>
    <p>생산계획은 저장됐습니다. 남은 문제를 구매팀에 보내 해결안 검토와 팀장 승인을 요청하세요.</p>
    <button class="primary" @click="load">남은 문제를 구매팀에 요청</button>
    <Drawer v-if="open" title="남은 문제를 구매팀에 요청" @close="open = false">
      <p v-if="busy" role="status">현재 데이터를 확인하고 있습니다…</p>
      <p v-if="error" class="error" role="alert">{{ error }} <button :disabled="busy" @click="load">최신 문제 다시 확인</button></p>
      <template v-if="preview">
        <p>이미 저장한 생산계획은 유지합니다. 아래 남은 문제만 새 구매 요청으로 전달합니다.</p>
        <ServerResult :result="preview.analysis" title="현재 남은 문제" />
        <label>구매팀에 전달할 내용<textarea v-model="message" rows="4" maxlength="4000" :disabled="busy || !!attempt" /></label>
        <button class="primary" :disabled="busy || !message.trim()" @click="send">{{ attempt ? '동일 전송 결과 다시 확인' : '구매팀에 남은 문제 전송' }}</button>
      </template>
    </Drawer>
  </section>
</template>
<style scoped>
.remaining-request { margin:20px 0; padding:18px; background:#f7fbf2; border:1px solid #dfe8d7; border-radius:10px; }
textarea { width:100%; } p { line-height:1.6; }
</style>
