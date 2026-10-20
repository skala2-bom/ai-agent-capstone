import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type { Session, Workspace, Command, Role } from "../types";
import { gateway, mode } from "../services/api";
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
  const epoch = ref(0);
  const buyer = computed(() => session.value?.role !== "PLANNER");
  let generation = 0;
  async function load() {
    const g = ++generation;
    busy.value = true;
    error.value = "";
    try {
      const result = await gateway.load(session.value!, scenario.value);
      if (g === generation) data.value = result;
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
    epoch,
    buyer,
    load,
    enter,
    clear,
    save,
    reset,
  };
});
