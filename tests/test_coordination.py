"""Authority and immutable planning context at the agent boundary; no model calls."""
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import graph as G
import whatif as W

HUB = {"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}
PLANS = [{"plan_id": "PP-05", "product_id": "FG-HUB", "product_name": "허브", "plan_date": "2026-12-14", "qty": 400}]


class CoordinationTests(unittest.IsolatedAsyncioTestCase):
    async def test_solver_uses_server_consent_instead_of_reextracting_chat(self):
        context = {"direction": "PLAN_TO_BUY", "mode": "SOLVE", "planOps": [HUB]}
        with patch.object(G, "load_plans", AsyncMock(return_value=PLANS)), patch.object(G, "classify_question", AsyncMock()) as model:
            out = await G.classify_intent({"role": "BUYER", "question": "이거 해결해줘", "coordination": context})
        self.assertEqual(out["intent"], "ADJUST")
        self.assertEqual(out["whatif_ops"], [HUB])
        model.assert_not_called()

    async def test_planner_cannot_start_purchase_solver(self):
        with patch.object(G, "load_plans", AsyncMock(return_value=PLANS)):
            out = await G.classify_intent({"role": "PLANNER", "question": "해결", "coordination": {"direction": "PLAN_TO_BUY", "mode": "SOLVE", "planOps": [HUB]}})
        self.assertEqual(out["intent"], "CLARIFY")

    def test_changed_consent_and_purchase_ops_are_rejected(self):
        self.assertTrue(W.coordination_errors({"direction": "PLAN_TO_BUY", "planOps": [HUB]}, [{**HUB, "new_date": "2026-11-26"}]))
        self.assertTrue(W.coordination_errors({"direction": "BUY_TO_PLAN"}, [{"op": "cancel_po", "po_id": "PO-2610-106"}]))
        self.assertEqual(W.coordination_errors({"direction": "BUY_TO_PLAN"}, [HUB]), [])

    async def test_followup_does_not_create_a_second_solver(self):
        classification = W.Classification(intent="ADJUST", whatif=[])
        with patch.object(G, "load_plans", AsyncMock(return_value=PLANS)), patch.object(G, "previous_turn", AsyncMock(return_value=None)), patch.object(G, "classify_question", AsyncMock(return_value=(classification, [], 1))):
            out = await G.classify_intent({"role": "BUYER", "question": "다시 해결해줘", "coordination": {"direction": "PLAN_TO_BUY", "mode": "CHAT", "planOps": [HUB]}})
        self.assertEqual(out["intent"], "IMPACT")
        self.assertEqual(out["whatif_ops"], [HUB])

    async def test_plan_request_analyzes_changes_without_purchase_authority(self):
        classification = W.Classification(intent="ADJUST", whatif=[W.WhatifOp(**HUB)])
        with patch.object(G, "load_plans", AsyncMock(return_value=PLANS)), patch.object(G, "previous_turn", AsyncMock(return_value=None)), patch.object(G, "classify_question", AsyncMock(return_value=(classification, [], 1))):
            out = await G.classify_intent({"role": "PLANNER", "question": "생산일 바꾸면", "coordination": {"direction": "BUY_TO_PLAN", "mode": "PLAN_REVIEW"}})
        self.assertEqual(out["intent"], "IMPACT")
        self.assertEqual(out["whatif_ops"], [HUB])


if __name__ == "__main__":
    unittest.main()
