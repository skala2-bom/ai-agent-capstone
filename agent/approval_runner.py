"""Resume one graph thread at a time from the durable inbox.

Approval HTTP registration cannot be made exactly-once with the current backend
(deduplication only covers PENDING). Ambiguous registrations/executions are held
for inspection, never blindly sent again. Other mapped cards can still proceed.
"""
import asyncio
import logging
import os

from langgraph.types import Command

import graph as G

log = logging.getLogger(__name__)
WORK_TIMEOUT = int(os.getenv("AGENT_WORK_TIMEOUT_S", "600"))
RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "600"))


def config(thread_id):
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def interrupts(snapshot):
    return [i for t in snapshot.tasks for i in (t.interrupts or ())]


class ApprovalRunner:
    def __init__(self, graph, store, initial_state):
        self.graph, self.store, self.initial_state = graph, store, initial_state
        self.active = {}
        self.loops = []

    def start(self):
        self.loops = [asyncio.create_task(self.dispatch()), asyncio.create_task(self.deliver_events())]

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
                    try:
                        await asyncio.wait_for(self.process(command), WORK_TIMEOUT)
                    except asyncio.CancelledError:
                        raise  # leave inbox PENDING; restart resumes the saved graph
                    except Exception as e:
                        log.exception("approval workflow command failed")
                        snapshot = await self.graph.aget_state(config(thread))
                        if snapshot.values.get("proposals"):
                            # A timeout must not terminal-fail other cards or replay an uncertain write.
                            await self.store.event(command["task_id"], {"type": "WORKFLOW_NOTICE",
                                "message": "처리가 중단되었습니다. 저장된 상태와 실행 이력을 확인한 뒤 복구가 필요합니다.",
                                "errorCode": type(e).__name__})
                            await self.store.finish_command(command["command_id"], "HELD", type(e).__name__)
                        else:
                            await self.store.event(command["task_id"], {"type": "FAILED", "errorCode": type(e).__name__,
                                "message": "작업 준비에 실패했습니다. " + str(e)[:300]})
                            await self.store.finish_command(command["command_id"], "ERROR", type(e).__name__)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Infrastructure failure before durable completion: retain PENDING for recovery.
            log.exception("approval worker unavailable; command retained")
        finally:
            self.active.pop(thread, None)

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
            if not pending and not snapshot.next:
                v = snapshot.values
                await self.store.event(task_id, {"type": "DONE", "status": "DONE", "answer": v.get("answer"),
                    "result": G.mask(v.get("result"), v.get("role")), "sources": v.get("evidence") or [],
                    "next_actions": v.get("next_actions") or []})
            return snapshot

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
