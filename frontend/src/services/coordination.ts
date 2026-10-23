export interface PlanChange {
  planId: string;
  before: { plan_date: string; qty: number; product_id?: string };
  after: { plan_date: string; qty: number; product_id?: string };
}
export interface CoordinationRequest {
  id: string; direction: "PLAN_TO_BUY" | "BUY_TO_PLAN"; status: string; revision: number;
  requesterId: string; requesterName: string; recipientRole: string; assigneeId?: string; assigneeName?: string;
  message: string; planOps: Record<string, unknown>[]; planChanges: PlanChange[];
  facts: {poId: string; itemName?: string; before: {due_date: string}; after: {due_date: string; qty: number}; reason?: string}[];
  analysis: Record<string, unknown>; createdAt: string; updatedAt: string; unread: boolean;
  managerVisible: boolean; needsAttention?: boolean; solutionTaskId?: string; solutionStatus?: string;
  decisionReason?: string; decidedName?: string; parentRequestId?: string;
  linkedRequestId?: string; linkedOutcome?: {status: string; reason: string};
  outcome?: {planChanges?: PlanChange[]};
}
export interface RequestPreview {
  sourceTaskId: string; direction: CoordinationRequest["direction"]; fingerprint: string;
  planOps: Record<string, unknown>[]; planChanges: PlanChange[]; facts: CoordinationRequest["facts"];
  analysis: Record<string, unknown>;
  requiresDelayConfirmation?: boolean;
}
export function canSendReceiptDelay(result?: Record<string, unknown>) {
  const delay = result?.receipt_delay as {reported?: boolean; changes?: unknown[]} | undefined;
  const ops = result?.whatif as {op?: string}[] | undefined;
  const impact = result?.impact as {shortages?: {orderable?: boolean}[]; excesses?: unknown[]} | undefined;
  return result?.intent === "IMPACT" && delay?.reported === true && !!delay.changes?.length
    && Array.isArray(ops) && ops.length === delay.changes.length && ops.every(o => o.op === "request_due_date_change")
    && (!!impact?.shortages?.some(s => !s.orderable) || !!impact?.excesses?.length);
}
export function receiptDelayMessage(preview: RequestPreview) {
  const facts = preview.facts.map(f => `${f.poId}${f.itemName ? ` (${f.itemName})` : ""}: ${f.before.due_date} → ${f.after.due_date} (${f.after.qty}개)`);
  const impact = preview.analysis as {shortages?: {plan_id: string; plan_date: string; item_name?: string; item_id: string; projected_stock: number}[]};
  const issues = (impact.shortages ?? []).map(s => `${s.plan_id} · ${s.plan_date}: ${s.item_name ?? s.item_id} ${Math.abs(s.projected_stock)}개 부족`);
  return [...facts, ...issues, "입고 지연에 따른 생산계획 조정을 검토해 주세요."].join("\n").slice(0, 4000);
}
export interface PlanPreview {
  previewId: string; changes: PlanChange[]; impact: Record<string, unknown>; canExecute: boolean; error?: string;
}
export const requestLabels: Record<string, string> = {
  OPEN: "접수 대기", IN_PROGRESS: "담당 검토 중", WAITING_REVIEW: "구매팀장 검토 중", WAITING_LINKED: "연결된 구매 요청 검토 중",
  COMPLETED: "처리 완료", REJECTED: "최종 거절", CANCELLED: "요청 취소",
};
export const requestClosed = (r?: {status: string}) => !!r && ["COMPLETED", "REJECTED", "CANCELLED"].includes(r.status);
export function canReject(r: CoordinationRequest, role?: string, userId?: string) {
  return !requestClosed(r) && r.status !== "WAITING_LINKED" && (r.direction === "PLAN_TO_BUY"
    ? role === "APPROVER" && r.managerVisible : role === "PLANNER" && r.assigneeId === userId);
}
export function planOperations(rows: {id: string; date: string; qty: number; originalDate: string; originalQty: number}[]) {
  return rows.flatMap(r => [
    ...(r.date !== r.originalDate ? [{op: "reschedule_plan", plan_id: r.id, new_date: r.date}] : []),
    ...(r.qty !== r.originalQty ? [{op: "change_plan_qty", plan_id: r.id, new_qty: r.qty}] : []),
  ] as Record<string, unknown>[]);
}
