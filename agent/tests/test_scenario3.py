"""팀 시나리오 ③ (decisions 6.56): 저장된 입고 지연 위에서 구매가 해결안을 묻는다. 네트워크·DB·모델 없이 돈다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G
import whatif as W

Q3 = ("현재 확정된 생산계획에서 1주 MCU 지연으로 남는 부족을 기존 발주 당김이나 분할로 해결할 수 있나요? "
      "세미온 계약 근거와 다른 제품에 미치는 영향도 보여 줘.")
POS = [{"po_id": "PO-2609-008", "item_id": "MCU", "item_name": "MCU", "due_date": "2026-12-04",
        "recorded_delay": {"from": "2026-11-27", "to": "2026-12-04", "days": 7}},
       {"po_id": "PO-2609-001", "item_id": "MCU", "item_name": "MCU", "due_date": "2026-12-12"},
       {"po_id": "PO-2610-109", "item_id": "LED", "item_name": "LED", "due_date": "2026-11-20"}]


class Scenario3Test(unittest.TestCase):
    def test_question_is_a_solution_request(self):
        self.assertTrue(W.explicit_adjust_request(Q3))

    def test_recorded_delay_is_not_added_again(self):
        self.assertEqual(W.recorded_match({"item_name": "MCU", "delay_days": 7}, POS, Q3), "PO-2609-008")
        # 실측: 모델이 다른 MCU 발주에 지연 0일을 넣었다 → 질문의 "1주"가 저장된 지연이다
        self.assertEqual(W.recorded_match({"po_id": "PO-2609-001", "delay_days": 0}, POS, Q3), "PO-2609-008")
        self.assertIsNone(W.recorded_match({"item_name": "MCU", "delay_days": 7}, POS, "MCU가 1주 더 늦어지면?"))
        self.assertIsNone(W.recorded_match({"item_name": "MCU", "delay_days": 14}, POS, "MCU가 2주 늦어지면?"))

    def test_unsupported_delay_extraction_is_dropped(self):
        # 실측: 해결 방법만 물었는데 질문에 없는 부품 발주에 날짜를 만들었다
        self.assertTrue(W.unsupported_delay({"po_id": "PO-2610-109", "new_due_date": "2027-01-08"}, POS, Q3))
        self.assertFalse(W.unsupported_delay({"po_id": "PO-2609-008", "delay_days": 7}, POS, "PO-2609-008이 1주 늦어지면?"))
        self.assertFalse(W.unsupported_delay({"item_name": "MCU", "delay_days": 7}, POS, "MCU가 1주 늦어지면?"))

    def test_current_state_solution_request_needs_no_po_number(self):
        q = "지금 확정된 계획 기준으로 MCU 부족을 기존 발주로 메울 방법 있어? 계약 근거도 같이."
        self.assertTrue(W.CURRENT_STATE.search(q) and W.explicit_adjust_request(q))

    def test_card_explanation_comes_from_verify_and_contract(self):
        state = {"plans": [{"plan_id": "PP-04", "product_name": "스마트 플러그", "plan_date": "2026-11-30"}],
                 "selected_option": {"item_id": "MCU", "side_effects": [], "evidence": [
                     {"title": "세미온반도체 공급계약", "clause_no": "2.1", "excerpt": "기존 발주는 협력사 확약을 받아 납기를 당기거나 나누어 받을 수 있다."}]},
                 "verify": {"warnings_after": [{"item_id": "MCU", "item_name": "MCU", "plan_id": "PP-04", "plan_date": "2026-11-30",
                                                "projected_stock": 0, "threshold": 300}]}}
        text = G.proposal_explain(state)
        for part in ("대상 결품이 해소됩니다", "새 결품은 없습니다", "MCU 예상 재고가 0개로 안전재고 300개보다 낮습니다",
                     "세미온반도체 공급계약 2.1조에 따라 실제 당김·분할에는 협력사 확약이 필요합니다", "팀장 승인·실행 전까지는 발주가 변경되지 않습니다"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
