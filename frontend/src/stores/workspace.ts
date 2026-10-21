import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type { Session, Workspace, Command, Role } from "../types";
import { gateway, mode, fetchApprovals, decideApproval, reviewApproval } from "../services/api";
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
  // 해결안 비교 화면은 구매팀장만 본다 (팀 토의, decisions 6.25). 구매 담당자는 AI 채팅으로 승인을 요청한다
  const approver = computed(() => session.value?.role === "APPROVER");
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
    remember();
    await load();
  }
  // 새로고침해도 로그인을 잇는다. sessionStorage라 탭을 닫으면 지워지고, 토큰이 만료되면(8시간)
  // 첫 요청의 401 → session-expired → clear()로 지워진다. 저장소를 못 쓰는 브라우저면 예전처럼 메모리만 쓴다
  const KEY = "bom-session";
  function tokenSubject(token?: string): string | undefined {
    try {
      const payload = token?.split(".")[1];
      return payload ? JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/"))).sub : undefined;
    } catch {
      return undefined;
    }
  }
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
    activeTask.value = undefined;
    deciding.value = {};
    approvalVersion++;
    refreshSequence++;
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
  async function refreshApprovals() {
    // 모든 역할: 팀장은 전부, 다른 역할은 자기가 요청한 카드의 상태 변화를 본다 (backend가 거름, decisions 6.28)
    if (mode !== "api" || !session.value || !data.value) return;
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
  /** 요청자가 초안을 보내거나 보내지 않는다 (decisions 6.33). 카드마다 따로 잠근다 */
  async function review(id: string, send: boolean, problem: string, memo: string) {
    const card = data.value?.approvals.find((x) => x.id === id);
    if (!card || !session.value || deciding.value[id] || card.status !== "DRAFT") return false;
    if (send && !problem.trim()) return false;
    const e = epoch.value;
    deciding.value[id] = true;
    error.value = "";
    try {
      await reviewApproval(session.value, id, send, problem, memo);
      if (e !== epoch.value) return false;
      Object.assign(card, { status: send ? "PENDING" : "WITHDRAWN", problem, memo });
      approvalVersion++;
      notice.value = send ? "팀장에게 승인 요청을 보냈습니다." : "보내지 않기로 했습니다. AI가 그 품목을 정리합니다.";
      return true;
    } catch (err) {
      if (e === epoch.value) error.value = (err as Error).message;
      return false;
    } finally {
      if (e === epoch.value) {
        deciding.value[id] = false;
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
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) ?? "null");
    if (saved?.session) {
      // userId 칸이 생기기 전에 저장된 로그인이면 토큰(JWT의 sub)에서 채운다. 없으면 채팅창이 내 작업을 남의 작업으로 본다
      session.value = { ...saved.session, userId: saved.session.userId ?? tokenSubject(saved.session.token) };
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
    activeTask,
    deciding,
    decide,
    review,
    refreshApprovals,
    openTask,
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
