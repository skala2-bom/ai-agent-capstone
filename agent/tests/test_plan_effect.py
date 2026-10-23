"""생산계획 변경 효과 (decisions 6.52): 다른 가정(입고 지연) 위에서 계획을 바꿔 보면 바꾸기 전과 비교한다.
팀 시나리오 ②: 릴레이 11/27 + MCU 1주 위에서 PP-03을 11/23 → 11/28. 네트워크·DB·모델 없이 돈다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G
import whatif as W

Q2 = ("릴레이가 11월 27일에 들어온다면 벽스위치 PP-03 600대의 생산일을 11월 23일에서 11월 28일로 옮겼을 때 "
      "릴레이 결품이 해소되고, 다른 제품에 새 결품은 없나요?")
PLANS = [{"plan_id": "PP-03", "product_id": "FG-SWITCH", "product_name": "스마트 벽스위치", "plan_date": "2026-11-23", "qty": 600},
         {"plan_id": "PP-04", "product_id": "FG-PLUG", "product_name": "스마트 플러그", "plan_date": "2026-11-30", "qty": 800}]
RELAY = {"op": "request_due_date_change", "po_id": "PO-2610-106", "new_due_date": "2026-11-27"}
MCU = {"op": "request_due_date_change", "po_id": "PO-2609-008", "new_due_date": "2026-12-04"}
MOVE = {"op": "reschedule_plan", "plan_id": "PP-03", "new_date": "2026-11-28"}


def short(item, plan, date, qty):
    return {"item_id": item, "plan_id": plan, "plan_date": date, "projected_stock": -qty, "qty": qty, "orderable": False}


class PlanEffectTest(unittest.IsolatedAsyncioTestCase):
    def test_effect_compares_by_item_and_plan(self):
        before = {"shortages": [short("RELAY", "PP-03", "2026-11-23", 1000), short("MCU", "PP-04", "2026-11-30", 500)]}
        after = {"shortages": [short("MCU", "PP-04", "2026-11-30", 500)]}
        e = W.plan_effect(before, after)
        self.assertEqual([(s["item_id"], s["plan_id"]) for s in e["resolved"]], [("RELAY", "PP-03")])
        self.assertEqual(e["new"], [])
        self.assertEqual([(s["item_id"], s["before_qty"]) for s in e["remaining"]], [("MCU", 500)])
        lines = G.effect_lines(e, PLANS)
        self.assertEqual(lines[0], "해소된 결품: 스마트 벽스위치 생산(PP-03, 2026-11-23) RELAY 1,000개")
        self.assertEqual(lines[1], "새로 생긴 결품: 없음")

    async def test_restated_delay_makes_it_a_follow_up_and_sets_compare_base(self):
        saved = (G.llm, G.M.invoke, G.call_tool, G.app_graph)

        class Saved:   # 앞 질문(시나리오 ①)의 저장된 State
            async def aget_state(self, cfg):
                return type("S", (), {"values": {"question": "①", "whatif_ops": [RELAY, MCU], "intent": "IMPACT",
                                                 "extend": [{"po_id": "PO-2609-008", "base_due": "2026-11-27"}]}})()

        async def invoke(*a, **k):   # 모델이 follow_up을 빠뜨리고 릴레이 지연을 다시 적은 경우
            return W.Classification(intent="IMPACT", whatif=[W.WhatifOp(**RELAY), W.WhatifOp(**MOVE)], follow_up=False)

        async def tool(state, name, args, **k):
            if name == "list_plans":
                return True, {"plans": [dict(p) for p in PLANS]}
            if name == "get_item":
                return True, {"name": args["item_id"]}
            return True, {"purchase_orders": [{"po_id": "PO-2610-106", "item_id": "RELAY", "supplier_name": "파워링크전자",
                                               "qty": 1200, "due_date": "2026-11-20"}]}
        G.llm, G.M.invoke, G.call_tool, G.app_graph = object(), invoke, tool, Saved()
        try:
            out = await G.classify_intent({"question": Q2, "role": "PLANNER", "parent_thread_id": "p"})
        finally:
            G.llm, G.M.invoke, G.call_tool, G.app_graph = saved
        self.assertEqual(out["intent"], "IMPACT")
        self.assertEqual(sorted(map(str, out["whatif_ops"])), sorted(map(str, [RELAY, MCU, MOVE])))   # MCU 1주를 잃지 않음
        self.assertEqual(sorted(map(str, out["compare_base"])), sorted(map(str, [RELAY, MCU])))
        self.assertEqual(out["extend"], [])   # 일정 변경 질문에는 장기화 비교를 다시 붙이지 않는다

    def test_dropped_or_unchanged_supply_premise_asks_back(self):
        pos = [{"po_id": "PO-2610-106", "item_id": "RELAY", "item_name": "릴레이", "due_date": "2026-11-20"},
               {"po_id": "PO-2610-114", "item_id": "RELAY", "item_name": "릴레이", "due_date": "2026-11-27"},
               {"po_id": "PO-2609-008", "item_id": "MCU", "item_name": "MCU", "due_date": "2026-11-27"}]
        q = "릴레이가 11월 27일에 들어오는데 벽스위치 PP-03 생산을 11월 23일에서 11월 26일로 옮기면 릴레이 결품이 풀려?"
        move = {"op": "reschedule_plan", "plan_id": "PP-03", "new_date": "2026-11-26"}
        self.assertIn("릴레이 발주가 없음", W.supply_premise_errors(q, [move], pos)[0])   # 실측 P3: 릴레이 입고를 뺐다
        unchanged = {"op": "request_due_date_change", "po_id": "PO-2610-114", "new_due_date": "2026-11-27"}
        self.assertIn("바뀌는 것이 없음", W.supply_premise_errors(q, [unchanged, move], pos)[0])
        self.assertEqual(W.supply_premise_errors(Q2, [MOVE], pos, {"PO-2610-106", "PO-2609-008"}), [])   # ①에 이어서
        self.assertEqual(W.supply_premise_errors("PP-03을 11/28로 옮기면?", [MOVE], pos), [])            # 입고 이야기 없음

    def test_plan_only_question_has_no_compare(self):
        self.assertFalse(W.restates_previous([MOVE], [RELAY, MCU]))


if __name__ == "__main__":
    unittest.main()
