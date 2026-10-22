"""Task review graph checks with in-memory checkpoints; no model/business DB calls."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
import uuid

import httpx
from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import graph as G
import task_batch as B
from approval_runner import ApprovalRunner, config


class BatchReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.task = uuid.uuid4().hex
        self.cfg = config(self.task)
        self.graph = G.builder.compile(checkpointer=InMemorySaver())
        self.store = AsyncMock()
        self.store.begin_registration.side_effect = lambda pid, task, payload: ({}, True)
        self.runner = ApprovalRunner(self.graph, self.store, lambda c: {})
        self.proposals = []
        for i, item in enumerate(("MCU", "RELAY"), 1):
            target = {"item_id": item, "rule_id": "INV-001", "plan_id": "PP-01", "qty": 100,
                      "projected_stock": -100, "orderable": False}
            self.proposals.append({"proposal_id": item, "revision": 1, "target": "INV-001|" + item,
                "option": {"option_id": item, "tool": "split_po", "args": {"po_id": f"PO-2610-{i:03d}",
                    "split_qty": 100, "new_due_date": "2026-11-25"}, "item_id": item, "target_rule": "INV-001", "summary": item},
                "targets": [target], "verify": {"passed": True}, "status": "PENDING", "approval_id": "APR-" + item})
        state = {"task_id": self.task, "thread_id": self.task, "workflow_version": 3, "intent": "ADJUST",
            "role": "BUYER", "plans": [], "proposals": self.proposals, "processed_commands": [],
            "scope": [p["target"] for p in self.proposals], "batch_revision": 0, "batch_status": "REVIEW",
            "batch_context": {"fingerprint": "test"}, "manager_reviews": {}, "executions": [], "current_ops": [],
            "impact": {"shortages": [p["targets"][0] for p in self.proposals]}, "current_impact": {"shortages": [p["targets"][0] for p in self.proposals]}}
        await self.graph.aupdate_state(self.cfg, state, as_node="batch_idle")
        await self.graph.ainvoke(None, self.cfg)
        self.backend = AsyncMock(side_effect=self.backend_call)
        self.emit = AsyncMock(side_effect=lambda task, event, **kw: httpx.Response(200, json={"approvalId": "APR-" + event.get("proposalId", "test")}))
        self.patches = [patch.object(B, "backend", self.backend), patch.object(G, "emit", self.emit),
                        patch.object(G, "call_tool", AsyncMock(side_effect=self.tool)), patch.object(G, "llm", None)]
        for p in self.patches: p.start()
    async def asyncTearDown(self):
        for p in reversed(self.patches): p.stop()
    async def backend_call(self, task, path, body=None):
        if path == "complete":
            state = (await self.graph.aget_state(self.cfg)).values
            if state["batch_last_command"]["action"] == "CANCEL":
                return {"status": "CANCELLED", "executions": []}
            return {"status": "COMPLETED", "executions": [
                {"approval_id": p["approval_id"], "tool": p["option"]["tool"], "args": p["option"]["args"], "result": {}}
                for p in state["proposals"] if state.get("manager_reviews", {}).get(p["proposal_id"], {}).get("status") == "CONFIRMED"],
                "impact": {}, "remainingWhatif": []}
        return {}
    async def tool(self, state, name, args, **kw):
        if name == "analyze_impact": return True, state["impact"]
        if name == "compare_options": return True, {"options": []}
        self.fail("Unexpected business mutation " + name)
    async def send(self, action, item="MCU", **extra):
        state = (await self.graph.aget_state(self.cfg)).values
        key = uuid.uuid4().hex
        body = {"requestId": key, "expectedRevision": state.get("batch_revision", 0), "action": action,
                "approvalId": "APR-" + item, "reason": "검토 사유", "actorId": "approver", **extra}
        command = {"command_id": key, "task_id": self.task, "thread_id": self.task, "kind": "BATCH", "payload": body}
        await self.runner.process(command)
        return (await self.graph.aget_state(self.cfg)).values, command
    async def test_review_waits_and_execute_runs_only_once(self):
        state, _ = await self.send("CONFIRM")
        self.assertEqual(state["proposals"][0]["status"], "PENDING")
        self.assertFalse(B.view(state)["canExecute"])
        self.backend.assert_not_awaited()
        state, _ = await self.send("CONFIRM", "RELAY")
        self.assertTrue(B.view(state)["canExecute"])
        state, command = await self.send("EXECUTE")
        self.assertEqual(state["batch_status"], "COMPLETED")
        self.assertTrue(all(p["status"] == "EXECUTED" for p in state["proposals"]))
        await self.runner.process(command)
        self.assertEqual(sum(c.args[1] == "complete" for c in self.backend.await_args_list), 1)
    async def test_manual_revision_does_not_mutate_business_and_needs_new_check(self):
        state, _ = await self.send("MANUAL", args={"po_id": "PO-2610-001", "new_qty": 500, "new_due_date": "2026-11-25"})
        self.assertEqual(len(state["proposals"]), 3)
        self.assertEqual(state["proposals"][-1]["source"], "MANUAL")
        self.assertEqual(state["proposals"][-1]["status"], "PENDING")
        self.assertEqual(B.view(state)["entries"][0]["reviewStatus"], "UNREVIEWED")
        self.assertFalse(any(c.args[1] == "complete" for c in self.backend.await_args_list))
        manual_id = state["proposals"][-1]["approval_id"]
        await self.send("CONFIRM", approvalId=manual_id)
        await self.send("CONFIRM", "RELAY")
        state, _ = await self.send("EXECUTE")
        self.assertEqual(state["batch_status"], "COMPLETED")
        self.assertIn("직접 작성한 수정안", state["answer"])
    async def test_no_alternative_stays_reviewable_for_manual_edit(self):
        state, _ = await self.send("REPLAN")
        entry = B.view(state)["entries"][0]
        self.assertEqual(entry["outcome"]["code"], "NO_ALTERNATIVE")
        self.assertTrue((await self.graph.aget_state(self.cfg)).next)
        state, _ = await self.send("MANUAL", args={"po_id": "PO-2610-001", "new_qty": 500, "new_due_date": "2026-11-25"})
        self.assertEqual(state["proposals"][-1]["source"], "MANUAL")
    async def test_cancel_finishes_without_execution(self):
        state, _ = await self.send("CANCEL")
        self.assertEqual(state["batch_status"], "CANCELLED")
        self.assertFalse(state["executions"])
    async def test_unknown_result_can_recover_with_same_request(self):
        await self.send("CONFIRM")
        state, _ = await self.send("CONFIRM", "RELAY")
        self.backend.side_effect = httpx.ReadTimeout("lost response")
        body = {"requestId": "stable", "expectedRevision": state["batch_revision"], "action": "EXECUTE", "actorId": "approver"}
        command = {"command_id": "stable", "task_id": self.task, "thread_id": self.task, "kind": "BATCH", "payload": body}
        with self.assertRaises(httpx.ReadTimeout): await self.runner.process(command)
        self.assertEqual((await self.graph.aget_state(self.cfg)).values["batch_status"], "UNKNOWN")
        self.backend.side_effect = self.backend_call
        await self.runner.process(command)
        self.assertEqual((await self.graph.aget_state(self.cfg)).values["batch_status"], "COMPLETED")

    async def test_response_render_failure_after_commit_can_recover(self):
        await self.send("CONFIRM")
        state, _ = await self.send("CONFIRM", "RELAY")
        body = {"requestId": "render-retry", "expectedRevision": state["batch_revision"], "action": "EXECUTE", "actorId": "approver"}
        command = {"command_id": "render-retry", "task_id": self.task, "thread_id": self.task, "kind": "BATCH", "payload": body}
        with patch.object(G, "_adjust_answer", side_effect=ValueError("render failed")):
            with self.assertRaises(RuntimeError): await self.runner.process(command)
        state = (await self.graph.aget_state(self.cfg)).values
        self.assertEqual(state["batch_status"], "UNKNOWN")
        self.assertNotIn("render-retry", state["processed_commands"])
        await self.runner.process(command)
        self.assertEqual((await self.graph.aget_state(self.cfg)).values["batch_status"], "COMPLETED")

if __name__ == "__main__": unittest.main()
