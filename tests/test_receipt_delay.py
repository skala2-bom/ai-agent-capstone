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


class ReceiptDelayTests(unittest.IsolatedAsyncioTestCase):
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
