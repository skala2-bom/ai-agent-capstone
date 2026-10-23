/** DONE ends transport, but a clarification is not a completed analysis. */
export function taskStatus(status: string, result?: unknown): string {
  if (status === "DONE" && result && typeof result === "object" && "intent" in result && result.intent === "CLARIFY") {
    const detail = "clarification" in result ? result.clarification : undefined;
    return detail && typeof detail === "object" && "kind" in detail && detail.kind === "INTERPRETATION_ERROR"
      ? "질문 해석 실패" : "추가 정보 필요";
  }
  return ({DONE: "완료", FAILED: "실패", WAITING_APPROVAL: "승인 대기", RUNNING: "진행 중", QUEUED: "접수"} as Record<string, string>)[status] ?? status;
}
