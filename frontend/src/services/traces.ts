import type { ExecutionTrace } from "../types";

export function traceLink(link?: string | null): string | undefined {
  try {
    const url = new URL(link ?? "");
    return url.protocol === "https:" && !url.username && !url.password &&
      (url.hostname === "smith.langchain.com" || url.hostname.endsWith(".smith.langchain.com")) ? url.href : undefined;
  } catch { return undefined; }
}

export function toTrace(value: unknown): ExecutionTrace | undefined {
  if (!value || typeof value !== "object" || !("traceId" in value) || typeof value.traceId !== "string") return undefined;
  const raw = value as Record<string, unknown>;
  const trace: ExecutionTrace = { traceId: value.traceId };
  for (const key of ["previousTraceId", "executionKind", "outcome", "promptVersion"] as const)
    if (typeof raw[key] === "string") trace[key] = raw[key];
  for (const key of ["durationMs", "llmCalls", "toolCalls", "toolErrors", "inputTokens", "outputTokens", "retryCount", "fallbackCount", "summaryCount"] as const)
    if (typeof raw[key] === "number" && Number.isFinite(raw[key])) trace[key] = raw[key];
  trace.traceUrl = traceLink(typeof raw.traceUrl === "string" ? raw.traceUrl : null);
  trace.tracingEnabled = raw.tracingEnabled === true;
  trace.costUsd = typeof raw.costUsd === "number" ? raw.costUsd : null;
  return trace;
}
