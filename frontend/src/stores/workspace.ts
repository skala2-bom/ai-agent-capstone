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
  // 해결안 비교 화면은 구매팀장만 본다 (팀 토의, decisions 6.25). 구매 담당자는 AI 채팅으로 승인을 요청한다
  const approver = computed(() => session.value?.role === "APPROVER");
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
    remember();
    await load();
  }
  // 새로고침해도 로그인을 잇는다. sessionStorage라 탭을 닫으면 지워지고, 토큰이 만료되면(8시간)
  // 첫 요청의 401 → session-expired → clear()로 지워진다. 저장소를 못 쓰는 브라우저면 예전처럼 메모리만 쓴다
  const KEY = "bom-session";
  function remember() {
    try {
      if (session.value) sessionStorage.setItem(KEY, JSON.stringify({ session: session.value, scenario: scenario.value }));
      else sessionStorage.removeItem(KEY);
    } catch {
      /* 저장 못 해도 지금 탭은 그대로 쓴다 */
    }
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
    remember();
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
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (saved?.session) {
      session.value = saved.session;
      scenario.value = saved.scenario ?? scenario.value;
      void load();
    }
  } catch {
    /* 저장된 값이 깨졌으면 로그인 화면부터 */
  }
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
    approver,
    load,
    enter,
    clear,
    save,
    reset,
  };
});
