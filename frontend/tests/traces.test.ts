import { describe, expect, it } from "vitest";
import { traceLink, toTrace } from "../src/services/traces";
import { toHistory } from "../src/services/api";
import { EventAccumulator, parseFrame } from "../src/services/sse";

describe("실행 추적", () => {
  it("허용된 LangSmith 링크만 표시한다", () => {
    expect(traceLink("https://smith.langchain.com/o/1/projects/p/2/r/3")).toContain("/r/3");
    for (const link of ["javascript:alert(1)", "https://smith.langchain.com.evil.test/", "https://evil.test/", "https://user:pass@smith.langchain.com/"])
      expect(traceLink(link)).toBeUndefined();
  });
  it("옛 이력은 유지하고 새 이력에는 추적과 미집계 비용을 전달한다", () => {
    expect(toTrace(null)).toBeUndefined();
    const [history] = toHistory([{ taskId: "T", trace: { traceId: "R", llmCalls: 2, costUsd: null } }]);
    expect(history.trace).toMatchObject({ traceId: "R", llmCalls: 2, costUsd: null });
  });
  it("TRACE는 SSE에서 상태·최종 답변을 바꾸지 않는다", () => {
    const event = { type: "TRACE", taskId: "T", seq: 1, ts: "2026-10-21", traceId: "R" };
    const parsed = parseFrame(`data: ${JSON.stringify(event)}`)!;
    const stream = new EventAccumulator("T");
    expect(stream.accept(parsed)).toBe(true);
    expect(stream.status).toBe("접수");
    expect(stream.terminal).toBe(false);
    stream.accept({ type: "DONE", taskId: "T", seq: 2, ts: event.ts, answer: "끝" });
    expect(stream.answer).toBe("끝");
  });
});
