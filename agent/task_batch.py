"""Task-wide manager review. Checkpoints hold reviews; backend commits business changes atomically."""
import copy
from datetime import datetime, timezone
import os
import uuid

import httpx
from langgraph.types import Command

import graph as G
import whatif as W


def enabled(state):
    return state.get("workflow_version") == 3


def latest_proposals(state):
    by_target = {}
    for p in state.get("proposals") or []:
        by_target[p["target"]] = p
    return list(by_target.values())


def view(state):
    reviews = state.get("manager_reviews") or {}
    entries = []
    for p in latest_proposals(state):
        status = reviews.get(p["proposal_id"], {}).get("status", "UNREVIEWED")
        if p["status"] == "REJECTED":
            status = "EXCLUDED" if p.get("outcome") else "REPLANNING"
        elif p["status"] in {"REGISTRATION_UNKNOWN", "EXECUTION_UNKNOWN", "PREPARED"}:
            status = "UNAVAILABLE"
        elif p["status"] == "EXECUTED":
            status = "EXECUTED"
        elif p["status"] == "WITHDRAWN":
            status = "CANCELLED" if state.get("batch_status") == "CANCELLED" else "EXCLUDED"
        entries.append({"approvalId": p.get("approval_id"), "proposalId": p["proposal_id"],
                        "revision": p["revision"], "target": p["target"], "itemId": p["option"]["item_id"],
                        "poId": p["option"]["args"].get("po_id"), "summary": p["option"].get("summary", ""),
                        "source": p.get("source", "AI"), "reviewStatus": status, "outcome": p.get("outcome"),
                        "reviewedBy": reviews.get(p["proposal_id"], {}).get("actorId")})
    status = state.get("batch_status", "REVIEW")
    return {"type": "TASK_BATCH_STATE", "mode": "BATCH", "revision": state.get("batch_revision", 0),
            "status": status, "entries": entries, "error": state.get("batch_error"),
            "lastRequestId": state.get("batch_last_request"), "lastCommand": state.get("batch_last_command"),
            "canExecute": bool(entries) and status in {"REVIEW", "BLOCKED"}
                and all(e["reviewStatus"] in {"CONFIRMED", "EXCLUDED"} for e in entries)}


async def backend(task_id, path, body=None):
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request("GET" if body is None else "POST",
            f"{G.BACKEND_URL}/internal/tasks/{task_id}/batch/{path}", json=body,
            headers={"X-Service-Token": os.getenv("SERVICE_TOKEN", "")})
        response.raise_for_status()
        return response.json()


async def publish(runner, state, *, immediate=False):
    snapshot = await runner.graph.aget_state({"configurable": {"thread_id": state["thread_id"]}})
    event = view(snapshot.values)
    # Durable outbox deliveries can arrive after the synchronous pre-execution event.
    # Checkpoint steps preserve state order independently of HTTP arrival order.
    event["stateVersion"] = (snapshot.metadata or {}).get("step", 0)
    if immediate:
        response = await G.emit(state["task_id"], event, important=True)
        if response is None or not response.is_success:
            raise RuntimeError("task 검토 상태를 저장하지 못했습니다.")
    else:
        await runner.store.event(state["task_id"], event)


async def initialize(runner, snapshot, cfg):
    state = snapshot.values
    if not enabled(state) or state.get("batch_context"):
        return False
    data = await backend(state["task_id"], "snapshot")
    context = {"type": "TASK_BATCH_CONTEXT", "workflowVersion": 3, "fingerprint": data["fingerprint"],
               "scope": state.get("scope") or [], "whatif": state.get("current_ops") or []}
    response = await G.emit(state["task_id"], context, important=True)
    if response is None or not response.is_success:
        raise RuntimeError("task 실행 기준을 저장하지 못했습니다.")
    await runner.graph.aupdate_state(cfg, {"batch_context": context}, as_node="check_batch")
    await runner.graph.ainvoke(None, cfg)
    return True


async def sync_coordination_terminal(runner, snapshot, cfg):
    state = snapshot.values
    if not state.get("coordination") or state.get("batch_status") in {"COMPLETED", "CANCELLED", "REJECTED"}:
        return False
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{G.BACKEND_URL}/internal/tasks/{state['task_id']}/coordination",
            headers={"X-Service-Token": os.getenv("SERVICE_TOKEN", "")})
        response.raise_for_status()
        context = response.json()
    if context.get("status") not in {"REJECTED", "CANCELLED", "COMPLETED"}:
        return False
    result = context.get("result") or {"status": context["status"], "executions": [], "reason": context.get("reason")}
    pending = [i for t in snapshot.tasks for i in t.interrupts]
    if not pending:
        return False
    gate = pending[0]
    if gate.value.get("stage") == "decisions":
        await runner.graph.ainvoke(Command(resume={gate.id: {"decision": "BATCH_DONE", "result": result,
            "command_id": "coordination-terminal:" + state["task_id"]}}), cfg)
    elif gate.value.get("stage") == "register":
        await runner.graph.aupdate_state(cfg, {"batch_status": context["status"], "notice": "부서 요청이 종료되었습니다: " + str(context.get("reason") or "")})
        await runner.graph.ainvoke(Command(resume={gate.id: {p["proposal_id"]: {"status": "WITHDRAWN", "reason": "부서 요청 종료"}
            for p in gate.value["proposals"]}}), cfg)
    else:
        return False
    return True


def manual_proposal(state, parent, command):
    args = command.get("args") or {}
    if set(args) != {"po_id", "new_qty", "new_due_date"} or args["po_id"] != parent["option"]["args"].get("po_id"):
        raise ValueError("해당 발주의 수량과 입고일만 수정할 수 있습니다.")
    qty = args["new_qty"]
    if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= 2147483647:
        raise ValueError("수량은 1 이상의 정수여야 합니다.")
    errors = W.validate_ops([{"op": "change_po_qty", "po_id": args["po_id"], "new_qty": qty},
        {"op": "request_due_date_change", "po_id": args["po_id"], "new_due_date": args["new_due_date"]}], state.get("plans") or [])
    if errors:
        raise ValueError(" / ".join(errors))
    revision = parent["revision"] + 1
    pid = uuid.uuid5(uuid.NAMESPACE_URL, state["task_id"] + ":manual:" + command["requestId"]).hex
    opt = {**parent["option"], "option_id": "MANUAL-" + pid, "tool": "update_purchase", "args": args,
           "summary": f"[직접 수정] {args['po_id']} 수량 {qty:,}개 · 입고일 {args['new_due_date']}",
           "added_cost": None, "evidence": parent["option"].get("evidence") or []}
    return {"proposal_id": pid, "revision": revision, "target": parent["target"], "source": "MANUAL",
            "replaces": parent["proposal_id"], "previous_approval_id": parent["approval_id"],
            "option": opt, "targets": parent["targets"], "verify": {"passed": False},
            "status": "PREPARED", "approval_id": None}


async def save(runner, cfg, updates, node="batch_idle"):
    await runner.graph.aupdate_state(cfg, updates, as_node=node)
    await runner.graph.ainvoke(None, cfg)
    return await runner.graph.aget_state(cfg)


async def process(runner, command, cfg):
    snapshot = await runner.settle(command["task_id"], command["thread_id"])
    state, body = snapshot.values, command["payload"]
    key, action = command["command_id"], body["action"]
    if not enabled(state):
        raise ValueError("이전 방식의 작업입니다. 새 task에서 검토해 주세요.")
    if key in state.get("processed_commands", []):
        if snapshot.next and not any(t.interrupts for t in snapshot.tasks):
            await runner.graph.ainvoke(None, cfg)
            await runner.settle(command["task_id"], command["thread_id"])
        await publish(runner, state)
        return
    resuming = state.get("batch_last_request") == body["requestId"] and state.get("batch_status") in {"EXECUTING", "UNKNOWN"}
    if not resuming and (state.get("batch_revision", 0) != body["expectedRevision"]
                         or state.get("batch_status") in {"EXECUTING", "UNKNOWN", "COMPLETED", "CANCELLED", "REJECTED"}):
        # Report the rejected command without changing the accepted review/version.
        await runner.store.event(state["task_id"], {"type": "WORKFLOW_NOTICE", "requestId": body["requestId"],
            "message": "검토 상태가 바뀌어 요청을 반영하지 않았습니다. 최신 상태를 확인하세요."})
        return
    updates = {"batch_revision": state.get("batch_revision", 0) + 1, "batch_last_request": body["requestId"],
               "batch_last_command": body, "batch_error": None, "batch_status": "REVIEW"}
    try:
        if action in {"EXECUTE", "CANCEL"}:
            if not resuming:
                if action == "EXECUTE" and not view(state)["canExecute"]:
                    raise ValueError("미검토 또는 AI 재검토 중인 제안이 있습니다.")
                snapshot = await save(runner, cfg, {**updates, "batch_status": "EXECUTING"})
                state = snapshot.values
            await publish(runner, state, immediate=True)
            try:
                result = await backend(state["task_id"], "complete", {"requestId": body["requestId"]})
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    detail = exc.response.json()
                    raise ValueError(detail.get("message", "전체 실행 검증에 실패했습니다.")) from exc
                raise
            gate = next(i for t in snapshot.tasks for i in t.interrupts)
            try:
                await runner.graph.ainvoke(Command(resume={gate.id: {"decision": "BATCH_DONE", "result": result, "command_id": key}}), cfg)
            except Exception as exc:
                # DB may already have committed; never report this as a rejected execution.
                raise RuntimeError("저장된 실행 결과의 화면 반영을 다시 확인해야 합니다.") from exc
        else:
            parent = next((p for p in latest_proposals(state) if p.get("approval_id") == body.get("approvalId")), None)
            if not parent:
                raise ValueError("새 제안이 생겼습니다. 최신 카드를 확인하세요.")
            if action == "CONFIRM" and parent["status"] != "PENDING":
                raise ValueError("거절된 원안은 승인할 수 없습니다. 직접 수정하거나 새 제안을 검토하세요.")
            reviews = copy.deepcopy(state.get("manager_reviews") or {})
            if action in {"CONFIRM", "UNCONFIRM", "NEEDS_EDIT", "EXCLUDE"}:
                reviews[parent["proposal_id"]] = {"status": {"CONFIRM": "CONFIRMED", "UNCONFIRM": "UNREVIEWED",
                    "NEEDS_EDIT": "EXCLUDED", "EXCLUDE": "EXCLUDED"}[action], "actorId": body["actorId"],
                    "at": datetime.now(timezone.utc).isoformat()}
                await save(runner, cfg, {**updates, "manager_reviews": reviews,
                    "processed_commands": [*state.get("processed_commands", []), key]})
            elif action == "REPLAN":
                if parent["status"] != "PENDING":
                    raise ValueError("이미 거절·검토된 요청입니다. 직접 수정안을 작성해 주세요.")
                await backend(state["task_id"], "reject", {"requestId": body["requestId"]})
                snapshot = await save(runner, cfg, updates)
                gate = next(i for t in snapshot.tasks for i in t.interrupts)
                await runner.graph.ainvoke(Command(resume={gate.id: {"approvalId": parent["approval_id"], "decision": "REJECTED",
                    "reason": body["reason"], "command_id": key}}), cfg)
            elif action == "MANUAL":
                proposal = manual_proposal(state, parent, body)
                await backend(state["task_id"], "reject", {"requestId": body["requestId"]})
                proposals = copy.deepcopy(state["proposals"])
                for p in proposals:
                    if p["proposal_id"] == parent["proposal_id"]:
                        p.update(status="REJECTED", reason=body["reason"], outcome={"code": "MANUAL",
                            "item_name": G.target_name(state, parent["target"]),
                            "message": "직접 작성한 수정안으로 교체했습니다.", "next_action": "새 수정안을 확인해 주세요."})
                proposals.append(proposal)
                await save(runner, cfg, {**updates, "proposals": proposals,
                    "blocked": {k: v for k, v in (state.get("blocked") or {}).items() if k != parent["target"]},
                    "processed_commands": [*state.get("processed_commands", []), key]}, node="check_batch")
    except ValueError as exc:
        # Validation failure is a completed command, not a terminal task failure.
        latest = (await runner.graph.aget_state(cfg)).values
        if latest.get("batch_last_request") == body["requestId"]:
            updates["batch_revision"] = latest.get("batch_revision", updates["batch_revision"])
        await save(runner, cfg, {**updates, "batch_status": "BLOCKED", "batch_error": str(exc),
            "processed_commands": [*latest.get("processed_commands", []), key]})
    except (httpx.HTTPError, RuntimeError):
        if action in {"EXECUTE", "CANCEL"}:
            latest = (await runner.graph.aget_state(cfg)).values
            await save(runner, cfg, {"batch_status": "UNKNOWN",
                "processed_commands": [c for c in latest.get("processed_commands", []) if c != key],
                "batch_error": "실행 응답을 확인하지 못했습니다. 같은 요청으로 결과를 다시 확인해 주세요."})
            await publish(runner, (await runner.graph.aget_state(cfg)).values)
        raise
    await runner.settle(command["task_id"], command["thread_id"])
