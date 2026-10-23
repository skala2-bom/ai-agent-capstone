"""공급 지연 장기화 비교 (decisions 6.50): "약 1주"는 코드가 날짜로, "더 길어지면"은 1·4·12주를 코드가 계산한다.
네트워크·DB·모델 없이 돈다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graph as G
import whatif as W
import receipt_delay as D
from unittest.mock import AsyncMock, patch

QUESTION = ("세미온반도체 공장 화재로 MCU 발주 PO-2609-008의 입고가 약 1주 늦어질 수 있다고 합니다. "
            "릴레이 발주 PO-2610-106도 11월 27일로 늦어집니다. 두 지연을 함께 분석하고, "
            "MCU 지연이 더 길어질 경우 어디부터 생산 차질이 생기는지도 보여 줘.")
PLANS = [{"plan_id": "PP-03", "product_id": "FG-SWITCH", "product_name": "스마트 벽스위치", "plan_date": "2026-11-23", "qty": 600},
         {"plan_id": "PP-04", "product_id": "FG-PLUG", "product_name": "스마트 플러그", "plan_date": "2026-11-30", "qty": 800},
         {"plan_id": "PP-05", "product_id": "FG-HUB", "product_name": "IoT 허브", "plan_date": "2026-12-14", "qty": 400}]
DUE = {"PO-2609-008": "2026-11-27", "PO-2610-106": "2026-11-20"}
RELAY = {"op": "request_due_date_change", "po_id": "PO-2610-106", "new_due_date": "2026-11-27"}
MCU = {"op": "request_due_date_change", "po_id": "PO-2609-008", "delay_days": 7, "may_extend": True}
RECEIPTS = [{"po_id": po, "due_date": due, "status": "OPEN", "qty": 800,
             "item_id": "MCU" if po == "PO-2609-008" else "RELAY",
             "item_name": "MCU" if po == "PO-2609-008" else "릴레이"} for po, due in DUE.items()]


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
            return True, {"purchase_orders": RECEIPTS}
        G.llm, G.M.invoke, G.call_tool = object(), invoke, tool
        try:
            out = await G.classify_intent({"question": QUESTION, "role": "PLANNER"})
        finally:
            G.llm, G.M.invoke, G.call_tool = saved
        self.assertEqual(out["intent"], "IMPACT")
        self.assertEqual(out["whatif_ops"][1]["new_due_date"], "2026-12-04")
        self.assertEqual(out["extend"], [{"po_id": "PO-2609-008", "base_due": "2026-11-27"}])

    async def test_receipt_mentions_preserve_scenario_metadata_and_base_snapshot(self):
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                parsed = W.Classification(intent="IMPACT", receipt_delays=[
                    D.DelayMention(po_id="PO-2609-008", delay_days=7, may_extend=True)],
                    whatif=[W.WhatifOp(**RELAY)] if mixed else [])
                if not mixed:
                    parsed.receipt_delays.append(D.DelayMention(po_id="PO-2610-106", new_due_date="2026-11-27"))
                with patch.object(G, "llm", object()), patch.object(G.M, "invoke", AsyncMock(return_value=parsed)), \
                     patch.object(G, "load_plans", AsyncMock(return_value=PLANS)), \
                     patch.object(G, "call_tool", AsyncMock(return_value=(True, {"purchase_orders": RECEIPTS}))):
                    out = await G.classify_intent({"question": QUESTION, "role": "BUYER"})
                self.assertEqual(out["intent"], "IMPACT")
                self.assertEqual(out["extend"], [{"po_id": "PO-2609-008", "base_due": "2026-11-27"}])
                self.assertEqual({o["po_id"]: o["new_due_date"] for o in out["whatif_ops"]},
                                 {"PO-2609-008": "2026-12-04", "PO-2610-106": "2026-11-27"})
                self.assertFalse(out["receipt_delay"]["reported"])
                self.assertEqual(len(out["receipt_delay"]["changes"]), 2)
                self.assertTrue(all("delay_days" not in o and "may_extend" not in o for o in out["whatif_ops"]))

    async def test_legacy_period_cannot_bypass_receipt_date_guard(self):
        parsed = W.Classification(intent="IMPACT", whatif=[W.WhatifOp(**MCU)])
        with patch.object(G, "llm", object()), patch.object(G.M, "invoke", AsyncMock(return_value=parsed)):
            _, errors, _ = await G.classify_question("PO-2609-008 입고가 늦어졌어. 문제 있나?", PLANS, receipts=RECEIPTS)
        self.assertTrue(errors and "질문에 없는 지연 기간" in errors[0])

    async def test_invented_plan_date_is_not_authorized_by_a_relative_receipt(self):
        parsed = W.Classification(intent="IMPACT", whatif=[W.WhatifOp(**MCU),
            W.WhatifOp(op="reschedule_plan", plan_id="PP-05", new_date="2026-12-04")])
        with patch.object(G, "llm", object()), patch.object(G.M, "invoke", AsyncMock(return_value=parsed)):
            _, errors, _ = await G.classify_question(QUESTION, PLANS, receipts=RECEIPTS)
        self.assertTrue(any("질문에 없는 날짜" in e for e in errors), errors)

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
