"""AI HTTP receipt. Persist commands before 202; PostgreSQL workers run graphs.

Only agent-owned tables are written here. Backend business APIs, RabbitMQ and
approval contracts stay unchanged. Human approval waiting has no timeout.
"""
import uuid
import os
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
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
checkpoints = None   # checkpointer 연결 풀


@asynccontextmanager
async def lifespan(app: FastAPI):
    global app_graph, store, runner, checkpoints
    if not AGENT_DB_URL:
        # Serving 202 from memory would silently lose accepted decisions on restart.
        yield
        return
    store = ApprovalStore(AGENT_DB_URL)
    await store.open()
    # checkpointer는 연결 하나(from_conn_string)가 아니라 풀로 연다. 연결 하나는 postgres가 재시작되면
    # 끊긴 채 남아 승인 재개가 계속 실패했다. check_connection이 꺼낼 때마다 살아 있는지 보고 새로 붙는다.
    # 연결 옵션은 from_conn_string과 같다 (decisions 6.41)
    checkpoints = AsyncConnectionPool(AGENT_DB_URL, min_size=1, max_size=12, open=False,
                                      check=AsyncConnectionPool.check_connection,
                                      kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    await checkpoints.open()
    try:
        saver = AsyncPostgresSaver(conn=checkpoints)
        await saver.setup()
        app_graph = G.builder.compile(checkpointer=saver)
        G.execution_store = store
        G.app_graph = app_graph
        runner = ApprovalRunner(app_graph, store, initial_state)
        runner.start()
        try:
            yield
        finally:
            await runner.close()
            await O.shutdown()
    finally:
        G.execution_store = None
        G.app_graph = None
        await checkpoints.close()
        await store.close()


app = FastAPI(lifespan=lifespan)


class TaskIn(BaseModel):
    taskId: str
    threadId: str | None = None
    userId: str | None = None
    role: str | None = None
    question: str = ""
    whatif: list[dict] | None = None
    parentThreadId: str | None = None   # 이어서 묻는 질문이면 앞 작업의 thread (decisions 6.44)


class ResumeIn(BaseModel):
    threadId: str | None = None
    approvalId: str
    decision: Literal["APPROVED", "REJECTED", "WITHDRAWN"]   # WITHDRAWN = 요청자가 보내지 않음 (decisions 6.33)
    reason: str | None = None


class BatchIn(BaseModel):
    requestId: str
    expectedRevision: int
    action: Literal["CONFIRM", "UNCONFIRM", "NEEDS_EDIT", "REPLAN", "MANUAL", "EXCLUDE", "EXECUTE", "CANCEL"]
    approvalId: str | None = None
    reason: str = ""
    args: dict | None = None
    actorId: str


def initial_state(command):
    body = TaskIn(**command["payload"])
    thread_id = body.threadId or body.taskId
    trace_id = O.current().trace_id if O.current() else str(uuid.uuid4())
    graph_input = {
        "messages": [HumanMessage(body.question)],
        "task_id": body.taskId, "thread_id": thread_id, "trace_id": trace_id,
        "user_id": body.userId, "role": body.role, "question": body.question,
        "whatif_source": "backend" if body.whatif is not None else "agent",
        "parent_thread_id": body.parentThreadId,
        # 이번 실행의 값은 비우고 시작한다 (같은 thread의 이전 실행 값이 섞이지 않게).
        # whatif_ops는 backend가 줄 때만 넣는다. 이어 묻기는 같은 thread가 아니라 parent_thread_id로 앞 작업을 읽는다.
        "turn_count": 0, "tool_counts": {}, "impact": None, "targets": [], "options": [], "recommended": None,
        "tried": [], "selected_option": None, "pick_reason": None, "verify": None, "verify_log": [],
        "verify_count": 0, "reject_count": 0, "rejections": [], "decision": None, "executed": None,
        "evidence": [], "clarify": None, "notice": None, "inherited": None, "failure": None, "answer": None,
        "current_impact": None, "current_ops": [], "scope": [], "active_target": None,
        "blocked": {}, "executions": [], "search_hits": [], "qa_searched": False, "evidence_error": None,
        "next_actions": [], "result": None,
        "proposals": [], "active_proposal": None, "replan_target": None, "replan_constraint": None,
        "processed_commands": [command["command_id"]], "workflow_version": 3,
        "batch_revision": 0, "batch_status": "REVIEW", "manager_reviews": {},
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
    if snapshot.values and snapshot.values.get("workflow_version") not in (2, 3):
        raise HTTPException(409, "이전 버전의 승인 대기 작업입니다. 기존 버전에서 마무리한 뒤 전환하세요.")
    # Accept even while registration/execution is running: the inbox drains in order.
    try:
        fresh = await store.receive("decision:" + body.approvalId, task_id, thread, "DECISION", body.model_dump())
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"accepted": True, "duplicate": not fresh}


@app.post("/agent/tasks/{task_id}/batch", status_code=202)
async def review_batch(task_id: str, body: BatchIn):
    require_store()
    key = "batch:" + task_id + ":" + body.requestId
    try:
        fresh = await store.receive(key, task_id, task_id, "BATCH", body.model_dump())
        if not fresh:
            await store.query("UPDATE agent.workflow_command SET status='PENDING', error=NULL WHERE command_id=%s AND kind='BATCH' AND status='HELD'", (key,))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"accepted": True, "duplicate": not fresh}


@app.get("/health")
async def health(response: Response):
    # docker healthcheck가 본다. 그래프·checkpointer가 준비되고 agent DB에 붙을 때만 healthy다 —
    # 그래야 받은 결정을 잃지 않는다. 아니면 503. LLM·MCP는 보지 않는다 (decisions 6.41)
    ok = app_graph is not None and store is not None
    if ok:
        try:
            for pool in (store.pool, checkpoints):   # 접수함과 checkpointer 둘 다 붙어야 한다
                async with pool.connection(timeout=1.5) as c:
                    await c.execute("select 1")
        except Exception:
            ok = False
    if not ok:
        response.status_code = 503
    return {"ok": ok,
            "checkpointer": "postgres" if store else "unavailable", "workflow_version": 3,
            "model": "openai" if G.llm is not None else "fake",
            "tracingConfigured": O.client() is not None, "promptVersion": O.prompt_version()}
