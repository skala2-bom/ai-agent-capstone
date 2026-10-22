export interface PlanChange {
  planId: string;
  before: { plan_date: string; qty: number; product_id?: string };
  after: { plan_date: string; qty: number; product_id?: string };
}
export interface CoordinationRequest {
  id: string; direction: "PLAN_TO_BUY" | "BUY_TO_PLAN"; status: string; revision: number;
  requesterId: string; requesterName: string; recipientRole: string; assigneeId?: string; assigneeName?: string;
  message: string; planOps: Record<string, unknown>[]; planChanges: PlanChange[];
  facts: {poId: string; before: {due_date: string}; after: {due_date: string; qty: number}; reason: string}[];
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
