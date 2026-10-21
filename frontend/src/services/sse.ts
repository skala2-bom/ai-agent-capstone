import type { AgentEvent } from "../types";
export class EventAccumulator {
  seen = new Set<number>();
  answer = "";
  events: AgentEvent[] = [];
  approvals: AgentEvent[] = [];
  proposals: Record<string, AgentEvent> = {};
  eventIds = new Set<string>();
  status = "접수";
  terminal = false;
  constructor(public taskId: string) {}
  accept(e: AgentEvent) {
    if (
      e.taskId !== this.taskId ||
      !Number.isInteger(e.seq) ||
      this.seen.has(e.seq) ||
      this.terminal || (e.eventId !== undefined && this.eventIds.has(e.eventId))
    )
      return false;
    this.seen.add(e.seq);
    if (e.eventId) this.eventIds.add(e.eventId);
    this.events.push(e);
    if (e.type === "TOKEN") this.answer += e.text ?? "";
    if (e.type === "TOOL_START")
      this.status =
        e.label ??
        {
          analyze_impact: "영향 분석",
          read_doc: "근거 확인",
          get_inventory: "데이터 확인",
        }[e.tool ?? ""] ??
        "데이터 확인";
    if (e.type === "APPROVAL_REQUIRED") {
      this.status = "승인 대기";
      if (!this.approvals.some((a) => a.approvalId === e.approvalId))
        this.approvals.push(e);
    }
    if (e.type === "PROPOSAL_PROGRESS" && e.proposalId) {
      const previous = this.proposals[e.proposalId];
      if (!previous || (e.version ?? e.seq) > (previous.version ?? previous.seq))
        this.proposals[e.proposalId] = e;
      const values = Object.values(this.proposals);
      this.status = `실행 완료 ${values.filter(x => x.proposalStatus === 'EXECUTED').length}건 · 승인 대기 ${values.filter(x => x.proposalStatus === 'PENDING').length}건`;
    }
    if (e.type === "WORKFLOW_NOTICE") this.status = "확인 필요";
    if (e.type === "DONE") {
      this.answer = e.answer ?? this.answer;
      this.status = "완료";
      this.terminal = true;
    }
    if (e.type === "FAILED") {
      this.status = "실패";
      this.terminal = true;
    }
    return true;
  }
}
export function parseFrame(frame: string): AgentEvent | null {
  const data = frame
    .split("\n")
    .filter((l) => l.startsWith("data:"))
    .map((l) => l.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  const e = JSON.parse(data);
  if (
    ![
      "TOKEN",
      "TOOL_START",
      "TOOL_END",
      "APPROVAL_REQUIRED",
      "PROPOSAL_PROGRESS",
      "TASK_REVIEW_READY",
      "WORKFLOW_NOTICE",
      "TRACE",
      "DONE",
      "FAILED",
    ].includes(e.type) ||
    typeof e.taskId !== "string" ||
    !Number.isInteger(e.seq) ||
    typeof e.ts !== "string"
  )
    throw new Error("이벤트 형식이 올바르지 않습니다.");
  return e;
}
export function connect(
  taskId: string,
  token: string | undefined,
  onEvent: (e: AgentEvent) => void,
  onError: (m: string) => void,
) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let ended = false;
  async function run() {
    try {
      const r = await fetch(
        `/api/ai/tasks/${encodeURIComponent(taskId)}/stream`,
        {
          headers: {
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        },
      );
      if (!r.ok) {
        if (r.status === 401)
          window.dispatchEvent(new Event("session-expired"));
        if ([401, 403, 404].includes(r.status)) ended = true;
        throw new Error(`분석 연결 실패 (${r.status})`);
      }
      if (!r.body) throw new Error("스트림이 없습니다.");
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (!controller.signal.aborted) {
        const { value, done } = await reader.read();
        if (done) break;
        buf = (buf + decoder.decode(value, { stream: true })).replace(
          /\r\n/g,
          "\n",
        );
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          try {
            const e = parseFrame(frame);
            if (e && e.taskId === taskId) {
              onEvent(e);
              if (e.type === "DONE" || e.type === "FAILED") {
                ended = true;
                // 화면이 onEvent 안에서 stop()으로 이미 끊었으면 cancel이 AbortError를 낸다.
                // 정상 종료이므로 무시한다 (안 그러면 아래 catch가 "잘못된 이벤트"로 표시)
                await reader.cancel().catch(() => {});
                return;
              }
            }
          } catch {
            onError("잘못된 이벤트를 받았습니다. 재연결 시 다시 확인합니다.");
          }
        }
      }
      if (!ended) throw new Error("연결이 끊겼습니다. 재연결 중…");
    } catch (e) {
      if (!controller.signal.aborted)
        onError(e instanceof Error ? e.message : "연결 오류");
    } finally {
      if (!controller.signal.aborted && !ended) timer = setTimeout(run, 2000);
    }
  }
  void run();
  return () => {
    ended = true;
    controller.abort();
    clearTimeout(timer);
  };
}
