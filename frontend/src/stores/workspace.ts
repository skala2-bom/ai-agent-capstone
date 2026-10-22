import { defineStore } from "pinia";
import { ref, computed } from "vue";
import type { Session, Workspace, Command, Role, TaskReviewReady, TaskReviewRequest } from "../types";
import { gateway, mode, fetchApprovals, decideApproval, reviewTask as submitTaskReview, request, toApprovals, toHistory } from "../services/api";
import type { BatchState, BatchAction, BatchCommand } from "../services/task-batch";
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
  const batchStates = ref<Record<string, BatchState>>({});
  const batchPending = ref<Record<string, BatchCommand | undefined>>({});
  const batchErrors = ref<Record<string, string>>({});
  async function refreshBatches() {
    if (mode !== "api" || !session.value || !data.value) return;
    const e = epoch.value;
    const ids = [...new Set(data.value.approvals.filter(a => a.status !== "DRAFT").map(a => a.taskId))];
    await Promise.all(ids.map(async id => {
      try {
        const state = await request<BatchState>(`/ai/tasks/${encodeURIComponent(id)}/batch`, session.value?.token);
        if (e !== epoch.value) return;
        if ((state.stateVersion ?? state.revision ?? -1) < (batchStates.value[id]?.stateVersion ?? batchStates.value[id]?.revision ?? -1)) return;
        batchStates.value[id] = state;
        if (["COMPLETED", "CANCELLED", "REJECTED"].includes(state.status)) { batchPending.value[id] = undefined; batchErrors.value[id] = ""; }
        if (state.lastRequestId === batchPending.value[id]?.requestId) batchPending.value[id] = undefined;
        else if (batchPending.value[id] && (state.revision ?? 0) > batchPending.value[id]!.expectedRevision) {
          batchPending.value[id] = undefined;
          batchErrors.value[id] = "다른 검토가 먼저 반영됐습니다. 최신 내용을 확인한 뒤 다시 선택하세요.";
        }
      } catch (err) { if (e === epoch.value) batchErrors.value[id] = (err as Error).message; }
    }));
  }
  async function sendBatch(taskId: string, action: BatchAction, approvalId?: string, extra: Partial<BatchCommand> = {}, retry?: BatchCommand) {
    if (batchPending.value[taskId] || session.value?.role !== "APPROVER") return false;
    const state = batchStates.value[taskId];
    if (state?.mode !== "BATCH" || state.revision === undefined) return false;
    const body: BatchCommand = retry ?? {requestId: crypto.randomUUID(), expectedRevision: state.revision, action,
      approvalId: approvalId ?? null, reason: "", args: null, ...extra};
    const e = epoch.value;
    batchPending.value[taskId] = body; batchErrors.value[taskId] = "";
    try {
      await request(`/ai/tasks/${encodeURIComponent(taskId)}/batch`, session.value.token, "POST", body);
      if (e !== epoch.value) return false;
      notice.value = action === "EXECUTE" ? "전체 재검증과 실행을 요청했습니다." : action === "CANCEL" ? "작업 전체 취소를 요청했습니다." : "검토 요청을 접수했습니다. 발주는 아직 변경하지 않았습니다.";
      await refreshApprovals();
      return true;
    } catch (err) {
      if (e === epoch.value) { batchErrors.value[taskId] = (err as Error).message; batchPending.value[taskId] = undefined; }
      return false;
    }
  }
  interface ReviewState {
    taskProblem: string; memos: Record<string, string>; ready?: TaskReviewReady;
    busy: boolean; error: string; loading: boolean;
    attempt?: { send: boolean; body: TaskReviewRequest };
  }
  // 입력 중인 메모는 메모리에만 둔다. 자동 저장하거나 브라우저 저장소에 쓰지 않는다.
  const taskReviews = ref<Record<string, ReviewState>>({});
  function taskReview(id: string): ReviewState {
    return taskReviews.value[id] ??= {
      taskProblem: [...(data.value?.approvals ?? [])].reverse().find(a => a.taskId === id && a.taskProblem)?.taskProblem ?? "",
      memos: {}, busy: false, error: "", loading: false,
    };
  }
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
        await refreshBatches();
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
    batchStates.value = {}; batchPending.value = {}; batchErrors.value = {};
    taskReviews.value = {};
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
    await refreshBatches();
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
  async function loadTaskReview(id: string) {
    if (!session.value || mode !== "api") return;
    const state = taskReview(id), e = epoch.value;
    if (state.loading) return;
    state.loading = true;
    try {
      const result = await request<{ reviewReady?: TaskReviewReady } & Record<string, unknown>>(`/ai/tasks/${encodeURIComponent(id)}`, session.value.token);
      if (e === epoch.value) {
        state.ready = result.reviewReady;
        if (data.value && typeof result.taskId === "string") {
          data.value.history = [...toHistory([result]), ...data.value.history.filter(t => t.id !== id)]
            .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
        }
      }
    } finally { if (e === epoch.value) state.loading = false; }
  }
  async function reviewTask(id: string, send: boolean) {
    const state = taskReview(id), e = epoch.value;
    if (!session.value || state.busy || (state.attempt && state.attempt.send !== send)) return false;
    const drafts = data.value?.approvals.filter(a => a.taskId === id && a.status === "DRAFT") ?? [];
    if (!state.attempt) {
      if (!state.ready || !drafts.length || drafts.some(a => !state.ready!.approvalIds.includes(a.id))) return false;
      if (send && !state.taskProblem.trim()) return false;
      state.attempt = { send, body: { requestId: crypto.randomUUID(), reviewId: state.ready.reviewId,
        taskProblem: state.taskProblem.trim(), memos: Object.fromEntries(drafts.map(a => [a.id, state.memos[a.id] ?? a.memo ?? ""])) } };
    }
    state.busy = true; state.error = ""; approvalVersion++;
    try {
      const result = await submitTaskReview(session.value, id, state.attempt.send, state.attempt.body);
      if (e !== epoch.value) return false;
      if (data.value) {
        const converted = toApprovals(result.cards, new Map(data.value.items.map(i => [i.id, i.name])));
        const ids = new Set(converted.approvals.map(a => a.id));
        data.value.approvals = [...data.value.approvals.filter(a => !ids.has(a.id)), ...converted.approvals];
        data.value.options = [...data.value.options.filter(o => !ids.has(o.id)), ...converted.options];
      }
      state.attempt = undefined;
      approvalVersion++;
      notice.value = send ? "작업의 해결안을 팀장에게 모두 보냈습니다." : "작업의 해결안을 보내지 않음으로 처리했습니다.";
      return true;
    } catch (err) {
      if (e === epoch.value) {
        state.error = (err as Error).message;
        // 응답 유실은 동일 requestId로 재전송한다. 명시적인 거부는 입력을 수정할 수 있게 한다.
        if (err instanceof Error && "status" in err && [400, 403, 404, 409].includes(Number(err.status))) state.attempt = undefined;
      }
      return false;
    } finally {
      if (e === epoch.value) {
        state.busy = false; approvalVersion++;
        void refreshApprovals().catch(() => {});
        void loadTaskReview(id).catch(() => {});
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
    batchStates, batchPending, batchErrors, refreshBatches, sendBatch,
    decide,
    reviewTask, taskReview, taskReviews, loadTaskReview,
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
