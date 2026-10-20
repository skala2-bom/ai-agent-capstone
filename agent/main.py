"""agent — AI Serving 계층 (아키텍처 3장, 5장 · contracts 2.6·2.7).

이 파일이 맡는 것
  - API 두 개: 작업 실행(POST /agent/tasks), 승인 후 재개(POST /agent/tasks/{id}/resume)
  - checkpointer 연결: AGENT_DB_URL이 있으면 PostgreSQL(agent 스키마), 없으면 메모리
  - Harness 바깥 틀: 작업 120초 제한, 끝 이벤트(DONE·FAILED·APPROVAL_REQUIRED) 보내기

그래프(노드·분기·반복)는 graph.py, 가정 변경 형식·verify 판정은 whatif.py에 있다.

금지 (교재 5장)
  - 업무 DB 직접 쓰기. 결과는 전부 backend로 이벤트를 보내 저장하게 한다.
    agent가 쓰는 DB는 checkpointer용 agent 스키마뿐이다 (db/init.sql 계정 분리).

설계 배경: docs/walking-skeleton.md · 결정 근거: docs/decisions.md
"""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

import graph as G

AGENT_DB_URL = os.getenv("AGENT_DB_URL")    # 예: postgresql://agent:agent@postgres:5432/bom
TASK_TIMEOUT_S = 120                         # 작업 한 번 실행 (아키텍처 4.2, 승인 대기 시간 제외)
RECURSION_LIMIT = 60                         # 그래프 단계 수 안전장치. 모델 호출 한도(8회)는 graph.py가 따로 센다

app_graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """checkpointer를 붙여 그래프를 만든다.

    PostgreSQL에 저장하면 승인을 기다리는 동안 agent가 재시작돼도 작업이 사라지지 않는다.
    테이블은 처음 실행할 때 LangGraph가 agent 스키마에 직접 만든다 (agent 계정 search_path = agent).
    """
    global app_graph
    if AGENT_DB_URL:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(AGENT_DB_URL) as saver:
            await saver.setup()
            app_graph = G.builder.compile(checkpointer=saver)
            yield
    else:
        app_graph = G.builder.compile(checkpointer=InMemorySaver())
        yield


app = FastAPI(lifespan=lifespan)


class TaskIn(BaseModel):
    """contracts 2.6. backend가 아직 userId·role·whatif를 보내지 않아도 받을 수 있게 전부 선택값이다."""
    taskId: str
    threadId: str | None = None
    userId: str | None = None
    role: str | None = None
    question: str = ""
    whatif: list[dict] | None = None   # null이면 agent가 질문에서 뽑는다. 자동 작업이면 backend가 채운다


class ResumeIn(BaseModel):
    """contracts 2.7."""
    threadId: str | None = None
    approvalId: str
    decision: Literal["APPROVED", "REJECTED"]
    reason: str | None = None


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


async def run(task_id: str, thread_id: str, graph_input, role: str | None, trace_id: str) -> None:
    """그래프를 한 번 돌리고, 멈춘 이유에 맞는 끝 이벤트를 보낸다.

    - 승인 대기로 멈춤 → APPROVAL_REQUIRED (approval_gate가 interrupt로 넘긴 값)
    - 끝까지 감 → DONE
    - 시간 초과·한도 초과·예외 → FAILED. 여기서 막지 않으면 화면이 영원히 기다린다
    """
    config = _config(thread_id)
    try:
        await asyncio.wait_for(app_graph.ainvoke(graph_input, config), TASK_TIMEOUT_S)
    except asyncio.TimeoutError:
        await G.emit(task_id, {"type": "FAILED", "errorCode": "TIMEOUT", "traceId": trace_id,
                               "message": f"작업이 {TASK_TIMEOUT_S}초 안에 끝나지 않았습니다."}, important=True)
        return
    except G.ToolUnavailable as e:
        await G.emit(task_id, {"type": "FAILED", "errorCode": "TOOL_UNAVAILABLE", "traceId": trace_id,
                               "message": str(e)}, important=True)
        return
    except G.LimitExceeded as e:
        await G.emit(task_id, {"type": "FAILED", "errorCode": "LOOP_LIMIT", "traceId": trace_id,
                               "message": str(e)}, important=True)
        return
    except Exception as e:
        await G.emit(task_id, {"type": "FAILED", "errorCode": type(e).__name__, "traceId": trace_id,
                               "message": str(e)}, important=True)
        return

    snapshot = await app_graph.aget_state(config)
    pending = [i for t in snapshot.tasks for i in (t.interrupts or ())]
    if pending:
        await G.emit(task_id, {"type": "APPROVAL_REQUIRED", **pending[0].value}, important=True)
        return
    v = snapshot.values
    await G.emit(task_id, {"type": "DONE", "status": "DONE", "answer": v.get("answer"),
                           "result": G.mask(v.get("result"), role), "sources": v.get("evidence") or [],
                           "next_actions": v.get("next_actions") or []}, important=True)


@app.post("/agent/tasks", status_code=202)
async def create_task(body: TaskIn, background: BackgroundTasks):
    """② backend → agent 작업 실행 (contracts 2.6). 바로 202를 돌려주고 뒤에서 돌린다.

    같은 threadId로 다시 부르면 앞 대화의 State를 이어받는다. 예: 영향 분석 뒤
    "해결 방법 찾아 줘"라고 하면 앞에서 뽑은 가정 변경을 그대로 쓴다.
    """
    thread_id = body.threadId or body.taskId
    trace_id = uuid.uuid4().hex
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
        "next_actions": [], "result": None,
    }
    if body.whatif is not None:
        graph_input["whatif_ops"] = body.whatif
    background.add_task(run, body.taskId, thread_id, graph_input, body.role, trace_id)
    return {"accepted": True}


@app.post("/agent/tasks/{task_id}/resume", status_code=202)
async def resume(task_id: str, body: ResumeIn, background: BackgroundTasks):
    """승인·거절 결과로 멈춘 자리부터 이어 간다 (contracts 2.7). 같은 threadId의 checkpointer 상태를 쓴다."""
    thread_id = body.threadId or task_id
    snapshot = await app_graph.aget_state(_config(thread_id))
    if not any(t.interrupts for t in snapshot.tasks):
        raise HTTPException(409, "승인을 기다리는 작업이 아닙니다.")
    if body.decision == "REJECTED" and not (body.reason or "").strip():
        raise HTTPException(422, "거절 사유가 필요합니다 (contracts 3.3).")
    v = snapshot.values
    background.add_task(run, task_id, thread_id, Command(resume=body.model_dump()),
                        v.get("role"), v.get("trace_id") or uuid.uuid4().hex)
    return {"accepted": True}


@app.get("/health")
async def health():
    return {"ok": app_graph is not None, "checkpointer": "postgres" if AGENT_DB_URL else "memory",
            "model": "openai" if G.llm is not None else "fake"}
