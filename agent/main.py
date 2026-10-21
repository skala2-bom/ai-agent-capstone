"""AI HTTP receipt. Persist commands before 202; PostgreSQL workers run graphs.

Only agent-owned tables are written here. Backend business APIs, RabbitMQ and
approval contracts stay unchanged. Human approval waiting has no timeout.
"""
import uuid
import os
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel

import graph as G
from approval_store import ApprovalStore
from approval_runner import ApprovalRunner, config
import observability as O

logging.basicConfig(level=logging.INFO, format="%(message)s")

AGENT_DB_URL = os.getenv("AGENT_DB_URL")
app_graph = None
store = None
runner = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_graph, store, runner
    if not AGENT_DB_URL:
        # Serving 202 from memory would silently lose accepted decisions on restart.
        yield
        return
    store = ApprovalStore(AGENT_DB_URL)
    await store.open()
    try:
        async with AsyncPostgresSaver.from_conn_string(AGENT_DB_URL) as saver:
            await saver.setup()
            app_graph = G.builder.compile(checkpointer=saver)
            G.execution_store = store
            runner = ApprovalRunner(app_graph, store, initial_state)
            runner.start()
            try:
                yield
            finally:
                await runner.close()
                await O.shutdown()
    finally:
        G.execution_store = None
        await store.close()


app = FastAPI(lifespan=lifespan)


class TaskIn(BaseModel):
    taskId: str
    threadId: str | None = None
    userId: str | None = None
    role: str | None = None
    question: str = ""
    whatif: list[dict] | None = None


class ResumeIn(BaseModel):
    threadId: str | None = None
    approvalId: str
    decision: Literal["APPROVED", "REJECTED", "WITHDRAWN"]   # WITHDRAWN = 요청자가 보내지 않음 (decisions 6.33)
    reason: str | None = None


def initial_state(command):
    body = TaskIn(**command["payload"])
    thread_id = body.threadId or body.taskId
    trace_id = O.current().trace_id if O.current() else str(uuid.uuid4())
    graph_input = {
        "messages": [HumanMessage(body.question)],
        "task_id": body.taskId, "thread_id": thread_id, "trace_id": trace_id,
        "user_id": body.userId, "role": body.role, "question": body.question,
        "whatif_source": "backend" if body.whatif is not None else "agent",
        # 이번 실행의 값은 비우고 시작한다 (같은 thread의 이전 실행 값이 섞이지 않게).
        # whatif_ops는 backend가 줄 때만 넣는다 — 안 주면 이전 대화의 변경을 이어 쓸 수 있게 둔다.
        "turn_count": 0, "tool_counts": {}, "impact": None, "targets": [], "options": [], "recommended": None,
        "tried": [], "selected_option": None, "pick_reason": None, "verify": None, "verify_log": [],
        "verify_count": 0, "reject_count": 0, "rejections": [], "decision": None, "executed": None,
        "evidence": [], "clarify": None, "notice": None, "failure": None, "answer": None,
        "current_impact": None, "current_ops": [], "scope": [], "active_target": None,
        "blocked": {}, "executions": [], "search_hits": [], "qa_searched": False, "evidence_error": None,
        "next_actions": [], "result": None,
        "proposals": [], "active_proposal": None, "replan_target": None,
        "processed_commands": [command["command_id"]], "workflow_version": 2,
    }
    if body.whatif is not None:
        graph_input["whatif_ops"] = body.whatif
    return graph_input


def require_store():
    if store is None:
        raise HTTPException(503, "AGENT_DB_URL이 필요합니다. 승인 결정을 영구 저장한 뒤 접수합니다.")


@app.post("/agent/tasks", status_code=202)
async def create_task(body: TaskIn):
    require_store()
    try:
        fresh = await store.receive("start:" + body.taskId, body.taskId, body.threadId or body.taskId,
                                    "START", body.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"accepted": True, "duplicate": not fresh}


@app.post("/agent/tasks/{task_id}/resume", status_code=202)
async def resume(task_id: str, body: ResumeIn):
    require_store()
    if body.decision == "REJECTED" and not (body.reason or "").strip():
        raise HTTPException(422, "거절 사유가 필요합니다.")
    thread = body.threadId or task_id
    snapshot = await app_graph.aget_state(config(thread))
    if snapshot.values and snapshot.values.get("workflow_version") != 2:
        raise HTTPException(409, "이전 버전의 승인 대기 작업입니다. 기존 버전에서 마무리한 뒤 전환하세요.")
    # Accept even while registration/execution is running: the inbox drains in order.
    try:
        fresh = await store.receive("decision:" + body.approvalId, task_id, thread, "DECISION", body.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"accepted": True, "duplicate": not fresh}


@app.get("/health")
async def health():
    return {"ok": app_graph is not None and store is not None,
            "checkpointer": "postgres" if store else "unavailable", "workflow_version": 2,
            "model": "openai" if G.llm is not None else "fake",
            "tracingConfigured": O.client() is not None, "promptVersion": O.prompt_version()}
