"""해결 요청을 분석만으로 끝내지 않기 (decisions 6.48): 요청 표현 넓히기, 풀 문제가 없을 때의 안내."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G
import whatif as W

EMPTY = {"shortages": [], "warnings": [], "excesses": []}


class AdjustRequestTest(unittest.TestCase):
    def test_request_words(self):
        for q in ["그럼 해결 방법 만들어 줘", "해결방안 줘", "해결책 알려줘", "해결안 만들어 줘",
                  "어떻게 해결해?", "대안 있어?", "대안 좀 알려줘", "발주 조정해 줘"]:
            self.assertTrue(W.explicit_adjust_request(q), q)
        for q in ["해결안 검수 규정이 뭐야?", "허브를 11/25로 당기면 문제 있어?"]:   # 문서 질문·영향 질문은 아니다
            self.assertFalse(W.explicit_adjust_request(q), q)

    def answer(self, **state):
        return G._adjust_answer({"plans": [], "current_impact": EMPTY, "scope": [], "proposals": [], **state})

    def test_no_problem_under_the_assumption_says_why(self):
        text = self.answer(whatif_ops=[{"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}],
                           inherited="앞 질문의 가정을 이어서 계산했습니다: 허브")
        self.assertTrue(text.startswith("이 가정으로 다시 계산하니 기존 발주를 조정해야 할 결품·과잉이 없어 해결안을 만들지 않았습니다."))
        self.assertIn("앞 작업에서 실행한 변경으로 이미 풀렸을 수 있습니다.", text)

    def test_no_assumption_and_no_problem_asks_what_to_solve(self):
        text = self.answer(whatif_ops=[])
        self.assertIn("어떤 변경에 대한 해결 방법인지 함께 알려 주세요.", text)

    def test_normal_result_keeps_its_first_line(self):
        self.assertTrue(self.answer(whatif_ops=[], scope=["INV-001|MCU"]).startswith("현재 분석 결과입니다."))


if __name__ == "__main__":
    unittest.main()
