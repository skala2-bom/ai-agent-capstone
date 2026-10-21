import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useWorkspace } from "../src/stores/workspace";
import type { Workspace } from "../src/types";

const api = vi.hoisted(() => ({ load: vi.fn(), fetch: vi.fn(), decide: vi.fn() }));
vi.mock("../src/services/api", () => ({ mode: "api", gateway: { load: api.load },
  fetchApprovals: api.fetch, decideApproval: api.decide }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
function fixture(): Workspace {
  return { plans: [], items: [], purchases: [], options: [], history: [], metrics: [],
    approvals: ["A", "B"].map((id) => ({ id, taskId: "T", optionId: id, requester: "buyer", at: "2026-11-02", status: "PENDING" })) };
}
function setup() {
  const w = useWorkspace();
  w.session = { role: "APPROVER", name: "팀장", token: "token" };
  w.data = fixture();
  // Default background poll remains pending to expose late-response races explicitly.
  api.fetch.mockImplementation(() => new Promise(() => {}));
  return w;
}
beforeEach(() => { vi.clearAllMocks(); setActivePinia(createPinia()); vi.stubGlobal("window", new EventTarget()); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("독립 승인과 상태 갱신", () => {
  it("두 카드를 동시에 접수하고 전체 workspace를 재계산하지 않는다", async () => {
    const w = setup();
    const a = deferred<void>(), b = deferred<void>();
    api.decide.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    const first = w.decide("A", "APPROVED", ""), second = w.decide("B", "REJECTED", "다른 안");
    expect(api.decide).toHaveBeenCalledTimes(2);
    expect(w.deciding).toMatchObject({ A: true, B: true });
    expect(w.busy).toBe(false);
    a.resolve(); b.resolve();
    expect(await first).toBe(true); expect(await second).toBe(true);
    expect(w.data!.approvals.map((a) => a.status)).toEqual(["APPROVED", "REJECTED"]);
    expect(api.load).not.toHaveBeenCalled();
  });
  it("같은 카드 연타와 사유 없는 거절은 전송하지 않는다", async () => {
    const w = setup(), pending = deferred<void>();
    api.decide.mockReturnValue(pending.promise);
    const first = w.decide("A", "APPROVED", "");
    expect(await w.decide("A", "APPROVED", "")).toBe(false);
    expect(await w.decide("B", "REJECTED", "  ")).toBe(false);
    expect(api.decide).toHaveBeenCalledOnce();
    pending.resolve(); await first;
  });
  it("승인 전에 시작한 오래된 조회가 승인 상태를 되돌리지 않는다", async () => {
    const w = setup(), poll = deferred<{ approvals: Workspace["approvals"]; options: [] }>();
    api.fetch.mockReturnValueOnce(poll.promise);
    const refresh = w.refreshApprovals();
    api.decide.mockResolvedValue(undefined);
    await w.decide("A", "APPROVED", "");
    poll.resolve({ approvals: fixture().approvals, options: [] }); await refresh;
    expect(w.data!.approvals[0].status).toBe("APPROVED");
  });
  it("오래된 전체 조회도 최신 카드 상태를 덮어쓰지 않는다", async () => {
    const w = setup(), load = deferred<Workspace>();
    api.load.mockReturnValue(load.promise);
    const loading = w.load();
    api.decide.mockResolvedValue(undefined);
    await w.decide("A", "APPROVED", "");
    load.resolve(fixture()); await loading;
    expect(w.data!.approvals[0].status).toBe("APPROVED");
  });
  it("로그아웃 후 도착한 응답은 이전 세션을 복원하지 않는다", async () => {
    const w = setup(), pending = deferred<void>();
    api.decide.mockReturnValue(pending.promise);
    const saved = w.decide("A", "APPROVED", "");
    w.clear(); pending.resolve();
    expect(await saved).toBe(false);
    expect(w.data).toBeUndefined(); expect(w.session).toBeUndefined();
  });
  it("결정 저장 후 조회 실패를 결정 실패로 보여주지 않는다", async () => {
    const w = setup();
    api.decide.mockResolvedValue(undefined); api.fetch.mockRejectedValue(new Error("offline"));
    expect(await w.decide("A", "APPROVED", "")).toBe(true);
    expect(w.data!.approvals[0].status).toBe("APPROVED");
    expect(w.error).toBe("");
  });
});
