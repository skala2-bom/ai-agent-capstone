"""Resume one graph thread at a time from the durable inbox.

Approval HTTP registration cannot be made exactly-once with the current backend
(deduplication only covers PENDING). Ambiguous registrations/executions are held
for inspection, never blindly sent again. Other mapped cards can still proceed.
"""
import asyncio
import hashlib
import json
import logging
import os

from langgraph.types import Command

import graph as G
import observability as O

log = logging.getLogger(__name__)
WORK_TIMEOUT = int(os.getenv("AGENT_WORK_TIMEOUT_S", "600"))
RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "600"))


def config(thread_id):
    return O.graph_config(thread_id, RECURSION_LIMIT)


def interrupts(snapshot):
    return [i for t in snapshot.tasks for i in (t.interrupts or ())]


class ApprovalRunner:
    def __init__(self, graph, store, initial_state):
        self.graph, self.store, self.initial_state = graph, store, initial_state
        self.active = {}
        self.loops = []

    def start(self):
        self.loops = [asyncio.create_task(self.dispatch()), asyncio.create_task(self.deliver_events()),
                      asyncio.create_task(self.recover_reviews())]

    async def close(self):
        tasks = [*self.loops, *self.active.values()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def dispatch(self):
        while True:
            try:
                for row in await self.store.threads():
                    thread = row["thread_id"]
                    if thread not in self.active and len(self.active) < 3:
                        self.active[thread] = asyncio.create_task(self.process_thread(thread))
            except Exception:
                log.exception("approval inbox scan failed; retrying")
            await asyncio.sleep(0.3)

    async def process_thread(self, thread):
        try:
            async with self.store.thread_lock(thread) as locked:
                if not locked:
                    return
                for _ in range(40):
                    command = await self.store.next_command(thread)
                    if command is None:
                        break
                    await self.process_observed(command)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Infrastructure failure before durable completion: retain PENDING for recovery.
            log.exception("approval worker unavailable; command retained")
        finally:
            self.active.pop(thread, None)

    async def process_observed(self, command):
        previous = await self.store.previous_trace(command["task_id"])
        async with O.execution(command["task_id"], command["thread_id"], command["command_id"],
                               command["kind"], previous) as trace:
            await self.store.event(command["task_id"], {"type": "TRACE", **trace.summary(), "outcome": "RUNNING"})
            try:
                await asyncio.wait_for(self.process(command), WORK_TIMEOUT)
                snapshot = await self.graph.aget_state(config(command["thread_id"]))
                proposals = snapshot.values.get("proposals") or []
                if any(p["status"] in {"REGISTRATION_UNKNOWN", "EXECUTION_UNKNOWN"} for p in proposals):
                    trace.outcome = "HELD"
                elif interrupts(snapshot):
                    trace.outcome = "WAITING_APPROVAL"
                elif any(p["status"] == "BLOCKED" for p in proposals):
                    trace.outcome = "PARTIAL"
            except asyncio.CancelledError:
                trace.outcome = "INTERRUPTED"
                raise
            except Exception as e:
                trace.outcome = "ERROR"
                O.note("command_error", errorCode=type(e).__name__)
                snapshot = await self.graph.aget_state(config(command["thread_id"]))
                if snapshot.values.get("proposals"):
                    trace.outcome = "HELD"
                    await self.store.event(command["task_id"], {"type": "WORKFLOW_NOTICE",
                        "message": "처리가 중단되었습니다. 저장된 상태와 실행 이력을 확인한 뒤 복구가 필요합니다.",
                        "errorCode": type(e).__name__})
                    await self.store.finish_command(command["command_id"], "HELD", type(e).__name__)
                else:
                    await self.store.event(command["task_id"], {"type": "FAILED", "errorCode": type(e).__name__,
                        "message": "작업 준비에 실패했습니다. " + str(e)[:300]})
                    await self.store.finish_command(command["command_id"], "ERROR", type(e).__name__)
            finally:
                await self.store.event(command["task_id"], {"type": "TRACE", **trace.summary()})
                trace.finishing = True
                if trace.terminal_event:
                    await self.store.event(*trace.terminal_event)
                if trace.command_completion:
                    await self.store.finish_command(*trace.command_completion)

    async def register(self, task_id, proposal):
        payload = {"type": "APPROVAL_REQUIRED", **G.approval_payload(proposal)}
        row, fresh = await self.store.begin_registration(proposal["proposal_id"], task_id, payload)
        if not fresh:
            if row["status"] == "REGISTERED":
                return {"status": "PENDING", "approval_id": row["approval_id"]}
            if row["status"] == "REFUSED":
                return {"status": "BLOCKED", "reason": row["error"]}
            # SENDING includes a process crash after sending and before saving the reply.
            await self.store.bind(proposal["proposal_id"], "UNKNOWN", error="등록 응답 확인 필요")
            return {"status": "REGISTRATION_UNKNOWN", "reason": "승인 카드 등록 여부를 확정할 수 없습니다. 중복 등록을 막기 위해 보류했습니다."}
        response = await G.emit(task_id, payload)  # exactly one HTTP attempt (NO important=True)
        approval_id = None
        if response is not None and response.is_success:
            try:
                body = response.json()
                approval_id = body.get("approvalId") if isinstance(body, dict) else None
            except ValueError:
                pass
        if isinstance(approval_id, str) and approval_id:
            await self.store.bind(proposal["proposal_id"], "REGISTERED", approval_id)
            return {"status": "PENDING", "approval_id": approval_id}
        if response is not None and response.status_code in (400, 401, 403, 404, 422):
            reason = f"승인 카드 등록 거부 (HTTP {response.status_code}). 권한·발주 상태를 확인해 주세요."
            await self.store.bind(proposal["proposal_id"], "REFUSED", error=reason)
            return {"status": "BLOCKED", "reason": reason}
        await self.store.bind(proposal["proposal_id"], "UNKNOWN", error="등록 응답 확인 필요")
        return {"status": "REGISTRATION_UNKNOWN", "reason": "승인 카드 등록 응답을 확인하지 못했습니다. 중복 등록하지 않고 보류합니다."}

    async def settle(self, task_id, thread_id):
        """Drain registration interruptions, then stop at the shared decision wait."""
        cfg = config(thread_id)
        while True:
            snapshot = await self.graph.aget_state(cfg)
            pending = interrupts(snapshot)
            if pending and pending[0].value.get("stage") == "register":
                bindings = {}
                for proposal in pending[0].value["proposals"]:
                    bindings[proposal["proposal_id"]] = await self.register(task_id, proposal)
                await self.graph.ainvoke(Command(resume={pending[0].id: bindings}), cfg)
                continue
            await self.progress(task_id, snapshot.values)
            await self.review_ready(task_id, snapshot)
            if not pending and not snapshot.next:
                v = snapshot.values
                await self.store.event(task_id, {"type": "DONE", "status": "DONE", "answer": v.get("answer"),
                    "result": G.mask(v.get("result"), v.get("role")), "sources": v.get("evidence") or [],
                    "next_actions": v.get("next_actions") or []})
            return snapshot

    async def review_ready(self, task_id, snapshot):
        pending = interrupts(snapshot)
        proposals = snapshot.values.get("proposals") or []
        # Only a complete registration round at the common decision wait is reviewable.
        if not pending or pending[0].value.get("stage") != "decisions" or not proposals:
            return
        if any(p["status"] in {"PREPARED", "REGISTRATION_UNKNOWN"} for p in proposals):
            return
        # The full registered lineage keeps this ID stable across manager decisions.
        ids = sorted({p["approval_id"] for p in proposals if p.get("approval_id")})
        if ids:
            review_id = hashlib.sha256(json.dumps([task_id, ids]).encode()).hexdigest()
            await self.store.event(task_id, {"type": "TASK_REVIEW_READY", "reviewId": review_id, "approvalIds": ids})

    async def recover_review_readiness(self):
        # Upgrade compatibility: existing idle DRAFT tasks have no new inbox command.
        # Read their saved decision wait; never resume the graph or perform a tool call here.
        rows = await self.store.query("""SELECT c.task_id, c.thread_id FROM agent.workflow_command c
            WHERE c.kind='START' AND c.status='DONE'
              AND EXISTS (SELECT 1 FROM agent.approval_binding b WHERE b.task_id=c.task_id AND b.status='REGISTERED')
              AND NOT EXISTS (SELECT 1 FROM agent.workflow_outbox e WHERE e.task_id=c.task_id
                  AND e.payload->>'type' IN ('TASK_REVIEW_READY', 'DONE', 'FAILED'))""")
        for row in rows:
            async with self.store.thread_lock(row["thread_id"]) as locked:
                if locked:
                    snapshot = await self.graph.aget_state(config(row["thread_id"]))
                    await self.review_ready(row["task_id"], snapshot)

    async def recover_reproposals(self):
        # Old registered reproposals may still be DRAFT in the backend. Use their known
        # approval IDs, never create an unbound replacement or replay an uncertain registration.
        rows = await self.store.query("""SELECT DISTINCT c.task_id, c.thread_id FROM agent.workflow_command c
            JOIN agent.approval_binding b ON b.task_id=c.task_id
            WHERE c.kind='START' AND c.status='DONE' AND b.status='REGISTERED'
              AND b.payload->>'replaces' IS NOT NULL AND b.payload->>'previousApprovalId' IS NULL
              AND NOT EXISTS (SELECT 1 FROM agent.workflow_outbox e WHERE e.task_id=c.task_id
                  AND e.payload->>'type' IN ('DONE', 'FAILED'))""")
        for row in rows:
            async with self.store.thread_lock(row["thread_id"]) as locked:
                if not locked:
                    continue
                snapshot = await self.graph.aget_state(config(row["thread_id"]))
                for p in snapshot.values.get("proposals") or []:
                    if p["status"] != "PENDING" or not p.get("previous_approval_id") or not p.get("approval_id"):
                        continue
                    binding = (await self.store.query("SELECT * FROM agent.approval_binding WHERE proposal_id=%s", (p["proposal_id"],)))[0]
                    if binding["status"] != "REGISTERED" or binding["payload"].get("previousApprovalId"):
                        continue
                    payload = {"type": "APPROVAL_REQUIRED", **G.approval_payload(p), "approvalId": p["approval_id"]}
                    response = await G.emit(row["task_id"], payload)
                    if response is not None and response.is_success and response.json().get("approvalId") == p["approval_id"]:
                        await self.store.confirm_reproposal_route(p["proposal_id"], payload)

    async def recover_reviews(self):
        while True:
            try:
                await self.recover_reproposals()
                await self.recover_review_readiness()
            except Exception:
                log.exception("buyer review readiness recovery failed; retrying")
            await asyncio.sleep(30)

    async def progress(self, task_id, state):
        for p in state.get("proposals") or []:
            await self.store.event(task_id, G.proposal_event(p, state))

    async def process(self, command):
        task_id, thread = command["task_id"], command["thread_id"]
        cfg = config(thread)
        snapshot = await self.graph.aget_state(cfg)
        v = snapshot.values
        consumed = command["command_id"] in v.get("processed_commands", [])
        if consumed:
            if snapshot.next and not interrupts(snapshot):
                await self.graph.ainvoke(None, cfg)
        elif command["kind"] == "START":
            if snapshot.next:
                raise ValueError("이 thread에는 아직 완료되지 않은 작업이 있습니다.")
            await self.graph.ainvoke(self.initial_state(command), cfg)
        else:
            if v.get("task_id") != task_id:
                await self.store.finish_command(command["command_id"], "UNMATCHED", "작업과 thread가 일치하지 않습니다.")
                return
            # A preceding command may have checkpointed execution before a crash.
            if snapshot.next and not interrupts(snapshot):
                await self.graph.ainvoke(None, cfg)
            snapshot = await self.settle(task_id, thread)
            v = snapshot.values
            approval_id = command["payload"]["approvalId"]
            proposal = next((p for p in v.get("proposals", []) if p.get("approval_id") == approval_id), None)
            if v.get("task_id") != task_id or not proposal or proposal["status"] != "PENDING":
                await self.store.finish_command(command["command_id"], "UNMATCHED", "승인 카드와 대기 상태 연결을 확인해야 합니다.")
                await self.store.event(task_id, {"type": "WORKFLOW_NOTICE", "approvalId": approval_id,
                    "message": "접수한 결정을 대기 중인 제안에 연결하지 못했습니다. 승인 이력을 확인해 주세요."})
                return
            gate = interrupts(snapshot)[0]
            await self.graph.ainvoke(Command(resume={gate.id: {**command["payload"], "command_id": command["command_id"]}}), cfg)
        await self.settle(task_id, thread)
        await self.store.finish_command(command["command_id"])

    async def deliver_events(self):
        while True:
            try:
                # A separate session lock prevents multiple processes reordering an outbox stream.
                async with self.store.thread_lock("__outbox__") as locked:
                    if locked:
                        for row in await self.store.pending_events():
                            payload = {**row["payload"], "eventId": row["event_key"], "version": row["id"]}
                            response = await G.emit(row["task_id"], payload)
                            if response is not None and response.is_success:
                                await self.store.delivered(row["id"])
            except Exception:
                log.exception("approval progress delivery failed; retrying")
            await asyncio.sleep(0.2)
