import { describe, it, expect } from "vitest";
import { taskStatus } from "../src/services/task-status";
import { toHistory } from "../src/services/api";

describe("classification result status", () => {
  it("keeps successful analysis and legacy clarification distinct", () => {
    expect(taskStatus("DONE", { intent: "IMPACT" })).toBe("완료");
    expect(taskStatus("DONE", { intent: "CLARIFY" })).toBe("추가 정보 필요");
    expect(taskStatus("DONE", null)).toBe("완료");
    expect(taskStatus("FAILED", { intent: "CLARIFY" })).toBe("실패");
  });
  it("shows the same status when a task is restored in history", () => {
    const tasks = ["NEEDS_INPUT", "INTERPRETATION_ERROR"].map((kind, i) => ({
      taskId: `T${i}`, status: "DONE", result: { intent: "CLARIFY", clarification: { kind } },
      userId: "buyer", createdAt: "2026-11-02", answer: "확인할 내용",
    }));
    expect(toHistory(tasks).map(t => t.status)).toEqual(["추가 정보 필요", "질문 해석 실패"]);
  });
});
