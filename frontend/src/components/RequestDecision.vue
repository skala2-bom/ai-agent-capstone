<script setup lang="ts">
import { ref } from "vue";
import { useWorkspace } from "../stores/workspace";
import { useCoordination } from "../stores/coordination";
import { request } from "../services/api";
import { canReject, type CoordinationRequest } from "../services/coordination";
import ApprovalConfirm from "./ApprovalConfirm.vue";
const props = defineProps<{item: CoordinationRequest}>();
const emit = defineEmits<{changed: []}>();
const w = useWorkspace(), c = useCoordination();
const reason = ref(""), error = ref(""), editing = ref(false), confirming = ref(false), busy = ref(false);
const attempt = ref<Record<string, unknown>>();
function confirm() {
  if (!reason.value.trim()) return;
  attempt.value = {commandId: crypto.randomUUID(), expectedRevision: props.item.revision, reason: reason.value.trim()};
  confirming.value = true;
}
async function submit() {
  confirming.value = false; busy.value = true; error.value = "";
  try {
    await request(`/coordination/requests/${encodeURIComponent(props.item.id)}/reject`, w.session?.token, "POST", attempt.value);
    await Promise.all([c.refresh(), w.refreshApprovals()]); emit("changed"); editing.value = false; attempt.value = undefined;
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
</script>
<template>
  <div v-if="canReject(item, w.session?.role, w.session?.userId)" class="request-decision">
    <button v-if="!editing" @click="editing = true">요청 최종 거절</button>
    <div v-else>
      <label>요청 부서에 전달할 최종 거절 사유<textarea v-model="reason" rows="3" maxlength="4000" :disabled="busy || !!attempt" /></label>
      <p class="caption">다른 해결안을 찾는 경우에는 개별 제안의 수정 필요를 사용하세요.</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button v-if="attempt && error" :disabled="busy" @click="submit">동일 결정 결과 다시 확인</button>
      <button v-else :disabled="busy || !reason.trim()" @click="confirm">최종 거절 내용 확인</button>
      <button :disabled="busy" @click="editing = false; attempt = undefined; error = ''">돌아가기</button>
    </div>
    <ApprovalConfirm v-if="confirming" decision="REJECTED" purpose="request-reject" :summary="item.message" :reason="reason"
      @confirm="submit" @cancel="confirming = false; attempt = undefined" />
  </div>
</template>
<style scoped>
.request-decision { margin-top:16px; }
textarea { width:100%; }
button + button { margin-left:8px; }
</style>
