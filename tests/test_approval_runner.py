"""Failure/recovery checks with real agent PostgreSQL checkpoints and a stub MCP.

Uses a separate database container from the live integration agent, so its inbox
worker cannot consume test commands. No business DB or model calls are made.
"""
import asyncio
import copy
import os
from contextlib import AsyncExitStack
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import AsyncMock, patch

import httpx
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import NodeCancelledError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import graph as G
import task_batch as B
from approval_store import ApprovalStore
from approval_runner import ApprovalRunner, config, interrupts


def issue(item):
    return {"item_id": item, "plan_id": "PP-1", "plan_date": "2026-11-23",
            "projected_stock": -10, "orderable": False, "rule_id": "INV-001"}


@unittest.skipUnless(os.getenv("AGENT_TEST_DB_URL"), "격리된 agent 테스트 DB가 필요합니다.")
class ApprovalRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dsn = os.environ["AGENT_TEST_DB_URL"]
        if "agent-test-db" not in self.dsn or not self.dsn.endswith("/capstone_ai_test"):
            raise RuntimeError("agent 단위 테스트 전용 DB만 사용합니다.")
        self.stack = AsyncExitStack()
        self.task = "unit-" + uuid.uuid4().hex
        self.store = ApprovalStore(self.dsn)
        await self.store.open()
        self.stack.push_async_callback(self.store.close)
        self.saver = await self.stack.enter_async_context(AsyncPostgresSaver.from_conn_string(self.dsn))
        await self.saver.setup()
        self.graph = G.builder.compile(checkpointer=self.saver)
        self.runner = ApprovalRunner(self.graph, self.store, lambda c: self.fail("Unexpected new start"))
        self.cfg = config(self.task)
        self.changed = set()
        self.writes = []
        self.options = {}
        self.proposals = []
        for item in ("A", "B"):
            opt = {"option_id": "OPT-" + item, "tool": "split_po", "args": {
                "po_id": "PO-" + item, "split_qty": 10, "new_due_date": "2026-11-23"},
                "item_id": item, "target_rule": "INV-001", "summary": item + " 분할", "added_cost": 10,
                "risk": "LOW", "feasible": True, "resolves": True, "side_effects": [], "evidence": []}
            self.options[item] = opt
            self.proposals.append({"proposal_id": self.task + item, "revision": 1, "target": "INV-001|" + item,
                "option": copy.deepcopy(opt), "targets": [issue(item)], "verify": {"passed": True},
                "status": "PREPARED", "approval_id": None})
        state = {"task_id": self.task, "thread_id": self.task, "intent": "ADJUST", "role": "BUYER",
                 "workflow_version": 2, "processed_commands": ["start:" + self.task],
                 "proposals": self.proposals, "current_ops": [], "whatif_ops": [], "blocked": {},
                 "current_impact": {"shortages": [issue("A"), issue("B")]},
                 "impact": {"shortages": [issue("A"), issue("B")]}, "executions": [],
                 "scope": ["INV-001|A", "INV-001|B"]}
        await self.graph.aupdate_state(self.cfg, state, as_node="check_batch")
        self.emit = AsyncMock(side_effect=lambda task, event, **kw:
            httpx.Response(200, json={"approvalId": "APR-" + event["proposalId"]})
            if event["type"] == "APPROVAL_REQUIRED" else httpx.Response(200, json={}))
        self.stack.enter_context(patch.object(G, "emit", self.emit))
        self.stack.enter_context(patch.object(G, "execution_store", self.store))
        self.stack.enter_context(patch.object(G, "call_tool", side_effect=self.tool))
        self.stack.enter_context(patch.object(G, "destructive_tools", AsyncMock(return_value={"split_po"})))
        self.stack.enter_context(patch.object(G, "llm", None))

    async def asyncTearDown(self):
        await self.stack.aclose()

    async def tool(self, state, name, args, **kwargs):
        if name == "analyze_impact":
            simulated = self.changed | {op["po_id"].removeprefix("PO-") for op in args["whatif"]}
            return True, {"shortages": [issue(i) for i in ("A", "B") if i not in simulated]}
        if name == "compare_options":
            return True, {"options": [o for i, o in self.options.items() if i not in self.changed]}
        if name == "split_po":
            item = args["po_id"].removeprefix("PO-")
            self.writes.append(args["approval_id"])
            self.changed.add(item)
            return True, {"original": {"po_id": args["po_id"], "qty": 90, "due_date": "2026-12-01"},
                          "new": {"po_id": args["po_id"] + "-child", "qty": 10, "due_date": args["new_due_date"]}}
        self.fail("Unexpected tool: " + name)

    async def enqueue(self, kind="START", item="A", decision="APPROVED"):
        approval = "APR-" + self.task + item
        key = "start:" + self.task if kind == "START" else "decision:" + approval
        payload = {} if kind == "START" else {"approvalId": approval, "decision": decision, "reason": "test"}
        await self.store.receive(key, self.task, self.task, kind, payload)
        return (await self.store.query("SELECT * FROM agent.workflow_command WHERE command_id=%s", (key,)))[0]

    async def start(self):
        await asyncio.wait_for(self.runner.process(await self.enqueue()), 10)

    async def values(self):
        return (await self.graph.aget_state(self.cfg)).values

    async def test_all_registered_before_wait_and_reverse_partial_approval(self):
        await self.start()
        self.assertEqual(self.emit.await_count, 2)
        self.assertEqual([p["status"] for p in (await self.values())["proposals"]], ["PENDING", "PENDING"])
        self.assertEqual(self.writes, [])
        command = await self.enqueue("DECISION", "B")
        await self.runner.process(command)
        v = await self.values()
        self.assertEqual([p["status"] for p in v["proposals"]], ["PENDING", "EXECUTED"])
        self.assertTrue(interrupts(await self.graph.aget_state(self.cfg)))
        self.assertEqual(len(self.writes), 1)
        # Re-delivered inbox command must not approve the remaining A proposal.
        await self.runner.process(command)
        self.assertEqual(len(self.writes), 1)
        await self.runner.process(await self.enqueue("DECISION", "A"))
        self.assertEqual(len(self.writes), 2)
        self.assertEqual((await self.values())["result"]["approval_summary"]["EXECUTED"], 2)

    async def test_batch_review_survives_new_checkpoint_connection(self):
        await self.start()
        await self.graph.aupdate_state(self.cfg, {"workflow_version": 3, "batch_context": {"fingerprint": "test"},
            "batch_revision": 0, "batch_status": "REVIEW", "manager_reviews": {}}, as_node="batch_idle")
        await self.graph.ainvoke(None, self.cfg)
        key = "batch-review:" + self.task
        payload = {"requestId": key, "action": "CONFIRM", "approvalId": "APR-" + self.task + "B",
                   "expectedRevision": 0, "actorId": "approver"}
        await self.store.receive(key, self.task, self.task, "BATCH", payload)
        command = (await self.store.query("select * from agent.workflow_command where command_id=%s", (key,)))[0]
        await self.runner.process(command)
        saver = await self.stack.enter_async_context(AsyncPostgresSaver.from_conn_string(self.dsn))
        self.graph = G.builder.compile(checkpointer=saver)
        self.runner = ApprovalRunner(self.graph, self.store, self.runner.initial_state)
        await self.runner.process(command)
        state = await self.values()
        self.assertEqual(state["batch_revision"], 1)
        self.assertEqual([e["reviewStatus"] for e in B.view(state)["entries"]], ["UNREVIEWED", "CONFIRMED"])
        self.assertEqual(self.writes, [])

    async def test_decision_received_during_registration_is_not_lost(self):
        command = await self.enqueue("DECISION", "B")
        await self.start()
        await self.runner.process(command)
        self.assertEqual(self.changed, {"B"})

    async def test_task_readiness_is_stable_and_all_withdrawn_never_execute(self):
        await self.start()
        async def readiness():
            events = await self.store.query("SELECT payload FROM agent.workflow_outbox WHERE task_id=%s AND payload->>'type'='TASK_REVIEW_READY'", (self.task,))
            return [e["payload"] for e in events]
        ready = await readiness()
        self.assertEqual(len(ready), 1)
        self.assertEqual(set(ready[0]["approvalIds"]), {"APR-" + self.task + "A", "APR-" + self.task + "B"})
        await self.runner.process(await self.enqueue("DECISION", "A", "WITHDRAWN"))
        self.assertEqual(await readiness(), ready)
        await self.runner.process(await self.enqueue("DECISION", "B", "WITHDRAWN"))
        self.assertEqual(self.writes, [])
        self.assertEqual([p["status"] for p in (await self.values())["proposals"]], ["WITHDRAWN", "WITHDRAWN"])
        events = await self.store.query("SELECT payload FROM agent.workflow_outbox WHERE task_id=%s AND payload->>'type'='DONE'", (self.task,))
        self.assertEqual(len(events), 1)

    async def test_upgrade_restores_readiness_for_existing_idle_drafts_without_tool_calls(self):
        await self.start()
        await self.store.query("DELETE FROM agent.workflow_outbox WHERE task_id=%s AND payload->>'type'='TASK_REVIEW_READY'", (self.task,))
        before_calls = self.emit.await_count
        before = await self.values()
        restarted = ApprovalRunner(self.graph, self.store, self.runner.initial_state)
        await restarted.recover_review_readiness()
        events = await self.store.query("SELECT payload FROM agent.workflow_outbox WHERE task_id=%s AND payload->>'type'='TASK_REVIEW_READY'", (self.task,))
        self.assertEqual(len(events), 1)
        self.assertEqual(await self.values(), before)
        self.assertEqual(self.emit.await_count, before_calls)
        self.assertEqual(self.writes, [])

    async def old_reproposal(self):
        p = self.proposals[0]
        p.update(revision=2, replaces="old-proposal", previous_approval_id="APR-old")
        await self.graph.aupdate_state(self.cfg, {"proposals": self.proposals}, as_node="check_batch")
        await self.start()
        await self.store.query("UPDATE agent.approval_binding SET payload=payload-'previousApprovalId' WHERE proposal_id=%s", (p["proposal_id"],))
        self.emit.reset_mock()
        return p

    async def test_old_reproposal_is_forwarded_by_known_id_without_restarting_graph(self):
        p = await self.old_reproposal()
        before = await self.values()
        await self.runner.recover_reproposals()
        self.emit.assert_awaited_once()
        payload = self.emit.call_args.args[1]
        self.assertEqual(payload["approvalId"], "APR-" + p["proposal_id"])
        self.assertEqual(payload["previousApprovalId"], "APR-old")
        self.assertEqual(await self.values(), before)
        self.assertEqual(self.writes, [])
        await self.runner.recover_reproposals()
        self.emit.assert_awaited_once()

    async def test_reproposal_forwarding_response_loss_retries_same_known_card_only(self):
        p = await self.old_reproposal()
        self.emit.side_effect = [None, httpx.Response(200, json={"approvalId": "APR-" + p["proposal_id"]})]
        await self.runner.recover_reproposals()
        await self.runner.recover_reproposals()
        self.assertEqual(self.emit.await_count, 2)
        self.assertEqual(self.emit.await_args_list[0], self.emit.await_args_list[1])
        await self.runner.recover_reproposals()
        self.assertEqual(self.emit.await_count, 2)
        self.assertEqual(self.writes, [])

    async def test_reproposal_recovery_never_reposts_unknown_registration(self):
        p = await self.old_reproposal()
        await self.store.bind(p["proposal_id"], "UNKNOWN", error="response lost")
        await self.runner.recover_reproposals()
        self.emit.assert_not_awaited()

    async def test_observed_resume_links_runs_and_done_stays_last(self):
        await self.runner.process_observed(await self.enqueue())
        rows = await self.store.query("SELECT payload FROM agent.workflow_outbox WHERE task_id=%s ORDER BY id", (self.task,))
        first = next(r["payload"]["traceId"] for r in rows if r["payload"]["type"] == "TRACE")
        await self.runner.process_observed(await self.enqueue("DECISION", "B"))
        await self.runner.process_observed(await self.enqueue("DECISION", "A"))
        rows = await self.store.query("SELECT payload FROM agent.workflow_outbox WHERE task_id=%s ORDER BY id", (self.task,))
        events = [r["payload"] for r in rows]
        traces = [e for e in events if e["type"] == "TRACE"]
        self.assertEqual(len({e["traceId"] for e in traces}), 3)
        self.assertTrue(any(e.get("previousTraceId") == first for e in traces))
        self.assertEqual(events[-1]["type"], "DONE")
        self.assertTrue(all(e.get("traceId") for e in events))
        self.assertEqual(len(self.writes), 2)

    async def test_observed_timeout_holds_work_without_replaying_a_change(self):
        command = await self.enqueue()
        async def slow(_):
            await asyncio.sleep(1)
        with patch.object(self.runner, "process", side_effect=slow), patch("approval_runner.WORK_TIMEOUT", 0.01):
            await self.runner.process_observed(command)
        row = (await self.store.query("SELECT status FROM agent.workflow_command WHERE command_id=%s", (command["command_id"],)))[0]
        self.assertEqual(row["status"], "HELD")
        events = [r["payload"] for r in await self.store.query(
            "SELECT payload FROM agent.workflow_outbox WHERE task_id=%s ORDER BY id", (self.task,))]
        self.assertTrue(any(e["type"] == "WORKFLOW_NOTICE" for e in events))
        self.assertEqual(events[-1]["outcome"], "HELD")
        self.assertFalse(any(e["type"] in {"DONE", "FAILED"} for e in events))
        self.assertEqual(self.writes, [])

    async def test_changed_cost_blocks_one_card_but_other_executes(self):
        await self.start()
        self.options["A"]["added_cost"] = 99
        await self.runner.process(await self.enqueue("DECISION", "A"))
        await self.runner.process(await self.enqueue("DECISION", "B"))
        self.assertEqual(self.changed, {"B"})
        self.assertEqual([p["status"] for p in (await self.values())["proposals"]], ["BLOCKED", "EXECUTED"])

    async def test_ambiguous_registration_is_not_reposted_or_reported_done(self):
        self.emit.side_effect = [None, httpx.Response(200, json={"approvalId": "APR-" + self.task + "B"})]
        await self.start()
        self.assertEqual([p["status"] for p in (await self.values())["proposals"]], ["REGISTRATION_UNKNOWN", "PENDING"])
        await self.runner.process(await self.enqueue("DECISION", "B"))
        self.assertEqual(self.changed, {"B"})
        self.assertTrue(interrupts(await self.graph.aget_state(self.cfg)))
        self.assertEqual(self.emit.await_count, 2)
        events = await self.store.query("SELECT payload FROM agent.workflow_outbox WHERE task_id=%s", (self.task,))
        self.assertFalse(any(e["payload"]["type"] in {"DONE", "TASK_REVIEW_READY"} for e in events))

    async def test_crash_after_registration_reply_reuses_binding(self):
        original = self.store.bind
        async def crash_once(*args, **kwargs):
            await original(*args, **kwargs)
            raise asyncio.CancelledError()
        command = await self.enqueue()
        with patch.object(self.store, "bind", side_effect=crash_once):
            with self.assertRaises(asyncio.CancelledError):
                await self.runner.process(command)
        await self.runner.process(command)
        self.assertEqual(self.emit.await_count, 2)  # A reply was persisted, so only B is posted again.
        self.assertEqual([p["status"] for p in (await self.values())["proposals"]], ["PENDING", "PENDING"])

    async def test_crash_after_write_journal_before_checkpoint_replays_result(self):
        await self.start()
        command = await self.enqueue("DECISION", "A")
        original = self.store.finish_attempt
        async def saved_then_crash(*args):
            await original(*args)
            raise asyncio.CancelledError()
        with patch.object(self.store, "finish_attempt", side_effect=saved_then_crash):
            with self.assertRaises(NodeCancelledError):
                await self.runner.process(command)
        # New checkpoint connection and graph object: nothing is recovered from process memory.
        saver = await self.stack.enter_async_context(AsyncPostgresSaver.from_conn_string(self.dsn))
        self.graph = G.builder.compile(checkpointer=saver)
        self.runner = ApprovalRunner(self.graph, self.store, lambda c: self.fail("Unexpected start"))
        await self.runner.process(command)
        self.assertEqual(len(self.writes), 1)
        self.assertEqual((await self.values())["proposals"][0]["status"], "EXECUTED")

    async def test_crash_before_execution_result_is_unknown_not_retried(self):
        await self.start()
        command = await self.enqueue("DECISION", "A")
        with patch.object(self.store, "finish_attempt", side_effect=asyncio.CancelledError):
            with self.assertRaises(NodeCancelledError):
                await self.runner.process(command)
        await self.runner.process(command)
        await self.runner.process(await self.enqueue("DECISION", "B"))
        self.assertEqual(len(self.writes), 2)
        self.assertEqual([p["status"] for p in (await self.values())["proposals"]], ["EXECUTION_UNKNOWN", "EXECUTED"])
        self.assertTrue(interrupts(await self.graph.aget_state(self.cfg)))

    async def test_receipt_is_deduplicated_and_conflicting_replay_rejected(self):
        command = await self.enqueue("DECISION")
        self.assertFalse(await self.store.receive(command["command_id"], self.task, self.task, "DECISION", command["payload"]))
        with self.assertRaises(ValueError):
            await self.store.receive(command["command_id"], self.task, self.task, "DECISION", {"decision": "REJECTED"})
        # Independent DB sessions cannot advance the same checkpoint concurrently.
        async with self.store.thread_lock(self.task) as first:
            async with self.store.thread_lock(self.task) as second:
                self.assertTrue(first)
                self.assertFalse(second)
        async with self.store.thread_lock(self.task) as third:
            self.assertTrue(third)

    async def test_same_po_candidates_are_not_prepared_independently(self):
        state = {"options": list(self.options.values()), "proposals": [{**self.proposals[0], "status": "PENDING"}]}
        self.assertEqual([o["item_id"] for o in G.candidates(state)], ["B"])

    async def test_http_accepts_and_persists_decision_before_graph_wait(self):
        import main as M
        body = {"threadId": self.task, "approvalId": "APR-" + self.task + "A", "decision": "APPROVED"}
        with patch.object(M, "store", self.store), patch.object(M, "app_graph", self.graph):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=M.app), base_url="http://test") as client:
                reply = await client.post(f"/agent/tasks/{self.task}/resume", json=body)
                self.assertEqual(reply.status_code, 202)
                rows = await self.store.query("SELECT * FROM agent.workflow_command WHERE task_id=%s", (self.task,))
                self.assertEqual((rows[0]["status"], rows[0]["payload"]["approvalId"]), ("PENDING", body["approvalId"]))
                again = await client.post(f"/agent/tasks/{self.task}/resume", json=body)
                self.assertTrue(again.json()["duplicate"])
                invalid = await client.post(f"/agent/tasks/{self.task}/resume", json={**body, "decision": "REJECTED"})
                self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.writes, [])

    async def test_http_without_durable_store_does_not_acknowledge(self):
        import main as M
        with patch.object(M, "store", None):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=M.app), base_url="http://test") as client:
                reply = await client.post("/agent/tasks", json={"taskId": self.task, "question": "해결해 줘"})
                self.assertEqual(reply.status_code, 503)

    async def test_wrong_card_does_not_consume_another_proposals_approval(self):
        await self.start()
        await self.runner.process(await self.enqueue("DECISION", "UNKNOWN"))
        self.assertEqual(self.writes, [])
        await self.runner.process(await self.enqueue("DECISION", "B"))
        self.assertEqual(self.changed, {"B"})


if __name__ == "__main__":
    unittest.main()
