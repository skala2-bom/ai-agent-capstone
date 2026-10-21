"""Offline harness checks: no provider keys, no LLM or embedding requests."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

import context_budget as C
import model_runtime as M
import observability as O
import graph as G


class StatusError(Exception):
    def __init__(self, code):
        self.status_code = code


class Answer(BaseModel):
    option_id: str


class Model:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls, self.bindings = [], []
        self.model_name = "offline-fixture"

    def bind_tools(self, tools):
        self.bindings.append(("tools", tools)); return self

    def with_structured_output(self, schema, **kwargs):
        self.bindings.append(("schema", schema, kwargs)); return self

    async def ainvoke(self, messages):
        self.calls.append(messages)
        result = next(self.replies)
        if isinstance(result, Exception):
            raise result
        return result


class Budget(unittest.TestCase):
    def test_long_tool_view_is_limited_without_changing_canonical_data(self):
        canonical = {"options": [{"args": {"new_qty": 123}, "details": "긴 결과" * 2000}]}
        original = copy.deepcopy(canonical)
        view = C.tool_content(canonical, 4000)
        self.assertEqual(len(view), 4000)
        self.assertIn("일부 생략", view)
        self.assertEqual(canonical, original)

    def test_complete_old_exchange_is_summarized_and_current_tool_pair_survives(self):
        messages = [SystemMessage("숫자를 만들지 말 것"), HumanMessage("이전 질문" * 4000),
                    AIMessage("이전 답변"), HumanMessage("현재 질문"),
                    AIMessage("", tool_calls=[{"id": "c1", "name": "get_item", "args": {}}]),
                    ToolMessage("현재 결과", tool_call_id="c1")]
        original = copy.deepcopy(messages)
        view = C.prepare_messages(messages, budget=2000)
        self.assertLessEqual(C.estimate_tokens(view), 2000)
        self.assertIn("이전 대화 요약", view[1].content)
        self.assertEqual(view[-3:], messages[-3:])
        self.assertEqual(messages, original)

    def test_oversized_current_question_and_tool_schema_fail_before_model_call(self):
        with self.assertRaises(C.ContextBudgetExceeded):
            C.prepare_messages([HumanMessage("필수 질문" * 1000)], budget=300)
        with self.assertRaises(C.ContextBudgetExceeded):
            C.prepare_messages([HumanMessage("작은 질문")], tools=[{"description": "schema" * 1000}], budget=300)

    def test_orphan_tool_result_is_rejected(self):
        with self.assertRaises(C.ContextBudgetExceeded):
            C.prepare_messages([ToolMessage("비정상 결과", tool_call_id="missing")])


class HarnessAsync(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LANGSMITH_TRACING": "false", "AGENT_MODEL_MODE": "fake"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.c = patch.object(O, "_client", None)
        self.c.start(); self.addCleanup(self.c.stop)
        self.checked = patch.object(O, "_client_checked", True)
        self.checked.start(); self.addCleanup(self.checked.stop)

    async def test_429_twice_then_success_has_one_retry_layer_and_usage(self):
        reply = AIMessage("성공", usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
        model = Model([StatusError(429), StatusError(429), reply])
        sleep = AsyncMock()
        async with O.execution("t", "th", "cmd", "START") as run:
            result = await M.invoke(model, None, [HumanMessage("검증")], sleep=sleep)
            self.assertEqual(result, reply)
            self.assertEqual((len(model.calls), run.retries, sleep.await_count), (3, 2, 2))
            self.assertEqual((run.input_tokens, run.output_tokens), (10, 3))

    async def test_503_exhaustion_binds_tools_on_fallback(self):
        main = Model([StatusError(503)] * 3)
        fallback = Model([AIMessage("복구")])
        tools = [G.analyze_impact_tool]
        async with O.execution("t", "th", "cmd", "START") as run:
            await M.invoke(main, fallback, [HumanMessage("검증")], tools=tools, sleep=AsyncMock())
            self.assertEqual((run.retries, run.fallbacks, run.llm_calls), (2, 1, 4))
            self.assertEqual(main.bindings, fallback.bindings)

    async def test_schema_is_bound_on_each_model_and_raw_usage_preserved(self):
        main = Model([StatusError(503)] * 3)
        fallback = Model([{"raw": AIMessage("", usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}),
                           "parsed": Answer(option_id="O1"), "parsing_error": None}])
        async with O.execution("t", "th", "cmd", "START") as run:
            result = await M.invoke(main, fallback, [HumanMessage("검증")], schema=Answer, sleep=AsyncMock())
            self.assertEqual(result.option_id, "O1")
            self.assertEqual(main.bindings, fallback.bindings)
            self.assertEqual(run.output_tokens, 2)

    async def test_all_models_fail_and_auth_errors_are_not_retried(self):
        main, fallback = Model([StatusError(503)] * 3), Model([StatusError(503)] * 3)
        with self.assertRaises(StatusError):
            await M.invoke(main, fallback, [HumanMessage("검증")], sleep=AsyncMock())
        self.assertEqual((len(main.calls), len(fallback.calls)), (3, 3))
        main, fallback = Model([StatusError(401)]), Model([AIMessage("unused")])
        with self.assertRaises(StatusError):
            await M.invoke(main, fallback, [HumanMessage("검증")], sleep=AsyncMock())
        self.assertEqual((len(main.calls), len(fallback.calls)), (1, 0))

    async def test_context_failure_never_calls_model(self):
        model = Model([AIMessage("unused")])
        with patch.object(C, "INPUT_BUDGET", 100), self.assertRaises(C.ContextBudgetExceeded):
            await M.invoke(model, None, [HumanMessage("큰 질문" * 1000)])
        self.assertEqual(model.calls, [])

    async def test_tool_error_is_counted_even_when_returned_as_false(self):
        with patch.object(G, "_call_tool", AsyncMock(return_value=(False, "[UPSTREAM_TIMEOUT]"))):
            async with O.execution("t", "th", "cmd", "START") as run:
                ok, _ = await G.call_tool({}, "analyze_impact", {}, quiet=True)
                self.assertFalse(ok)
                self.assertEqual((run.tool_calls, run.tool_errors), (1, 1))

    async def test_same_tool_fourth_call_never_reaches_mcp(self):
        state = {"intent": "IMPACT", "tool_counts": {"analyze_impact": 3}, "messages": [
            AIMessage("", tool_calls=[{"id": "c", "name": "analyze_impact", "args": {}}])]}
        with patch.object(G, "call_tool", AsyncMock()) as invoke:
            result = await G.tools(state)
            invoke.assert_not_awaited()
        self.assertIn("3번", result["messages"][0].content)

    async def test_execution_ids_are_isolated_and_resume_is_linked(self):
        async with O.execution("t", "th", "start", "START") as first:
            self.assertEqual(O.enrich({"type": "TOOL_START"})["traceId"], first.trace_id)
            self.assertEqual(O.graph_config("th", 600)["metadata"]["thread_id"], "th")
        async with O.execution("t", "th", "resume", "DECISION", first.trace_id) as second:
            self.assertNotEqual(first.trace_id, second.trace_id)
            self.assertEqual(second.previous_trace_id, first.trace_id)
        self.assertIsNone(O.current())

    async def test_parallel_executions_do_not_share_counters(self):
        async def child(task):
            async with O.execution(task, task, task, "START") as run:
                await asyncio.sleep(0)
                with O.tool_span("test", {}) as result:
                    result["ok"] = True
                return run.task_id, run.tool_calls
        self.assertEqual(await asyncio.gather(child("a"), child("b")), [("a", 1), ("b", 1)])

    async def test_trace_link_failure_does_not_stop_business_code(self):
        root = Mock()
        root.__enter__ = Mock(return_value=Mock(get_url=Mock(side_effect=RuntimeError("offline"))))
        root.__exit__ = Mock(return_value=False)
        with patch.object(O, "client", return_value=Mock()), patch("langsmith.trace", return_value=root):
            async with O.execution("t", "th", "cmd", "START") as run:
                self.assertIsNone(run.trace_url)
                with O.tool_span("local-tool", {}) as result:
                    result["ok"] = True
                self.assertEqual(run.tool_calls, 1)

    async def test_trace_flush_failure_is_not_a_shutdown_failure(self):
        with patch.object(O, "_client", Mock(flush=Mock(side_effect=RuntimeError("offline")))):
            await O.shutdown()


class Rules(unittest.TestCase):
    def test_prompt_versions_and_role_visibility(self):
        self.assertEqual(len(O.prompt_version()), 12)
        data = {"unit_price": 11, "amount": 22, "added_cost": 33, "saved_amount": 44}
        self.assertEqual(set(G.mask(data, "PLANNER").values()), {None})
        self.assertIsNone(G.mask(data, "BUYER")["unit_price"])
        self.assertEqual(G.mask(data, "APPROVER"), data)

    def test_trace_scrubber_covers_serialized_free_text_and_nested_credentials(self):
        raw = {"messages": ["unit_price: 10000 secret"], "args": {"unit_price": 123, "api_key": "secret"},
               "error": "Authorization Bearer secret", "input_tokens": 25}
        text = json.dumps(O.scrub(raw))
        self.assertNotIn("secret", text)
        self.assertNotIn("123", text)
        self.assertEqual(O.scrub(raw)["input_tokens"], 25)

    def test_path_specific_turn_limits_and_fake_mode(self):
        with self.assertRaises(G.LimitExceeded):
            G.next_turn({"turn_count": 8, "intent": "IMPACT"})
        with self.assertRaises(G.LimitExceeded):
            G.next_turn({"turn_count": G.MAX_ADJUST_TURNS, "intent": "ADJUST"})
        with patch.dict(os.environ, {"AGENT_MODEL_MODE": "fake", "OPENAI_API_KEY": "must-not-be-used"}):
            self.assertEqual(M.make_models(), (None, None))


if __name__ == "__main__":
    unittest.main()
