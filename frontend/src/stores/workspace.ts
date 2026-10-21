import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type { Session, Workspace, Command, Role } from "../types";
import { gateway, mode, fetchApprovals, decideApproval } from "../services/api";
export const names: Record<Role, string> = {
  PLANNER: "김계획",
  BUYER: "박구매",
  APPROVER: "정팀장",
};
export const useWorkspace = defineStore("workspace", () => {
  const session = ref<Session>();
  const data = ref<Workspace>();
  const busy = ref(false);
  const error = ref("");
  const notice = ref("");
  const scenario = ref("SC0");
  const context = ref("");
  const panel = ref(false);
  const activeTask = ref<string>();
  const deciding = ref<Record<string, boolean>>({});
  const epoch = ref(0);
  const buyer = computed(() => session.value?.role !== "PLANNER");
  let generation = 0;
  let approvalVersion = 0;
  let refreshSequence = 0;
  async function load() {
    const g = ++generation;
    const a = approvalVersion;
    busy.value = true;
    error.value = "";
    try {
      const result = await gateway.load(session.value!, scenario.value);
      if (g === generation) {
        // An older full reload must not overwrite newer per-card decisions/polls.
        if (a !== approvalVersion && data.value) {
          const oldIds = new Set(result.approvals.map((x) => x.optionId));
          const currentIds = new Set(data.value.approvals.map((x) => x.optionId));
          result.options = [...result.options.filter((x) => !oldIds.has(x.id)),
            ...data.value.options.filter((x) => currentIds.has(x.id))];
          result.approvals = data.value.approvals;
        }
        data.value = result;
      }
    } catch (e) {
      if (g === generation) error.value = (e as Error).message;
    } finally {
      if (g === generation) busy.value = false;
    }
  }
  async function enter(s: Session, environment = scenario.value) {
    clear();
    session.value = s;
    scenario.value = environment;
    await load();
  }
  function clear() {
    generation++;
    epoch.value++;
    session.value = undefined;
    data.value = undefined;
    error.value = "";
    notice.value = "";
    context.value = "";
    panel.value = false;
    activeTask.value = undefined;
    deciding.value = {};
    approvalVersion++;
    refreshSequence++;
    busy.value = false;
    window.dispatchEvent(new Event("close-agent"));
  }
  async function save(c: Command) {
    if (busy.value) return false;
    busy.value = true;
    error.value = "";
    notice.value = "";
    const g = generation;
    try {
      const result = await gateway.save(session.value!, c, scenario.value);
      if (g !== generation) return false;
      data.value = result;
      notice.value = "저장했습니다.";
      return true;
    } catch (e) {
      if (g === generation) error.value = (e as Error).message;
      return false;
    } finally {
      if (g === generation) busy.value = false;
    }
  }
  async function refreshApprovals() {
    if (mode !== "api" || session.value?.role !== "APPROVER" || !data.value) return;
    const e = epoch.value, a = approvalVersion, seq = ++refreshSequence;
    const result = await fetchApprovals(session.value, data.value.items);
    if (e !== epoch.value || a !== approvalVersion || seq !== refreshSequence || !data.value) return;
    const ids = new Set(data.value.approvals.map((x) => x.optionId));
    data.value.options = [...data.value.options.filter((x) => !ids.has(x.id)), ...result.options];
    data.value.approvals = result.approvals;
    approvalVersion++;
  }
  async function decide(id: string, decision: "APPROVED" | "REJECTED", reason: string) {
    if (mode === "mock") return save({ type: "approval", id, decision, reason });
    const card = data.value?.approvals.find((x) => x.id === id);
    if (!card || !session.value || deciding.value[id] || card.status !== "PENDING") return false;
    if (decision === "REJECTED" && !reason.trim()) return false;
    const e = epoch.value;
    deciding.value[id] = true;
    approvalVersion++;
    error.value = "";
    try {
      await decideApproval(session.value, card.taskId, id, decision, reason);
      if (e !== epoch.value) return false;
      const current = data.value?.approvals.find((x) => x.id === id);
      if (current) { current.status = decision; current.reason = reason; }
      approvalVersion++;
      notice.value = decision === "APPROVED" ? "승인을 접수했습니다. 실행 결과는 카드별로 갱신됩니다." : "거절을 접수했습니다.";
      // A refresh failure does not turn a successfully saved decision into a failed save.
      return true;
    } catch (err) {
      if (e === epoch.value) error.value = (err as Error).message;
      return false;
    } finally {
      if (e === epoch.value) {
        deciding.value[id] = false; approvalVersion++;
        void refreshApprovals().catch(() => {});
      }
    }
  }
  function openTask(id: string) {
    activeTask.value = id;
    panel.value = true;
  }
  async function reset() {
    if (mode !== "mock") return;
    gateway.reset(scenario.value);
    epoch.value++;
    window.dispatchEvent(new Event("close-agent"));
    context.value = "";
    panel.value = false;
    await load();
  }
  async function switchEnvironment(id: string) {
    if (!session.value) return;
    await enter({ ...session.value }, id);
  }
  const issues = computed(() => data.value?.issues ?? []);
  return {
    switchEnvironment,
    issues,
    session,
    data,
    busy,
    error,
    notice,
    scenario,
    context,
    panel,
    activeTask,
    deciding,
    decide,
    refreshApprovals,
    openTask,
    epoch,
    buyer,
    load,
    enter,
    clear,
    save,
    reset,
  };
});
