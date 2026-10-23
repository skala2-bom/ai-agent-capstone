"""Do not publish a model conclusion that contradicts the calculator."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import AIMessage
import graph as G


class ImpactSummaryTests(unittest.TestCase):
    def test_real_model_contradiction_is_replaced_by_calculated_conclusion(self):
        impact = {"shortages": [{"item_id": "MCU", "plan_id": "PP-04", "plan_date": "2026-11-30",
                                  "projected_stock": -500, "orderable": False}], "warnings": [], "excesses": []}
        result = G.respond({"intent": "IMPACT", "impact": impact, "plans": [],
                            "messages": [AIMessage(content="MCU가 500개 부족합니다. 문제가 없습니다.")]})
        self.assertNotIn("문제가 없습니다", result["answer"])
        self.assertIn("결품 1건", result["answer"])
        self.assertIn("새 발주로는 늦어", result["answer"])
        self.assertIn("500개", result["answer"])
        self.assertEqual(result["result"]["impact"], impact)

    def test_all_orderable_and_mixed_shortages_have_different_guidance(self):
        self.assertIn("제때 입고 가능한", G.impact_lead({"shortages": [{"orderable": True}]}))
        self.assertIn("일부 결품", G.impact_lead({"shortages": [{"orderable": True}, {"orderable": False}]}))
        self.assertNotIn("없습니다", G.impact_lead({"warnings": [{}]}))
        self.assertIn("없습니다", G.impact_lead({"shortages": [], "warnings": [], "excesses": []}))

    def test_other_plan_issue_does_not_imply_new_causal_shortage(self):
        note = G.chain_note({"shortages": [{"plan_id": "PP-04", "plan_date": "2026-11-30"}]},
                            [{"op": "reschedule_plan", "plan_id": "PP-03"}], [])
        self.assertIn("다른 생산의 결품", note)
        self.assertNotIn("연쇄", note)


if __name__ == "__main__":
    unittest.main()
