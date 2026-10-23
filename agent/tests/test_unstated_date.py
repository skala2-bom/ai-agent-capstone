"""질문에 없는 날짜를 지어내지 않는다 (평가셋 v2 Q17). 네트워크·DB·모델 없이 돈다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G
import whatif as W

PLANS = [{"plan_id": "PP-05", "product_id": "FG-HUB", "product_name": "IoT 허브", "plan_date": "2026-12-14", "qty": 400}]
HUB = {"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}


class UnstatedDateTest(unittest.IsolatedAsyncioTestCase):
    def test_date_must_be_written_in_the_question(self):
        self.assertTrue(W.unstated_date_errors("허브 생산 좀 당겨 보면 어때?", [HUB]))
        self.assertEqual(W.unstated_date_errors("허브를 11/25로 당기면?", [HUB]), [])
        self.assertEqual(W.unstated_date_errors("허브를 11월 25일로 당기면?", [HUB]), [])
        self.assertEqual(W.unstated_date_errors("허브를 2026-11-25로 당기면?", [HUB]), [])
        self.assertEqual(W.unstated_date_errors("그럼 해결 방법 만들어 줘", [HUB], ["2026-11-25"]), [])

    async def test_model_invented_date_becomes_clarify(self):
        saved = (G.llm, G.M.invoke)
        calls = []

        async def invoke(*args, **kwargs):   # 실제 Q17처럼 매번 11/25를 채우는 모델
            calls.append(1)
            return W.Classification(intent="IMPACT", whatif=[HUB])
        G.llm, G.M.invoke = object(), invoke
        try:
            c, errors, turns = await G.classify_question("허브 생산 좀 당겨 보면 어때?", PLANS)
        finally:
            G.llm, G.M.invoke = saved
        self.assertEqual(len(calls), 2)   # 오류를 알려 주고 한 번 더 기회를 준 뒤
        self.assertTrue(errors and "질문에 없는 날짜" in errors[0])   # classify_intent가 CLARIFY로 되묻는다


if __name__ == "__main__":
    unittest.main()
