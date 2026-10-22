import { describe, it, expect } from "vitest";
import { batchLocked, reviewLabel, type BatchEntry } from "../src/services/task-batch";
describe("task batch review", () => {
  it("distinguishes review confirmation from execution", () => {
    expect(reviewLabel({reviewStatus: "CONFIRMED"} as BatchEntry)).toBe("✓ 검토 완료");
    expect(reviewLabel({reviewStatus: "EXECUTED"} as BatchEntry)).toBe("실행 완료");
    expect(reviewLabel()).toBe("✕ 이전 제안");
  });
  it("locks unknown and in-flight results while allowing correction after validation failure", () => {
    for (const status of ["EXECUTING", "UNKNOWN", "COMPLETED", "CANCELLED"]) expect(batchLocked({mode: "BATCH", status})).toBe(true);
    expect(batchLocked({mode: "BATCH", status: "BLOCKED"})).toBe(false);
  });
});
