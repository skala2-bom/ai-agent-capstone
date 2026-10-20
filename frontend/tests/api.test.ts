import { describe, expect, it } from "vitest";
import { toApprovals, toHistory, toMetrics, toWorkspace } from "../src/services/api";

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
});
