"""이어 묻기 (decisions 6.44): 앞 작업의 가정을 언제 이어받고 언제 버리는가. 네트워크·DB·모델 없이 돈다."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G
import whatif as W

PLANS = [
    {"plan_id": "PP-03", "product_id": "FG-SWITCH", "product_name": "스마트 벽스위치", "plan_date": "2026-11-23", "qty": 600},
    {"plan_id": "PP-05", "product_id": "FG-HUB", "product_name": "IoT 허브", "plan_date": "2026-12-14", "qty": 400},
]
HUB = {"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}


class SavedStates:
    """checkpointer 대역: thread별로 저장된 State 값을 돌려준다."""
    def __init__(self, values):
        self.values = values

    async def aget_state(self, cfg):
        return types.SimpleNamespace(values=self.values.get(cfg["configurable"]["thread_id"], {}))


class FollowUpTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved = (G.app_graph, G.load_plans, G.llm)
        G.app_graph = SavedStates({"parent": {"question": "허브를 11/25로 당기면 문제 있어?", "whatif_ops": [HUB]}})
        G.llm = None   # 규칙 분류 (가짜 모델)

        async def plans(state):
            return PLANS
        G.load_plans = plans

    def tearDown(self):
        G.app_graph, G.load_plans, G.llm = self.saved

    async def ask(self, question, parent="parent", role="BUYER"):
        return await G.classify_intent({"question": question, "role": role, "parent_thread_id": parent})

    async def test_then_solve_inherits_previous_whatif_and_says_so(self):
        out = await self.ask("그럼 해결 방법 만들어 줘")
        self.assertEqual(out["intent"], "ADJUST")
        self.assertEqual(out["whatif_ops"], [HUB])
        self.assertEqual(out["inherited"], "앞 질문의 가정을 이어서 계산했습니다: IoT 허브 생산(PP-05, 2026-12-14) → 2026-11-25")

    async def test_add_on_change_is_merged(self):
        out = await self.ask("거기에 11/23 스위치도 900대로 늘리면 문제 있어?")
        self.assertEqual(out["intent"], "IMPACT")
        self.assertEqual(out["whatif_ops"], [HUB, {"op": "change_plan_qty", "plan_id": "PP-03", "new_qty": 900}])

    async def test_new_topic_does_not_inherit(self):
        out = await self.ask("지금 생산 계획대로 가면 부품 문제 있어?")
        self.assertEqual(out["whatif_ops"], [])
        self.assertIsNone(out["inherited"])

    async def test_without_parent_nothing_is_inherited(self):
        out = await self.ask("그럼 해결 방법 만들어 줘", parent=None)
        self.assertEqual((out["intent"], out["whatif_ops"], out["inherited"]), ("ADJUST", [], None))

    async def test_planner_follow_up_keeps_role_limit(self):
        out = await self.ask("그럼 해결 방법 만들어 줘", role="PLANNER")
        self.assertEqual((out["intent"], out["whatif_ops"]), ("IMPACT", [HUB]))
        self.assertIn("구매 담당자·팀장만", out["notice"])

    async def test_skips_back_over_a_question_without_assumptions(self):
        # 6.48: 허브 질문 → 규정 질문(가정 없음) → "그럼 해결 방법" 에서도 허브 가정을 잃지 않는다
        G.app_graph = SavedStates({
            "hub": {"question": "허브를 11/25로 당기면 문제 있어?", "intent": "IMPACT", "whatif_ops": [HUB]},
            "qa": {"question": "ECN-2026-014 규정이 뭐야?", "intent": "QA", "whatif_ops": [], "parent_thread_id": "hub"}})
        out = await self.ask("그럼 해결 방법 만들어 줘", parent="qa")
        self.assertEqual((out["intent"], out["whatif_ops"]), ("ADJUST", [HUB]))

    async def test_does_not_skip_back_over_a_current_state_question(self):
        # "지금 계획대로 가면?"은 가정이 없는 것이 곧 '지금 상태'라는 뜻이다. 그 앞 가정을 되살리지 않는다
        G.app_graph = SavedStates({
            "hub": {"question": "허브를 11/25로 당기면 문제 있어?", "intent": "IMPACT", "whatif_ops": [HUB]},
            "base": {"question": "지금 계획대로 가면?", "intent": "IMPACT", "whatif_ops": [], "parent_thread_id": "hub"}})
        out = await self.ask("그럼 해결 방법 만들어 줘", parent="base")
        self.assertEqual(out["whatif_ops"], [])

    def test_merge_replaces_same_target_and_keeps_others(self):
        switch = {"op": "change_plan_qty", "plan_id": "PP-03", "new_qty": 900}
        later = {"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-20"}
        self.assertEqual(W.merge_ops([HUB, switch], [later]), [switch, later])


if __name__ == "__main__":
    unittest.main()
