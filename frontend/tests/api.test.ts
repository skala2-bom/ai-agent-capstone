import { describe, expect, it } from "vitest";
import { toApprovals, toHistory, toIssues, toMetrics, toOptions, toWorkspace } from "../src/services/api";

// backend 실제 응답 모양 (contracts.md 2.1·3.3) → 화면 타입. 응답 칸 이름이 바뀌면 여기서 깨진다
describe("API 모드 응답 변환", () => {
  const inventory = {
    items: [
      { item_id: "FG-HUB", name: "IoT 허브", type: "FG", on_hand: 40, safety_stock: 0, supplier_name: null, lead_time_days: null, used_in: [] },
      { item_id: "MCU", name: "MCU", type: "PART", on_hand: 2700, safety_stock: 300, supplier_id: "SUP-SEMION", supplier_name: "세미온반도체", lead_time_days: 56, used_in: ["IoT 허브", "스마트 플러그"] },
    ],
  };

  it("재고·생산계획·발주를 화면 타입으로 바꾼다", () => {
    const w = toWorkspace(
      { plans: [{ plan_id: "PP-05", product_id: "FG-HUB", plan_date: "2026-12-14", qty: 400, status: "PLANNED" }] },
      inventory,
      { purchase_orders: [{ po_id: "PO-2609-001", item_id: "MCU", supplier_id: "SUP-SEMION", supplier_name: "세미온반도체", qty: 500, due_date: "2026-12-12", status: "OPEN" }] },
    );
    expect(w.items[1]).toEqual({ id: "MCU", name: "MCU", type: "PART", stock: 2700, safety: 300, supplier: "세미온반도체", lead: 56, shared: ["IoT 허브", "스마트 플러그"] });
    expect(w.plans[0]).toMatchObject({ id: "PP-05", product: "IoT 허브", date: "2026-12-14", qty: 400 });
    expect(w.purchases[0]).toMatchObject({ id: "PO-2609-001", item: "MCU", supplier: "세미온반도체", qty: 500, date: "2026-12-12", received: 0 });
  });

  it("승인 카드 하나로 승인과 대상 대안을 만든다", () => {
    const card = {
      approval_id: "APR-1", task_id: "T-1", tool: "split_po",
      args: { po_id: "PO-2609-001", split_qty: 100, new_due_date: "2026-11-25" },
      summary: "MCU 100개 먼저", amount: 390000, status: "PENDING", requested_by: "buyer", requested_at: "2026-11-02 10:00",
      before: { po_id: "PO-2609-001", item_id: "MCU", qty: 500, due_date: "2026-12-12" },
    };
    const { approvals, options } = toApprovals([card], new Map([["MCU", "MCU"]]));
    expect(approvals[0]).toMatchObject({ id: "APR-1", taskId: "T-1", optionId: "APR-1", requester: "박구매", status: "PENDING", beforeQty: 500, beforeDate: "2026-12-12" });
    expect(options[0]).toMatchObject({ id: "APR-1", action: "split", po: "PO-2609-001", qty: 100, date: "2026-11-25", item: "MCU", addedCost: 390000 });
  });

  it("AI 작업 목록을 작업 이력으로 바꾼다", () => {
    const h = toHistory([
      { taskId: "T-1", userId: "buyer", question: "MCU 해결 방법", trigger: "USER", status: "WAITING_APPROVAL", answer: null, createdAt: "2026-11-02 10:00" },
      { taskId: "T-2", userId: "buyer", question: "릴레이 지연 영향", trigger: "SYSTEM", status: "DONE", answer: "릴레이 1,000개 부족", createdAt: "2026-11-02 11:00" },
    ]);
    expect(h[0]).toEqual({ id: "T-1", at: "2026-11-02 10:00", title: "MCU 해결 방법", target: "AI 질문 · 박구매", status: "승인 대기", detail: "" });
    expect(h[1]).toMatchObject({ target: "자동 분석", status: "완료", detail: "릴레이 1,000개 부족" });
  });

  it("협력사 평가지표를 화면 표로 바꾸고 저장용 ID를 남긴다", () => {
    expect(toMetrics([{ supplier_id: "SUP-POWER", supplier_name: "파워링크전자", item_id: "RELAY", item_name: "릴레이", on_time_rate: 78.0, quality_score: 88, evaluated_by: "buyer", evaluated_at: "2026-10-15" }])[0])
      .toEqual({ supplierId: "SUP-POWER", itemId: "RELAY", supplier: "파워링크전자", item: "릴레이", onTime: 78, quality: 88, by: "박구매", date: "2026-10-15" });
  });

  it("영향 분석 결과를 위험 목록으로 — 제때 발주 가능한 결품은 추가발주 안내", () => {
    const items = [{ id: "MCU", name: "MCU", type: "PART" as const, stock: 0, safety: 0, supplier: "", lead: 0, shared: [] },
                   { id: "CASE-PLUG", name: "플러그 케이스", type: "PART" as const, stock: 0, safety: 0, supplier: "", lead: 0, shared: [] }];
    const plans = [{ id: "PP-05", product: "IoT 허브", date: "2026-11-25", qty: 400, status: "PLANNED" }];
    const issues = toIssues({
      policy_version: "v0.1",
      shortages: [{ rule_id: "INV-001", item_id: "MCU", plan_id: "PP-05", plan_date: "2026-11-25", projected_stock: -100, threshold: 0, qty: 100, orderable: false },
                  { rule_id: "INV-001", item_id: "MCU", plan_id: "PP-05", plan_date: "2026-11-25", projected_stock: -50, threshold: 0, qty: 50, orderable: true }],
      warnings: [{ rule_id: "INV-002", item_id: "MCU", plan_id: "PP-05", plan_date: "2026-11-25", projected_stock: 0, threshold: 300 }],
      excesses: [{ rule_id: "INV-003", item_id: "CASE-PLUG", date: "2026-11-27", qty: 300, window_days: 30, amount: null }],
    }, items, plans);
    expect(issues.map((i) => `${i.kind} ${i.item} ${i.plan} ${i.qty}`)).toEqual([
      "결품 MCU PP-05 · IoT 허브 100", "추가발주 MCU PP-05 · IoT 허브 50", "주의 MCU PP-05 · IoT 허브 300", "과잉 플러그 케이스  300"]);
  });

  it("해결안: 추천안이 1순위, 등급은 cost_level, 부작용이 있으면 해소로 보지 않는다", () => {
    const res = { options: [
      { option_id: "OPT-1", type: "SPLIT_PO", target_rule: "INV-001", item_id: "MCU", summary: "100개 먼저", args: { po_id: "PO-2609-001", split_qty: 100, new_due_date: "2026-11-25" },
        added_cost: null, cost_level: "MID", risk: "MID", resolves: true, side_effects: [], remaining_issues: [], recommended: true },
      { option_id: "OPT-2", type: "CANCEL_PO", target_rule: "INV-003", item_id: "MCU", summary: "취소", args: { po_id: "PO-X" },
        added_cost: 0, cost_level: "LOW", risk: "LOW", resolves: true, recommended: false,
        side_effects: [{ type: "NEW_SHORTAGE", item_id: "MCU", plan_id: "PP-02", increase_qty: 600 }], remaining_issues: [] },
    ] };
    const [a, b] = toOptions(res, "PLANNER", [], [{ id: "PO-X", item: "MCU", supplier: "", qty: 800, date: "", received: 0, passed: 0, failed: 0, status: "OPEN" }]);
    expect(a).toMatchObject({ id: "OPT-1", action: "split", qty: 100, grade: "MID", priority: 1, resolves: true, review: "확정", role: "PLANNER" });
    expect(a).not.toHaveProperty("addedCost");   // 생산관리는 금액 칸 자체가 없다
    expect(b).toMatchObject({ action: "cancel", qty: 800, resolves: false, priority: undefined });
    expect(b.impact).toContain("PP-02 +600개");
  });
});
