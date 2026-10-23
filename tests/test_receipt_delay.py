"""Chat delay resolution and permission metadata; no business mutations or model calls."""
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import receipt_delay as D
import graph as G
import whatif as W

QUESTION = "11/13 와이파이칩 입고일이 일주일 늦어졌어. 문제있나?"
ROWS = [dict(po_id="PO-2609-002", item_id="WIFI", item_name="Wi-Fi 칩", qty=1000, due_date="2026-11-13", status="OPEN"),
        dict(po_id="PO-2609-005", item_id="WIFI", item_name="Wi-Fi 칩", qty=500, due_date="2026-11-20", status="OPEN"),
        dict(po_id="PO-2609-006", item_id="WIFI", item_name="Wi-Fi 칩", qty=400, due_date="2026-11-20", status="OPEN")]
MULTI_ROWS = ROWS + [dict(po_id="PO-2610-106", item_id="RELAY", item_name="릴레이", qty=1200, due_date="2026-11-20", status="OPEN")]
MULTI_QUESTION = "릴레이 발주의 입고일이 11월 20일에서 11월 27일로 늦어지고, 와이파이 칩의 입고일이 11/13에서 11/20로 늦어진대. 생산에 어떤 문제가 생겨?"


def multi_classification(**extra):
    return W.Classification(intent="IMPACT", receipt_delays=[
        D.DelayMention(item_name="릴레이", original_due_date="2026-11-20", new_due_date="2026-11-27", delay_days=7),
        D.DelayMention(item_name="와이파이 칩", original_due_date="2026-11-13", new_due_date="2026-11-20", delay_days=7),
    ], **extra)


class ReceiptDelayTests(unittest.IsolatedAsyncioTestCase):
    async def classify_state(self, question, parsed):
        with patch.object(G, "llm", object()), patch.object(G.M, "invoke", AsyncMock(return_value=parsed)), \
             patch.object(G, "load_plans", AsyncMock(return_value=[])), \
             patch.object(G, "previous_turn", AsyncMock(return_value=None)), \
             patch.object(G, "call_tool", AsyncMock(return_value=(True, {"purchase_orders": MULTI_ROWS}))):
            return await G.classify_intent({"question": question, "role": "BUYER"})

    async def test_explicit_multi_receipt_dates_accept_matching_model_day_difference(self):
        state = await self.classify_state(MULTI_QUESTION, multi_classification())
        self.assertEqual(state["intent"], "IMPACT")
        self.assertEqual(state["whatif_ops"], [
            {"op": "request_due_date_change", "po_id": "PO-2610-106", "new_due_date": "2026-11-27"},
            {"op": "request_due_date_change", "po_id": "PO-2609-002", "new_due_date": "2026-11-20"},
        ])
        self.assertTrue(state["receipt_delay"]["reported"])
        self.assertEqual(state["turn_count"], 1)

    async def test_main_po_guard_rejects_wrong_item_with_receipt_diagnostics(self):
        parsed = W.Classification(intent="IMPACT", receipt_delays=[
            D.DelayMention(item_name="릴레이", delay_days=7)])
        state = await self.classify_state("MCU 입고가 1주 늦어질 수 있대. 문제 있어?", parsed)
        response = G.respond(state)
        self.assertEqual(response["result"]["intent"], "CLARIFY")
        self.assertEqual(response["result"]["clarification"]["kind"], "INTERPRETATION_ERROR")
        self.assertIn("품목(릴레이)이 없음", response["answer"])
        self.assertIn("PO-2610-106", response["answer"])
        self.assertNotIn("unclear", response["answer"])
        self.assertEqual(len(state["classification_attempts"]), 2)

    async def test_po_guard_accepts_wifi_alias_and_iso_original_date(self):
        parsed = W.Classification(intent="IMPACT", receipt_delays=[
            D.DelayMention(item_name="와이파이 칩", original_due_date="2026-11-13", delay_days=7)])
        state = await self.classify_state("와이파이 칩 2026-11-13 입고가 일주일 늦어졌어. 문제 있어?", parsed)
        self.assertEqual(state["intent"], "IMPACT")
        self.assertEqual(state["whatif_ops"], [
            {"op": "request_due_date_change", "po_id": "PO-2609-002", "new_due_date": "2026-11-20"}])

    async def test_conflicting_clarification_retries_with_receipt_context_and_retains_attempt(self):
        diagnostics = {}
        invoke = AsyncMock(side_effect=[multi_classification(unclear="생산일을 알려 주세요."), multi_classification()])
        with patch.object(G, "llm", object()), patch.object(G.M, "invoke", invoke):
            c, errors, turns = await G.classify_question(MULTI_QUESTION, [], receipts=MULTI_ROWS, diagnostics=diagnostics)
        self.assertEqual((errors, turns, c.unclear), ([], 2, None))
        self.assertIn("OPEN 발주 표", invoke.call_args.args[2][-1].content)
        self.assertEqual(diagnostics["attempts"][0]["issues"][0]["code"], "CONFLICTING_CLARIFICATION")
        self.assertIsNone(diagnostics["clarification"])

    async def test_unresolved_unclear_is_not_silently_discarded(self):
        reason = "추가로 언급한 MCU 발주의 새 입고일이 필요합니다."
        state = await self.classify_state(MULTI_QUESTION, multi_classification(unclear=reason))
        response = G.respond(state)
        self.assertEqual(response["result"]["intent"], "CLARIFY")
        self.assertEqual(response["result"]["clarification"]["kind"], "INTERPRETATION_ERROR")
        self.assertIn(reason, response["answer"])
        self.assertIn("PO-2610-106", response["answer"])
        self.assertIn("영향 분석을 진행하지 않았습니다", response["answer"])
        self.assertNotIn("유효한 whatif", response["answer"])
        self.assertNotIn("허브를", response["answer"])
        self.assertEqual(len(state["classification_attempts"]), 2)

    async def test_conflicting_date_and_duration_names_exact_receipt_and_values(self):
        parsed = multi_classification()
        parsed.receipt_delays[1].delay_days = 8
        state = await self.classify_state(MULTI_QUESTION, parsed)
        response = G.respond(state)
        detail = response["result"]["clarification"]
        self.assertEqual(detail["issues"][0]["code"], "CONFLICTING_RECEIPT_DATES")
        self.assertIn("PO-2609-002", response["answer"])
        self.assertIn("8일", response["answer"])
        self.assertIn("2026-11-21", response["answer"])
        self.assertIn("2026-11-20", response["answer"])
        self.assertEqual(response["result"]["whatif"], [])
        self.assertIsNone(response["result"]["impact"])
        self.assertEqual(detail["extracted"]["receipt_delays"][1]["delay_days"], 8)

    async def test_explicit_date_does_not_bypass_unstated_date_guard(self):
        parsed = multi_classification()
        parsed.receipt_delays[1].new_due_date = "2026-11-22"
        parsed.receipt_delays[1].delay_days = 9
        state = await self.classify_state(MULTI_QUESTION, parsed)
        response = G.respond(state)
        self.assertEqual(response["result"]["intent"], "CLARIFY")
        self.assertIn("PO-2609-002", response["answer"])
        self.assertIn("2026-11-22", response["answer"])
        self.assertNotIn("unclear에", response["answer"])

    async def test_partial_multi_receipt_ambiguity_explains_only_unresolved_item(self):
        parsed = multi_classification()
        parsed.receipt_delays[1] = D.DelayMention(item_name="와이파이 칩", original_due_date="2026-11-20", new_due_date="2026-11-27")
        state = await self.classify_state(MULTI_QUESTION.replace("11/13에서 11/20", "11/20에서 11/27"), parsed)
        response = G.respond(state)
        detail = response["result"]["clarification"]
        self.assertEqual(detail["kind"], "NEEDS_INPUT")
        self.assertIn("와이파이 칩", detail["issues"][0]["target"])
        for po in ["PO-2609-005", "PO-2609-006"]:
            self.assertIn(po, response["answer"])
        self.assertEqual(response["result"]["whatif"], [])
        self.assertEqual(G.route_intent(state), "respond")

    def test_relative_week_uses_actual_receipt_date(self):
        ops, errors = D.resolve(D.fake_mentions(QUESTION, ROWS, W.BASE_DATE), ROWS, W.BASE_DATE)
        self.assertEqual(errors, [])
        self.assertEqual(ops, [dict(op="request_due_date_change", po_id="PO-2609-002", new_due_date="2026-11-20")])
        snapshot = D.snapshot(QUESTION, ops, ROWS)
        self.assertTrue(snapshot["reported"])
        self.assertEqual(snapshot["changes"][0]["before"]["due_date"], "2026-11-13")

    def test_ambiguous_missing_and_closed_po_need_clarification(self):
        for mentions, rows in [([D.DelayMention(item_name="와이파이칩", delay_days=7)], ROWS),
                               ([D.DelayMention(po_id="PO-9999-999", delay_days=7)], ROWS),
                               ([D.DelayMention(po_id=ROWS[0]["po_id"], delay_days=7)], [{**ROWS[0], "status":"CLOSED"}])]:
            ops, errors = D.resolve(mentions, rows, W.BASE_DATE)
            self.assertEqual(ops, []); self.assertTrue(errors)
        _, errors = D.resolve([D.DelayMention(item_name="Wi-Fi 칩", original_due_date="2026-11-20", delay_days=7)], ROWS, W.BASE_DATE)
        self.assertIn("PO-2609-005", errors[0]); self.assertIn("PO-2609-006", errors[0])

    def test_hypotheses_negation_and_estimates_are_not_facts(self):
        for q in ["입고가 일주일 늦어지면 문제 있나?", "입고가 늦어질 수 있다고 합니다", "약 1주 지연 통보",
                  "입고 지연은 확정이 아니야", "만약 지연이 확정되면?", "늦어졌다는 건 아니야"]:
            self.assertFalse(D.reported(q), q)
        self.assertTrue(D.reported("PO-2610-106 입고는 11월 27일로 늦어진대. 문제 있어?"))

    def test_invalid_conflicting_and_duplicate_conditions_are_rejected(self):
        for values in [dict(delay_days=0), dict(delay_days=731), dict(new_due_date="2026-11-12"),
                       dict(delay_days=7, new_due_date="2026-11-22")]:
            self.assertTrue(D.resolve([D.DelayMention(po_id=ROWS[0]["po_id"], **values)], ROWS, W.BASE_DATE)[1])
        m = D.DelayMention(po_id=ROWS[0]["po_id"], delay_days=7)
        self.assertTrue(D.resolve([m,m], ROWS, W.BASE_DATE)[1])
        row = {**ROWS[0], "due_date":"2026-12-28"}
        self.assertEqual(D.resolve([m], [row], W.BASE_DATE)[0][0]["new_due_date"], "2027-01-04")

    async def test_graph_classifies_and_exposes_handoff_without_write_tools(self):
        call = AsyncMock(return_value=(True, {"purchase_orders":ROWS}))
        with patch.object(G, "llm", None), patch.object(G, "load_plans", AsyncMock(return_value=[])), patch.object(G, "call_tool", call):
            state = await G.classify_intent({"question":QUESTION, "role":"BUYER"})
        self.assertEqual(state["intent"], "IMPACT"); self.assertTrue(state["receipt_delay"]["reported"])
        call.assert_awaited_once()
        self.assertEqual(call.call_args.args[1], "list_receipts")
        self.assertEqual(G.respond(state)["result"]["receipt_delay"], state["receipt_delay"])

    async def test_ambiguous_graph_answer_names_candidates(self):
        with patch.object(G, "llm", None), patch.object(G, "load_plans", AsyncMock(return_value=[])), patch.object(G, "call_tool", AsyncMock(return_value=(True,{"purchase_orders":ROWS}))):
            state = await G.classify_intent({"question":"11/20 와이파이칩 입고가 일주일 늦어졌어", "role":"BUYER"})
        self.assertEqual(state["intent"], "CLARIFY")
        self.assertIn("PO-2609-005", state["clarify"])

    async def test_model_structured_duration_is_resolved_by_code(self):
        parsed = W.Classification(intent="IMPACT", receipt_delays=[D.DelayMention(item_name="와이파이칩", original_due_date="2026-11-13", delay_days=7)])
        with patch.object(G, "llm", object()), patch.object(G.M, "invoke", AsyncMock(return_value=parsed)):
            c, errors, _ = await G.classify_question(QUESTION, [], receipts=ROWS)
        self.assertEqual(errors, [])
        self.assertEqual(c.whatif[0].new_due_date, "2026-11-20")

    async def test_invalid_calendar_date_clarifies_instead_of_failing_task(self):
        with patch.object(G, "llm", None):
            _, errors, _ = await G.classify_question("11/31 와이파이칩 입고가 일주일 늦어졌어", [], receipts=ROWS)
        self.assertTrue(errors)

    def test_calendar_days_are_not_relative_delay_durations(self):
        self.assertEqual(D.stated_delay_days("11월 13일 입고가 늦어졌어"), set())
        self.assertEqual(D.stated_delay_days("11/13 입고가 2주 늦어졌어"), {14})
        self.assertEqual(D.stated_delay_days("2026-11-13 입고가 10일 늦어졌어"), {10})
        self.assertEqual(D.stated_delay_days(QUESTION), {7})

    async def test_calculated_receipt_date_does_not_authorize_an_invented_plan_date(self):
        plans = [{"plan_id":"PP-05", "product_id":"FG-HUB", "product_name":"허브", "plan_date":"2026-12-14", "qty":400}]
        parsed = W.Classification(intent="IMPACT", whatif=[W.WhatifOp(op="reschedule_plan", plan_id="PP-05", new_date="2026-11-20")],
                                  receipt_delays=[D.DelayMention(item_name="와이파이칩", original_due_date="2026-11-13", delay_days=7)])
        with patch.object(G, "llm", object()), patch.object(G.M, "invoke", AsyncMock(return_value=parsed)):
            _, errors, _ = await G.classify_question(QUESTION, plans, receipts=ROWS)
        self.assertTrue(any("질문에 없는 날짜" in e and "reschedule_plan" in e for e in errors), errors)

    async def test_model_cannot_invent_a_relative_duration_or_explicit_receipt_date(self):
        for values in [dict(delay_days=7), dict(new_due_date="2026-11-20")]:
            parsed = W.Classification(intent="IMPACT", receipt_delays=[D.DelayMention(po_id="PO-2609-002", **values)])
            with patch.object(G, "llm", object()), patch.object(G.M, "invoke", AsyncMock(return_value=parsed)):
                _, errors, _ = await G.classify_question("PO-2609-002 입고가 늦어졌어. 문제 있어?", [], receipts=ROWS)
            self.assertTrue(errors and "질문에 없는" in errors[0], errors)


if __name__ == "__main__":
    unittest.main()
