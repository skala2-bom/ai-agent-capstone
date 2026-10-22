"""팀장 조건 재제안 (decisions 6.47): 조건을 계산 서비스에 넘기고, 고르는 건 코드가, 못 풀면 이유를 돌려준다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G

KEY = "INV-001|MCU"
SHORT = {"item_id": "MCU", "plan_id": "PP-05", "plan_date": "2026-11-25", "projected_stock": -100, "orderable": False}
CONSTRAINT = {"new_due_date": "2026-11-22", "split_qty": 150}


def option(po, resolves=True, **extra):
    return {"option_id": "OPT-1", "item_id": "MCU", "target_rule": "INV-001", "feasible": True, "resolves": resolves,
            "side_effects": [], "remaining_issues": [] if resolves else [{"item_id": "MCU", "shortage_qty": 50}],
            "recommended": resolves, "tool": "split_po",
            "args": {"po_id": po, "split_qty": 150, "new_due_date": "2026-11-22"}, **extra}


class ReplanConstraintTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = G.call_tool
        self.calls = []

    def tearDown(self):
        G.call_tool = self.saved

    def answer(self, response):
        async def fake(state, name, args, **kw):
            self.calls.append((name, args))
            return True, response
        G.call_tool = fake

    def state(self, constraint=CONSTRAINT):
        return {"current_impact": {"shortages": [SHORT], "excesses": []}, "scope": [KEY], "current_ops": [],
                "replan_target": KEY, "replan_constraint": constraint, "proposals": [], "tried": [], "blocked": {}}

    async def test_constraint_is_sent_and_first_matching_option_is_picked_by_code(self):
        self.answer({"options": [option("PO-2609-001")], "constraint_notes": []})
        state = self.state()
        state.update(await G.compare(state))
        self.assertEqual(self.calls[0][1]["constraint"], CONSTRAINT)
        self.assertIsNone(state["evidence_error"])
        picked = await G.agent_adjust(state)
        self.assertEqual(picked["selected_option"]["args"]["po_id"], "PO-2609-001")
        self.assertEqual(picked["pick_reason"], "팀장 요청 조건(2026-11-22 · 150개)을 반영했습니다.")

    async def test_impossible_constraint_returns_reason_instead_of_a_card(self):
        note = "PO-2610-108는 리드타임상 2026-12-15 이후에만 받을 수 있어 요청일 2026-11-22는 불가능합니다."
        self.answer({"options": [option("PO-2609-008", resolves=False)], "constraint_notes": [note]})
        state = self.state()
        state.update(await G.compare(state))
        self.assertIn("요청 조건(2026-11-22 · 150개)으로는 해결안을 만들 수 없습니다.", state["evidence_error"])
        self.assertIn(note, state["evidence_error"])
        self.assertIn("남는 문제", state["evidence_error"])
        self.assertIsNone((await G.agent_adjust(state))["selected_option"])
        self.assertEqual(G.outcome_code(state), "NO_ALTERNATIVE")   # 오류(REVIEW_FAILED)가 아니다

    async def test_without_constraint_nothing_extra_is_sent(self):
        self.answer({"options": [option("PO-2609-001")]})
        state = self.state(constraint=None)
        state.update(await G.compare(state))
        self.assertNotIn("constraint", self.calls[0][1])


if __name__ == "__main__":
    unittest.main()
