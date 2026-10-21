import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adjust_tools import execute_adjust_calls, mask_financials


class AdjustToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_financial_visibility_follows_role(self):
        response = {"unit_price": 3500, "amount": 1750000, "added_cost": 390000}
        self.assertEqual(mask_financials(response, "PLANNER"),
                         {"unit_price": None, "amount": None, "added_cost": None})
        self.assertEqual(mask_financials(response, "BUYER"),
                         {"unit_price": None, "amount": 1750000, "added_cost": 390000})
        self.assertEqual(mask_financials(response, "APPROVER"), response)
        self.assertEqual(response["unit_price"], 3500)

    async def test_compare_preserves_backend_action_and_masks_planner_cost(self):
        issues = [{"item_id": "MCU", "qty": 100, "plan_date": "2026-11-25"}]
        option = {
            "option_id": "OPT-1", "tool": "split_po",
            "args": {"po_id": "PO-2609-001", "split_qty": 100, "new_due_date": "2026-11-25"},
            "added_cost": 390000, "feasible": True,
        }
        invoked = []
        events = []

        async def invoke(name, args):
            invoked.append((name, args))
            return {"policy_version": "v0.1", "options": [option]}

        async def emit(event):
            events.append(event)

        records, options = await execute_adjust_calls(
            [{"id": "call-1", "name": "compare_options", "args": {"issues": issues}}],
            invoke, emit, role="PLANNER",
        )

        self.assertEqual(invoked, [("compare_options", {"issues": issues})])
        self.assertEqual([event["type"] for event in events], ["TOOL_START", "TOOL_END"])
        self.assertTrue(events[-1]["ok"])
        self.assertEqual(options[0]["tool"], "split_po")
        self.assertEqual(options[0]["args"], option["args"])
        self.assertIsNone(options[0]["added_cost"])
        self.assertIsNone(json.loads(records[0]["content"])["options"][0]["added_cost"])
        self.assertEqual(option["added_cost"], 390000)  # 원본 backend 응답은 바꾸지 않는다

    async def test_change_tool_is_never_invoked(self):
        invoked = []
        events = []

        async def invoke(name, args):
            invoked.append(name)
            return {}

        async def emit(event):
            events.append(event)

        records, options = await execute_adjust_calls(
            [{"id": "call-2", "name": "split_po", "args": {"po_id": "PO-2609-001"}}],
            invoke, emit, role="APPROVER",
        )

        self.assertEqual(invoked, [])
        self.assertIsNone(options)
        self.assertIn("TOOL_NOT_ALLOWED", records[0]["content"])
        self.assertFalse(events[-1]["ok"])

    async def test_invalid_compare_result_is_reported_as_tool_error(self):
        async def invoke(name, args):
            return {"policy_version": "v0.1"}

        async def emit(event):
            pass

        records, options = await execute_adjust_calls(
            [{"id": "call-3", "name": "compare_options", "args": {"issues": []}}],
            invoke, emit,
        )
        self.assertIn("INVALID_TOOL_RESULT", records[0]["content"])
        self.assertIsNone(options)


if __name__ == "__main__":
    unittest.main()
