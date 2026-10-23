import {describe, it, expect} from "vitest";
import {canReject, requestClosed, planOperations, canSendReceiptDelay, receiptDelayMessage, type CoordinationRequest} from "../src/services/coordination";
import {batchLocked} from "../src/services/task-batch";
describe("department request authority and staging", () => {
  it("offers handoff only for reported pure receipt delays with actionable problems", () => {
    const result = {intent:"IMPACT",whatif:[{op:"request_due_date_change"}],receipt_delay:{reported:true,changes:[{}]},impact:{shortages:[{orderable:false}]}};
    expect(canSendReceiptDelay(result)).toBe(true);
    expect(canSendReceiptDelay({...result,receipt_delay:{reported:false,changes:[{}]}})).toBe(false);
    expect(canSendReceiptDelay({...result,whatif:[{op:"reschedule_plan"}]})).toBe(false);
    expect(canSendReceiptDelay({...result,impact:{shortages:[{orderable:true}]}})).toBe(false);
    expect(canSendReceiptDelay({...result,intent:"QA"})).toBe(false);
    expect(canSendReceiptDelay()).toBe(false);
  });
  it("prepares a request from actual delay dates and calculated shortages", () => {
    const message = receiptDelayMessage({sourceTaskId:"t",direction:"BUY_TO_PLAN",fingerprint:"f",planOps:[],planChanges:[],
      facts:[{poId:"PO-2609-002",itemName:"Wi-Fi 칩",before:{due_date:"2026-11-13"},after:{due_date:"2026-11-20",qty:1000}}],
      analysis:{shortages:[{plan_id:"PP-01",plan_date:"2026-11-16",item_id:"WIFI",projected_stock:-1000}]}});
    expect(message).toContain("2026-11-13 → 2026-11-20"); expect(message).toContain("PP-01 · 2026-11-16: WIFI 1000개 부족");
  });
  it("separates manager final rejection from buyer review", () => {
    const r = {direction:"PLAN_TO_BUY",status:"WAITING_REVIEW",managerVisible:true,assigneeId:"buyer"} as CoordinationRequest;
    expect(canReject(r,"BUYER","buyer")).toBe(false);
    expect(canReject(r,"APPROVER","approver")).toBe(true);
    expect(canReject({...r,managerVisible:false},"APPROVER","approver")).toBe(false);
    expect(canReject({...r,status:"COMPLETED"},"APPROVER","approver")).toBe(false);
  });
  it("only the assigned planner can reject a production adjustment", () => {
    const r = {direction:"BUY_TO_PLAN",status:"IN_PROGRESS",assigneeId:"planner"} as CoordinationRequest;
    expect(canReject(r,"PLANNER","planner")).toBe(true);
    expect(canReject(r,"PLANNER","other")).toBe(false);
    expect(canReject(r,"APPROVER","approver")).toBe(false);
    expect(canReject({...r,status:"WAITING_LINKED"},"PLANNER","planner")).toBe(false);
  });
  it("stages only changed dates and quantities across several plans", () => {
    expect(planOperations([{id:"P1",date:"2026-11-27",qty:600,originalDate:"2026-11-23",originalQty:600},
      {id:"P2",date:"2026-11-30",qty:500,originalDate:"2026-11-30",originalQty:800}])).toEqual([
      {op:"reschedule_plan",plan_id:"P1",new_date:"2026-11-27"},{op:"change_plan_qty",plan_id:"P2",new_qty:500}]);
  });
  it("keeps attention states open and final rejection locked", () => {
    expect(requestClosed({status:"IN_PROGRESS"})).toBe(false);
    expect(requestClosed({status:"PARTIALLY_COMPLETED"})).toBe(false);
    expect(requestClosed({status:"REJECTED"})).toBe(true);
    expect(batchLocked({mode:"BATCH",status:"REJECTED"})).toBe(true);
  });
});
