import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useWorkspace } from "../src/stores/workspace";
import type { TaskReviewRequest, Workspace } from "../src/types";
const api = vi.hoisted(() => ({ review: vi.fn(), fetch: vi.fn(), request: vi.fn() }));
vi.mock("../src/services/api", async (original) => ({
  ...await original<typeof import("../src/services/api")>(), mode: "api",
  reviewTask: api.review, fetchApprovals: api.fetch, request: api.request,
}));
function setup() {
  const w = useWorkspace();
  w.session = { userId: "buyer", role: "BUYER", name: "박구매", token: "token" };
  w.data = { plans: [], items: [], purchases: [], options: [], history: [], metrics: [],
    approvals: ["A", "B"].map(id => ({ id, taskId: "T", optionId: id, requester: "박구매", at: "2026-11-02",
      status: "DRAFT", problem: id + " 고정 문제" })) } as Workspace;
  const state = w.taskReview("T");
  state.ready = { reviewId: "ready", approvalIds: ["A", "B"] };
  state.taskProblem = "전체 생산 문제"; state.memos = { A: "첫 의견", B: "둘째 의견" };
  api.fetch.mockImplementation(() => new Promise(() => {}));
  api.request.mockResolvedValue({ reviewReady: state.ready });
  api.review.mockImplementation(async (_session, id: string, send: boolean, body: TaskReviewRequest) => ({ cards:
    Object.entries(body.memos).map(([approval_id, memo]) => ({ approval_id, task_id: id, status: send ? "PENDING" : "WITHDRAWN",
      taskProblem: body.taskProblem, memo, problem: approval_id + " 고정 문제", requested_by: "buyer", requested_at: "2026-11-02" })) }));
  return { w, state };
}
beforeEach(() => { vi.resetAllMocks(); setActivePinia(createPinia()); vi.stubGlobal("window", new EventTarget()); });
afterEach(() => vi.unstubAllGlobals());

describe("작업 단위 구매 검토", () => {
  it("한 요청에 모든 초안과 각각의 메모를 보내고 고정 문제는 전송하지 않는다", async () => {
    const { w } = setup();
    expect(await w.reviewTask("T", true)).toBe(true);
    expect(api.review).toHaveBeenCalledOnce();
    const body = api.review.mock.calls[0][3];
    expect(body).toMatchObject({ reviewId: "ready", taskProblem: "전체 생산 문제", memos: { A: "첫 의견", B: "둘째 의견" } });
    expect(body).not.toHaveProperty("problem");
    expect(w.data!.approvals.map(a => a.status)).toEqual(["PENDING", "PENDING"]);
    expect(w.data!.approvals.map(a => a.problem)).toEqual(["A 고정 문제", "B 고정 문제"]);
  });
  it("전체 등록 완료 전이나 새 초안이 추가된 동안에는 전송하지 않는다", async () => {
    const { w, state } = setup();
    state.ready = undefined;
    expect(await w.reviewTask("T", false)).toBe(false);
    state.ready = { reviewId: "old", approvalIds: ["A"] };
    expect(await w.reviewTask("T", true)).toBe(false);
    expect(api.review).not.toHaveBeenCalled();
  });
  it("큰 문제 설명은 전송에 필수지만 보내지 않음에는 없어도 된다", async () => {
    const { w, state } = setup(); state.taskProblem = " ";
    expect(await w.reviewTask("T", true)).toBe(false);
    expect(await w.reviewTask("T", false)).toBe(true);
    expect(w.data!.approvals.map(a => a.status)).toEqual(["WITHDRAWN", "WITHDRAWN"]);
  });
  it("연타와 반대 결정을 막고 응답 유실 때 같은 requestId와 내용으로 재시도한다", async () => {
    const { w, state } = setup();
    let reject!: (e: Error) => void;
    api.review.mockReturnValueOnce(new Promise((_, no) => { reject = no; }));
    const sending = w.reviewTask("T", true);
    expect(await w.reviewTask("T", true)).toBe(false);
    expect(await w.reviewTask("T", false)).toBe(false);
    reject(new Error("network")); expect(await sending).toBe(false);
    const firstBody = api.review.mock.calls[0][3];
    expect(state.attempt).toBeDefined();
    expect(await w.reviewTask("T", false)).toBe(false);
    expect(await w.reviewTask("T", true)).toBe(true);
    expect(api.review.mock.calls[1][3]).toEqual(firstBody);
    expect(state.attempt).toBeUndefined();
  });
  it("서버가 명시적으로 거부하면 입력을 유지하고 새 카드 구성을 다시 검토한다", async () => {
    const { w, state } = setup();
    api.review.mockRejectedValueOnce(Object.assign(new Error("카드 구성이 바뀌었습니다"), { status: 409 }));
    expect(await w.reviewTask("T", true)).toBe(false);
    expect(state.taskProblem).toBe("전체 생산 문제");
    expect(state.memos.B).toBe("둘째 의견");
    expect(state.attempt).toBeUndefined();
    expect(state.error).toBe("카드 구성이 바뀌었습니다");
    expect(w.data!.approvals.every(a => a.status === "DRAFT")).toBe(true);
  });
  it("이전 조회와 로그아웃 이후 응답으로 검토 상태가 되돌아가지 않는다", async () => {
    const { w } = setup();
    let resolve!: (v: unknown) => void;
    api.review.mockReturnValueOnce(new Promise(yes => { resolve = yes; }));
    const sending = w.reviewTask("T", true);
    w.clear(); resolve({ cards: [] });
    expect(await sending).toBe(false);
    expect(w.data).toBeUndefined();
    expect(w.taskReviews).toEqual({});
  });
});
