"""Send exactly one synthetic base trace. No LLM, embeddings or business writes.

Run with AGENT_MODEL_MODE=fake and provider keys removed. A PAT is required to
verify ingestion by reading the resulting run (tracing-only keys cannot read).
"""
import asyncio
import json
import logging
import sys

import observability as O


async def main():
    logging.getLogger("langsmith").setLevel(logging.CRITICAL)
    c = O.client()
    if c is None:
        print(json.dumps({"ok": False, "error": "TRACING_NOT_CONFIGURED"}))
        return 1
    async with O.execution("harness-connection-check", "harness-connection-check", "synthetic", "SMOKE") as run:
        from langgraph.graph import StateGraph, START, END
        graph = StateGraph(dict)
        async def node(state):
            with O.tool_span("synthetic_tool", {"unit_price": 987654321, "headers": {"Authorization": "synthetic-secret"}}) as report:
                report["ok"] = True
            return {"checked": True}
        graph.add_node("synthetic_node", node)
        graph.add_edge(START, "synthetic_node"); graph.add_edge("synthetic_node", END)
        await graph.compile().ainvoke({}, O.graph_config(run.thread_id, 10))
    await asyncio.to_thread(c.flush)
    for attempt in range(6):
        try:
            stored = await asyncio.to_thread(c.read_run, run.trace_id, load_child_runs=True)
            children = stored.child_runs or []
            serialized = stored.model_dump_json()
            secret_safe = "synthetic-secret" not in serialized and "987654321" not in serialized
            result = {"ok": secret_safe and bool(children), "traceId": run.trace_id, "traceUrl": run.trace_url,
                      "childRuns": len(children), "scrubbed": secret_safe, "llmCalls": 0,
                      "modelCostUsd": 0, "note": "synthetic base trace; no LLM or embedding calls"}
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["ok"] else 1
        except Exception as error:
            if attempt == 5 or type(error).__name__ in {"LangSmithAuthError", "LangSmithPermissionError"}:
                print(json.dumps({"ok": False, "error": type(error).__name__, "traceId": run.trace_id,
                                  "tracingEnabled": run.enabled, "note": "No credential or response body printed"}))
                return 1
            await asyncio.sleep(1)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
