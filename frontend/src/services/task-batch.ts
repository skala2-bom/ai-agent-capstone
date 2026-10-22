export type BatchAction = "CONFIRM" | "UNCONFIRM" | "NEEDS_EDIT" | "REPLAN" | "MANUAL" | "EXCLUDE" | "EXECUTE" | "CANCEL";
export interface BatchCommand {
  requestId: string; expectedRevision: number; action: BatchAction;
  approvalId?: string | null; reason?: string; args?: Record<string, unknown> | null;
}
export interface BatchEntry {
  approvalId?: string; proposalId: string; revision: number; target: string;
  poId: string; itemId: string; summary: string; source: "AI" | "MANUAL";
  reviewStatus: string; reviewedBy?: string;
  outcome?: {code: string; message: string; next_action: string};
}
export interface BatchState {
  mode: "LEGACY" | "BATCH"; status: string; revision?: number; stateVersion?: number;
  entries?: BatchEntry[]; canExecute?: boolean; error?: string;
  lastRequestId?: string; lastCommand?: BatchCommand;
}
export function reviewLabel(entry?: BatchEntry) {
  if (!entry) return "✕ 이전 제안";
  return ({CONFIRMED: "✓ 검토 완료", EXCLUDED: "✕ 실행 제외", CANCELLED: "작업 취소", REPLANNING: "✕ AI 재검토 중",
    UNAVAILABLE: "확인 필요", EXECUTED: "실행 완료", UNREVIEWED: "검토 대기"} as Record<string, string>)[entry.reviewStatus] ?? entry.reviewStatus;
}
export function batchLocked(state?: BatchState) {
  return !state || ["PREPARING", "EXECUTING", "UNKNOWN", "COMPLETED", "CANCELLED"].includes(state.status);
}
