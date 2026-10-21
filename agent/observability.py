"""Per-command traces and local evidence. Never changes business retry semantics."""
import asyncio
import contextvars
import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.callbacks import AsyncCallbackHandler

log = logging.getLogger("harness")
_current = contextvars.ContextVar("harness_execution", default=None)
_client = None
_client_checked = False
PRIVATE = {"messages", "content", "question", "answer", "reason", "excerpt", "text",
           "user_id", "userId", "unit_price", "amount", "added_cost", "saved_amount",
           "expedite_cost_per_day", "delay_penalty", "headers", "api_key", "password"}


def scrub(value):
    """Whitelist-shaped telemetry: free text and credentials never leave via traces."""
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if str(k) in PRIVATE or any(
            part in str(k).lower() for part in ("secret", "token", "authorization", "api_key"))
            and str(k) not in {"input_tokens", "output_tokens", "total_tokens", "tokens_before", "tokens_after", "inputTokens", "outputTokens"}
            else scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value[:30]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Arbitrary text may contain credentials or financial data embedded in JSON.
    return "[text omitted]"


def prompt_version():
    h = hashlib.sha256()
    for p in sorted((Path(__file__).parent / "prompts").glob("*.md")):
        h.update(p.name.encode()); h.update(p.read_bytes())
    return h.hexdigest()[:12]


def client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    if os.getenv("LANGSMITH_TRACING", "false").lower() != "true" or not os.getenv("LANGSMITH_API_KEY"):
        return None
    try:
        from langsmith import Client
        _client = Client(timeout_ms=1500, hide_inputs=scrub, hide_outputs=scrub,
                         hide_metadata=False, tracing_error_callback=lambda _: note("trace_delivery_failed"))
    except Exception:
        note("trace_setup_failed")
    return _client


def current():
    return _current.get()


def note(event, **fields):
    """Only call with identifiers, counters and fixed codes, never exception text."""
    run = current()
    data = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **(run.ids() if run else {}), **fields}
    log.info(json.dumps(data, ensure_ascii=False, default=str))


async def shutdown():
    """Bounded graceful flush; telemetry outages must not prevent shutdown."""
    if _client is None:
        return
    try:
        await asyncio.wait_for(asyncio.to_thread(_client.flush), timeout=3)
    except Exception:
        note("trace_flush_incomplete")


@dataclass
class Execution:
    task_id: str
    thread_id: str
    command_id: str
    kind: str
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    previous_trace_id: str | None = None
    trace_url: str | None = None
    enabled: bool = False
    started: float = field(default_factory=time.monotonic)
    input_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    retries: int = 0
    fallbacks: int = 0
    summaries: int = 0
    usage_observed: bool = False
    outcome: str = "COMPLETED"
    terminal_event: tuple | None = None
    finishing: bool = False
    command_completion: tuple | None = None

    def ids(self):
        return {"taskId": self.task_id, "threadId": self.thread_id, "traceId": self.trace_id,
                "commandId": self.command_id, "executionKind": self.kind}

    def metadata(self):
        return {"task_id": self.task_id, "thread_id": self.thread_id, "command_id": self.command_id,
                "execution_kind": self.kind, "previous_trace_id": self.previous_trace_id,
                "prompt_version": prompt_version(), "model_mode": os.getenv("AGENT_MODEL_MODE", "auto")}

    def summary(self):
        return {**self.ids(), "previousTraceId": self.previous_trace_id, "traceUrl": self.trace_url,
                "tracingEnabled": self.enabled, "outcome": self.outcome,
                "durationMs": int((time.monotonic() - self.started) * 1000),
                "llmCalls": self.llm_calls, "toolCalls": self.tool_calls, "toolErrors": self.tool_errors,
                "inputTokens": self.input_tokens if not self.llm_calls or self.usage_observed else None,
                "outputTokens": self.output_tokens if not self.llm_calls or self.usage_observed else None,
                "retryCount": self.retries, "fallbackCount": self.fallbacks, "summaryCount": self.summaries,
                "costUsd": None if self.llm_calls else 0, "costSource": "langsmith" if self.llm_calls else "no_llm_calls",
                "promptVersion": prompt_version()}


class EvidenceCallback(AsyncCallbackHandler):
    async def on_chain_start(self, serialized, inputs, *, run_id, **kwargs):
        note("node_start", node=kwargs.get("name") or (serialized or {}).get("name", "chain"), spanId=str(run_id))

    async def on_chain_end(self, outputs, *, run_id, **kwargs):
        note("node_end", spanId=str(run_id))

    async def on_chain_error(self, error, *, run_id, **kwargs):
        # GraphInterrupt is expected control flow, not a failed human approval.
        note("node_interrupt" if type(error).__name__ in {"GraphInterrupt", "NodeInterrupt"} else "node_error",
             errorCode=type(error).__name__, spanId=str(run_id))


CALLBACK = EvidenceCallback()


def graph_config(thread_id, recursion_limit):
    run = current()
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit,
            "metadata": run.metadata() if run else {"thread_id": thread_id}, "callbacks": [CALLBACK]}


def enrich(event):
    run = current()
    # An outbox event already belongs to its originating execution, not the delivery worker.
    return {**(run.ids() if run else {}), **event}


@asynccontextmanager
async def execution(task_id, thread_id, command_id, kind, previous_trace_id=None):
    run = Execution(task_id, thread_id, command_id, kind, previous_trace_id=previous_trace_id)
    token = _current.set(run)
    ctx = root = None
    c = client()
    try:
        from langsmith import tracing_context, trace
        ctx = tracing_context(enabled=c is not None, client=c)
        ctx.__enter__()
        if c:
            try:
                root = trace("bom.command", run_id=uuid.UUID(run.trace_id), client=c,
                             project_name=os.getenv("LANGSMITH_PROJECT", "bom-agent-dev"),
                             metadata=run.metadata(), inputs={"kind": kind})
                rt = root.__enter__()
                run.enabled = True
                # SDK URL formats vary by account/server; never fabricate a URL.
                # Resolve once per command, not per tool or outbox event.
                run.trace_url = await asyncio.wait_for(asyncio.to_thread(rt.get_url), timeout=2)
            except Exception:
                note("trace_link_unavailable")
        note("execution_start")
        yield run
    except BaseException as e:
        run.outcome = "INTERRUPTED" if isinstance(e, asyncio.CancelledError) else "ERROR"
        raise
    finally:
        note("execution_end", **run.summary())
        if root:
            try:
                root.new_run.end(outputs=run.summary(), error=run.outcome if run.outcome in {"ERROR", "INTERRUPTED", "HELD"} else None)
                root.__exit__(None, None, None)
            except Exception:
                note("trace_finalize_failed")
        if ctx:
            ctx.__exit__(None, None, None)
        _current.reset(token)


@contextmanager
def tool_span(name, args):
    """Set an error explicitly even if the application returns (False, message)."""
    cm = span = None
    run = current()
    if run:
        run.tool_calls += 1
    if run and run.enabled:
        try:
            from langsmith import trace
            cm = trace(name, run_type="tool", inputs=scrub(args), metadata=run.metadata(), client=client())
            span = cm.__enter__()
        except Exception:
            cm = None
    report = {"ok": False}
    start = time.monotonic()
    try:
        yield report
    finally:
        ms = int((time.monotonic() - start) * 1000)
        if run and not report["ok"]:
            run.tool_errors += 1
        note("tool_end", tool=name, ok=report["ok"], durationMs=ms, errorCode=report.get("errorCode"))
        if cm and span:
            try:
                span.end(outputs=scrub(report), error=None if report["ok"] else report.get("errorCode", "TOOL_ERROR"))
                cm.__exit__(None, None, None)
            except Exception:
                note("trace_finalize_failed")
