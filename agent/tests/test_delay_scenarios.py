"""공급 지연 장기화 비교 (decisions 6.50): "약 1주"는 코드가 날짜로, "더 길어지면"은 1·4·12주를 코드가 계산한다.
네트워크·DB·모델 없이 돈다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G
import whatif as W

QUESTION = ("세미온반도체 공장 화재로 MCU 발주 PO-2609-008의 입고가 약 1주 늦어질 수 있다고 합니다. "
            "릴레이 발주 PO-2610-106도 11월 27일로 늦어집니다. 두 지연을 함께 분석하고, "
            "MCU 지연이 더 길어질 경우 어디부터 생산 차질이 생기는지도 보여 줘.")
PLANS = [{"plan_id": "PP-03", "product_id": "FG-SWITCH", "product_name": "스마트 벽스위치", "plan_date": "2026-11-23", "qty": 600},
         {"plan_id": "PP-04", "product_id": "FG-PLUG", "product_name": "스마트 플러그", "plan_date": "2026-11-30", "qty": 800},
         {"plan_id": "PP-05", "product_id": "FG-HUB", "product_name": "IoT 허브", "plan_date": "2026-12-14", "qty": 400}]
DUE = {"PO-2609-008": "2026-11-27", "PO-2610-106": "2026-11-20"}
RELAY = {"op": "request_due_date_change", "po_id": "PO-2610-106", "new_due_date": "2026-11-27"}
MCU = {"op": "request_due_date_change", "po_id": "PO-2609-008", "delay_days": 7, "may_extend": True}


def short(item, plan, date, qty, orderable=False):
    return {"item_id": item, "plan_id": plan, "plan_date": date, "projected_stock": -qty, "qty": qty, "orderable": orderable}


class DelayScenarioTest(unittest.IsolatedAsyncioTestCase):
    def test_period_becomes_due_date_plus_days_and_marks_extension(self):
        ops, extend, errors = W.resolve_delays([RELAY, MCU], DUE)
        self.assertEqual(errors, [])
        self.assertEqual(ops, [RELAY, {"op": "request_due_date_change", "po_id": "PO-2609-008", "new_due_date": "2026-12-04"}])
        self.assertEqual(extend, [{"po_id": "PO-2609-008", "base_due": "2026-11-27"}])
        self.assertTrue(W.resolve_delays([MCU], {})[2])   # 열린 발주에 없으면 되묻는다

    def test_scenarios_move_only_the_extended_po(self):
        ops, extend, _ = W.resolve_delays([RELAY, MCU], DUE)
        self.assertEqual([o["new_due_date"] for o in W.scenario_ops(ops, extend, 28)], ["2026-11-27", "2026-12-25"])
        self.assertEqual(W.scenario_ops(ops, extend, 84)[1]["new_due_date"], "2027-02-19")

    def test_extension_phrase_marks_the_uncertain_delay_when_model_forgets(self):
        forgot = [RELAY, {**MCU, "may_extend": None}]
        marked = W.mark_extension(QUESTION, forgot)
        self.assertEqual([o.get("may_extend") for o in marked], [None, True])   # 기간으로 말한 MCU만
        self.assertEqual(W.mark_extension("PO-2610-106 입고가 11/27로 늦어지면 장기화되면 어때?", [RELAY])[0]["may_extend"], True)
        self.assertEqual(W.mark_extension("두 지연을 함께 분석해 줘", forgot), forgot)

    def test_period_passes_classification_checks(self):
        raw = [W.WhatifOp(**MCU).model_dump()]
        self.assertEqual(W.validate_ops(raw, PLANS), [])
        self.assertEqual(W.unstated_date_errors(QUESTION, raw), [])

    async def test_classify_resolves_period_with_code(self):
        saved = (G.llm, G.M.invoke, G.call_tool)

        async def invoke(*a, **k):
            return W.Classification(intent="IMPACT", whatif=[W.WhatifOp(**RELAY), W.WhatifOp(**MCU)])

        async def tool(state, name, args, **k):
            if name == "list_plans":
                return True, {"plans": [dict(p) for p in PLANS]}
            if name == "get_item":
                return True, {"name": args["item_id"]}
            return True, {"purchase_orders": [{"po_id": k, "item_id": "MCU" if k.startswith("PO-2609") else "RELAY",
                                               "supplier_name": "세미온반도체", "qty": 800, "due_date": v} for k, v in DUE.items()]}
        G.llm, G.M.invoke, G.call_tool = object(), invoke, tool
        try:
            out = await G.classify_intent({"question": QUESTION, "role": "PLANNER"})
        finally:
            G.llm, G.M.invoke, G.call_tool = saved
        self.assertEqual(out["intent"], "IMPACT")
        self.assertEqual(out["whatif_ops"][1]["new_due_date"], "2026-12-04")
        self.assertEqual(out["extend"], [{"po_id": "PO-2609-008", "base_due": "2026-11-27"}])

    def test_po_without_number_must_match_item_and_be_unique(self):
        pos = [{"po_id": "PO-2609-001", "item_id": "MCU", "item_name": "MCU", "due_date": "2026-12-12"},
               {"po_id": "PO-2609-008", "item_id": "MCU", "item_name": "MCU", "due_date": "2026-11-27"},
               {"po_id": "PO-2610-106", "item_id": "RELAY", "item_name": "릴레이", "due_date": "2026-11-20"}]
        q = "세미온반도체 MCU 입고가 1주 늦어질 수 있대. 더 길어지면 어디부터 차질이 생겨?"
        relay = {"op": "request_due_date_change", "po_id": "PO-2610-106", "delay_days": 7}
        mcu = {"op": "request_due_date_change", "po_id": "PO-2609-008", "delay_days": 7}
        self.assertIn("품목(릴레이)이 없음", W.po_errors(q, [relay], pos)[0])      # 실제 V5: MCU를 물었는데 릴레이
        self.assertIn("2건", W.po_errors(q, [mcu], pos)[0])                         # MCU 발주가 둘이라 되묻는다
        self.assertEqual(W.po_errors("MCU 11/27 입고분이 1주 늦어지면?", [mcu], pos), [])  # 원래 입고일로 특정
        self.assertEqual(W.po_errors(QUESTION, [mcu], pos), [])                      # 번호가 있으면 통과
        self.assertEqual(W.po_errors("그럼 12주까지 가면?", [mcu], pos, {"PO-2609-008"}), [])   # 이어 묻기

    async def test_follow_up_inherits_extension(self):
        saved = (G.llm, G.M.invoke, G.call_tool, G.app_graph)
        ops, extend, _ = W.resolve_delays([RELAY, MCU], DUE)

        class Saved:
            async def aget_state(self, cfg):
                return type("S", (), {"values": {"question": QUESTION, "whatif_ops": ops, "extend": extend, "intent": "IMPACT"}})()

        async def invoke(*a, **k):
            return W.Classification(intent="IMPACT", whatif=[], follow_up=True)

        async def tool(state, name, args, **k):
            if name == "list_plans":
                return True, {"plans": [dict(p) for p in PLANS]}
            if name == "get_item":
                return True, {"name": args["item_id"]}
            return True, {"purchase_orders": []}
        G.llm, G.M.invoke, G.call_tool, G.app_graph = object(), invoke, tool, Saved()
        try:
            out = await G.classify_intent({"question": "그럼 12주까지 가면 허브 말고 또 어디가 멈춰?", "role": "PLANNER",
                                           "parent_thread_id": "parent"})
        finally:
            G.llm, G.M.invoke, G.call_tool, G.app_graph = saved
        self.assertEqual(out["whatif_ops"], ops)
        self.assertEqual(out["extend"], extend)

    async def test_three_scenarios_and_where_it_breaks_first(self):
        saved = G.call_tool
        results = {"2026-12-04": [short("RELAY", "PP-03", "2026-11-23", 1000), short("MCU", "PP-04", "2026-11-30", 500)],
                   "2026-12-25": [short("RELAY", "PP-03", "2026-11-23", 1000), short("MCU", "PP-04", "2026-11-30", 500),
                                  short("MCU", "PP-05", "2026-12-14", 400)]}
        sent = []

        async def tool(state, name, args, **k):
            sent.append(args["whatif"])
            return True, {"shortages": results.get(args["whatif"][1]["new_due_date"], results["2026-12-25"])}
        G.call_tool = tool
        try:
            ops, extend, _ = W.resolve_delays([RELAY, MCU], DUE)
            sc = await G.delay_scenarios({"whatif_ops": ops, "extend": extend})
        finally:
            G.call_tool = saved
        self.assertEqual([s["weeks"] for s in sc], [1, 4, 12])
        self.assertTrue(all(w[0] == RELAY for w in sent))   # 확정 지연은 그대로
        lines = G.scenario_summary(sc, PLANS)
        self.assertIn("새로 IoT 허브 생산(PP-05, 2026-12-14) MCU 400개", lines[1])
        self.assertIn("새로 생긴 결품 없음", lines[2])


if __name__ == "__main__":
    unittest.main()
