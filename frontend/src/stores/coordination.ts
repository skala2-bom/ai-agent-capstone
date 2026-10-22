import { computed, ref } from "vue";
import { defineStore } from "pinia";
import { useWorkspace } from "./workspace";
import { mode, request } from "../services/api";
import type { CoordinationRequest } from "../services/coordination";

export const useCoordination = defineStore("coordination", () => {
  const w = useWorkspace();
  const items = ref<CoordinationRequest[]>([]);
  const error = ref("");
  const activeRequestId = ref<string>();
  const chatDraft = ref<{id?: string; question: string; nonce: number}>();
  const resubmitParentId = ref<string>();
  let generation = 0, refreshSequence = 0;
  const unread = computed(() => items.value.filter(r => r.unread).length);
  function clear() { generation++; refreshSequence++; items.value = []; error.value = ""; activeRequestId.value = undefined; chatDraft.value = undefined; resubmitParentId.value = undefined; }
  async function refresh() {
    if (mode !== "api" || !w.session) return;
    const token = w.session.token, g = generation, seq = ++refreshSequence;
    try {
      const rows = await request<CoordinationRequest[]>("/coordination/requests", token);
      if (g === generation && seq === refreshSequence && token === w.session?.token) { items.value = rows; error.value = ""; }
    } catch (e) { if (g === generation && seq === refreshSequence) error.value = (e as Error).message; }
  }
  async function load(id: string) {
    const token = w.session?.token, g = generation;
    const row = await request<CoordinationRequest>(`/coordination/requests/${encodeURIComponent(id)}`, token);
    if (g === generation && token === w.session?.token) items.value = [row, ...items.value.filter(r => r.id !== row.id)];
    return row;
  }
  async function read(r: CoordinationRequest) {
    await request(`/coordination/requests/${encodeURIComponent(r.id)}/read`, w.session?.token, "POST", {revision: r.revision});
    const current = items.value.find(x => x.id === r.id);
    if (current?.revision === r.revision) current.unread = false;
  }
  function ask(r: CoordinationRequest, question = "이 요청을 해결할 방법을 알려 주세요.") {
    w.activeTask = undefined; w.panel = true;
    activeRequestId.value = r.id;
    chatDraft.value = {id: r.id, question, nonce: Date.now()};
  }
  function reanalyze(r: CoordinationRequest) {
    resubmitParentId.value = r.id; activeRequestId.value = undefined;
    w.activeTask = undefined; w.panel = true;
    chatDraft.value = {question: "", nonce: Date.now()};
  }
  return {items, error, unread, activeRequestId, chatDraft, resubmitParentId, clear, refresh, load, read, ask, reanalyze};
});
