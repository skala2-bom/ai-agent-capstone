import { describe, it, expect } from "vitest";
import { fixture, scenarios } from "../src/mocks/fixtures";
import { mutate, selectable } from "../src/services/workflow";
import { gateway } from "../src/services/api";
const planner = { role: "PLANNER" as const, name: "김계획" },
  buyer = { role: "BUYER" as const, name: "박구매" },
  boss = { role: "APPROVER" as const, name: "정팀장" };
describe("role and workflow boundaries", () => {
  it("수동 입고 수량·날짜 변경과 작업 소유자를 기록하고 잘못된 수정은 거부한다", () => {
    const w = fixture("APPROVER");
    const id = w.purchases[0].id;
    const command = { type: "purchase" as const, id, date: "2026-11-25", qty: 600, reason: "협력사 확정" };
    expect(() => mutate(w, command, planner)).toThrow();
    expect(() => mutate(w, { ...command, qty: 1.5 }, buyer)).toThrow();
    expect(() => mutate(w, { ...command, reason: " " }, buyer)).toThrow();
    mutate(w, command, { ...buyer, userId: "buyer-a" });
    expect(w.purchases[0]).toMatchObject({ date: command.date, qty: 600 });
    expect(w.history[0]).toMatchObject({ title: "입고 정보 수동 변경", userId: "buyer-a" });
    expect(w.analysisStale).toBe(true);
    expect(() => mutate(w, command, buyer)).toThrow();
    w.purchases[0].status = "CANCELLED";
    expect(() => mutate(w, { ...command, qty: 700 }, buyer)).toThrow();
  });

  it("같은 역할의 다른 사용자에게도 목 작업 이력을 노출하지 않는다", async () => {
    gateway.reset();
    const a = { ...buyer, userId: "buyer-a" }, b = { ...buyer, userId: "buyer-b" };
    const initial = await gateway.load(a);
    await gateway.save(a, { type: "purchase", id: initial.purchases[0].id, date: "2026-11-25", qty: 600, reason: "확정" });
    expect((await gateway.load(a)).history).toHaveLength(1);
    expect((await gateway.load(b)).history).toHaveLength(0);
    expect((await gateway.load({ ...boss, userId: "approver" })).history).toHaveLength(0);
  });
  it("rejects unreviewed, stale and unresolved candidates", () => {
    const w = fixture("APPROVER");
    for (const id of ["OPT-3", "OPT-4"])
      expect(() =>
        mutate(
          w,
          { type: "priority", id, priority: 1, reason: "납기" },
          planner,
        ),
      ).toThrow();
    w.options[0].revision++;
    expect(selectable(w.options[0])).toBe(false);
  });
  it("allows B before A, self approval, prevents duplicates and does not execute on approval", () => {
    const w = fixture("APPROVER");
    mutate(
      w,
      { type: "priority", id: "OPT-1", priority: 1, reason: "납기 준수" },
      planner,
    );
    mutate(
      w,
      {
        type: "priority",
        id: "OPT-2",
        priority: 2,
        reason: "비용 절감 예비안",
      },
      planner,
    );
    mutate(w, { type: "request", id: "OPT-1" }, boss);
    expect(() => mutate(w, { type: "request", id: "OPT-1" }, boss)).toThrow();
    const id = w.approvals[0].id;
    expect(() =>
      mutate(
        w,
        { type: "approval", id, decision: "REJECTED", reason: " " },
        boss,
      ),
    ).toThrow();
    mutate(w, { type: "approval", id, decision: "APPROVED", reason: "" }, boss);
    expect(w.purchases[0].qty).toBe(500);
    expect(w.approvals[0].status).toBe("APPROVED");
    mutate(w, { type: "execution", id }, boss);
    expect(w.purchases[0].qty).toBe(400);
    expect(w.purchases.at(-1)?.qty).toBe(100);
    expect(w.approvals[0].status).toBe("EXECUTED");
  });
  it("rejection creates a fresh unreviewed candidate", () => {
    const w = fixture("APPROVER");
    mutate(
      w,
      { type: "priority", id: "OPT-1", priority: 1, reason: "납기" },
      planner,
    );
    mutate(w, { type: "request", id: "OPT-1" }, buyer);
    mutate(
      w,
      {
        type: "approval",
        id: w.approvals[0].id,
        decision: "REJECTED",
        reason: "확약 재확인",
      },
      boss,
    );
    expect(w.options.at(-1)?.review).toBe("미검수");
    expect(w.options.at(-1)?.grade).toBeUndefined();
  });
  it("does not allow unreceived stock to pass inspection", () => {
    expect(() =>
      mutate(
        fixture("APPROVER"),
        { type: "receipt", id: "PO-2609-001", passed: 500, failed: 0 },
        buyer,
      ),
    ).toThrow();
  });
  it("returns role specific DTOs and no price fields to planner", async () => {
    gateway.reset();
    const p = await gateway.load(planner),
      b = await gateway.load(buyer),
      a = await gateway.load(boss);
    expect(JSON.stringify(p)).not.toMatch(/addedCost|unitPrice|costEvidence/);
    expect(JSON.stringify(b)).not.toContain("unitPrice");
    expect(JSON.stringify(a)).toContain("unitPrice");
  });
  it("keeps scenario dates and policy provenance explicit", () => {
    expect(scenarios[1].issues[1].date).toBe("2026-11-30");
    expect(scenarios[3].issues[1].date).toBe("2027-01-04");
    expect(scenarios[3].policy_version).toContain("30d");
  });
});
