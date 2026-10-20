import { describe, it, expect, vi, afterEach } from "vitest";
import { EventAccumulator, parseFrame, connect } from "../src/services/sse";
import type { AgentEvent } from "../src/types";
const event = (
  seq: number,
  type: AgentEvent["type"],
  extra: Partial<AgentEvent> = {},
): AgentEvent => ({ seq, type, taskId: "T1", ts: "2026-11-02", ...extra });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe("SSE lifecycle", () => {
  it("deduplicates replay by task/seq and replaces tokens with final answer", () => {
    const a = new EventAccumulator("T1");
    a.accept(event(1, "TOKEN", { text: "hello" }));
    a.accept(event(1, "TOKEN", { text: "hello" }));
    a.accept(event(2, "TOKEN", { text: "wrong", taskId: "T2" }));
    expect(a.answer).toBe("hello");
    a.accept(event(2, "DONE", { answer: "hello final" }));
    expect(a.answer).toBe("hello final");
    expect(a.terminal).toBe(true);
  });
  it("keeps multiple approvals waiting without terminal timeout", () => {
    const a = new EventAccumulator("T1");
    a.accept(event(1, "APPROVAL_REQUIRED", { approvalId: "A1" }));
    a.accept(event(2, "APPROVAL_REQUIRED", { approvalId: "A2" }));
    expect(a.approvals).toHaveLength(2);
    expect(a.terminal).toBe(false);
    expect(a.status).toBe("승인 대기");
  });
  it("rejects malformed JSON and ignores heartbeats", () => {
    expect(parseFrame(": heartbeat")).toBeNull();
    expect(() => parseFrame("data: {bad")).toThrow();
    expect(() => parseFrame('data: {"type":"DONE"}')).toThrow();
    expect(parseFrame(`data: ${JSON.stringify(event(1, "DONE"))}`)?.type).toBe(
      "DONE",
    );
  });
  it("closes on DONE, sends authorization header and never puts JWT in URL", async () => {
    const fetch = vi.fn(
      async (_url: string, _options: RequestInit) =>
        new Response(
          new ReadableStream({
            start(c) {
              c.enqueue(
                new TextEncoder().encode(
                  `data: ${JSON.stringify(event(1, "DONE"))}\n\n`,
                ),
              );
              c.close();
            },
          }),
        ),
    );
    vi.stubGlobal("fetch", fetch);
    const receive = vi.fn();
    const close = connect("T1", "secret", receive, vi.fn());
    await new Promise((r) => setTimeout(r, 30));
    expect(receive).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0]?.[0]).not.toContain("secret");
    expect(fetch.mock.calls[0]?.[1].headers).toMatchObject({
      Authorization: "Bearer secret",
    });
    close();
  });
  it("aborts stream on explicit cleanup", () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", (_url: unknown, opts: RequestInit) => {
      signal = opts.signal as AbortSignal;
      return new Promise(() => {});
    });
    const close = connect(
      "T1",
      undefined,
      () => {},
      () => {},
    );
    close();
    expect(signal?.aborted).toBe(true);
  });
});

describe("SSE recovery", () => {
  it("reconnects, accepts full replay once and parses split CRLF boundaries", async () => {
    vi.useFakeTimers();
    let attempt = 0;
    const fetch = vi.fn(async () => {
      attempt++;
      const frames =
        `data: ${JSON.stringify(event(1, "TOKEN", { text: "once" }))}\r\n\r\n` +
        (attempt > 1
          ? `data: ${JSON.stringify(event(2, "DONE", { answer: "final" }))}\r\n\r\n`
          : "");
      return new Response(
        new ReadableStream({
          start(c) {
            for (const char of frames)
              c.enqueue(new TextEncoder().encode(char));
            c.close();
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetch);
    const accumulator = new EventAccumulator("T1");
    const close = connect(
      "T1",
      undefined,
      (e) => accumulator.accept(e),
      () => {},
    );
    await vi.advanceTimersByTimeAsync(2100);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(accumulator.events).toHaveLength(2);
    expect(accumulator.answer).toBe("final");
    expect(accumulator.terminal).toBe(true);
    await vi.advanceTimersByTimeAsync(10000);
    expect(fetch).toHaveBeenCalledTimes(2);
    close();
  });
  it("FAILED terminates and later tokens cannot revive a failed task", () => {
    const a = new EventAccumulator("T1");
    a.accept(event(1, "FAILED", { message: "분석 실패" }));
    a.accept(event(2, "TOKEN", { text: "late" }));
    expect(a.status).toBe("실패");
    expect(a.answer).toBe("");
    expect(a.terminal).toBe(true);
  });
});
