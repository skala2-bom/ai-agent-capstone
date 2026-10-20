"""agent — AI Serving 계층 (아키텍처 3장, 5장).

이 서비스가 맡는 것
  - LangGraph StateGraph로 모델을 감싸고, 모델이 고른 도구를 대신 실행한다
  - MCP Client로 business-mcp · rag-mcp에 붙는다 (Streamable HTTP)
  - 도구 실행 전후와 작업이 끝날 때 backend로 진행 이벤트를 보낸다

금지 (교재 5장)
  - DB 직접 쓰기. 결과는 전부 backend로 이벤트를 보내 저장하게 한다

지금 그래프는 `agent ↔ tools` 반복 하나뿐이다. 아키텍처 5장의 실제 그래프
(classify_intent → agent_impact/agent_adjust/agent_qa → verify → approval_gate …)는
Agent 담당이 이 뼈대 위에 노드를 늘려 가며 만든다.

설계 배경: docs/walking-skeleton.md · 결정 근거: docs/decisions.md
"""

import json
import os
import time

import httpx
from fastapi import BackgroundTasks, FastAPI
from fastmcp import Client
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

# compose 안에서는 서비스 이름이 그대로 주소가 된다.
# MCP 주소 끝의 `/mcp`를 빼면 404가 난다 (교재 4장에서 가장 자주 나는 오류).
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8080")
BUSINESS_MCP_URL = os.getenv("BUSINESS_MCP_URL", "http://business-mcp:9101/mcp")
RAG_MCP_URL = os.getenv("RAG_MCP_URL", "http://rag-mcp:9102/mcp")


# ---------------------------------------------------------------------------
# Tools — MCP 기능을 LangChain 도구로 감싼다 (Harness의 Tools 요소)
#
# 모델에게는 이 함수들의 docstring이 그대로 도구 설명으로 전달된다.
# 그래서 docstring에 "무엇을 받고 무엇을 돌려주는지 + 단위"를 적는다 (교재 2장).
#
# ponytail: 도구 호출마다 MCP Client를 새로 연결한다. 시작 순서를 신경 쓸 필요가
#           없어 뼈대에 맞지만, 호출마다 연결 비용이 든다. 도구가 늘면 Client를
#           한 번 만들어 재사용하도록 바꿀 것 (교재 4장 mcpServers 설정 방식).
# ---------------------------------------------------------------------------

@tool
async def analyze_impact(whatif: list[dict]) -> dict:
    """가정 변경(whatif)을 적용했을 때 생기는 결품·과잉 목록을 돌려준다.
    whatif 예: [{"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}]"""
    async with Client(BUSINESS_MCP_URL) as c:
        return (await c.call_tool("analyze_impact", {"whatif": whatif})).data


@tool
async def read_doc(uri: str) -> str:
    """근거 문서 조항 원문을 읽는다. uri 예: doc://ECN-2026-014/3.2"""
    # 모델은 Tool만 부를 수 있다. 그래서 rag-mcp의 Resource를 이렇게 "가상 도구"로
    # 감싸서 모델이 쓸 수 있게 만든다 (교재 3장 "LLM에 연결하기").
    # Resource는 원래 앱(여기)이 고르는 것이고, 모델이 고르는 것은 Tool이다.
    async with Client(RAG_MCP_URL) as c:
        return (await c.read_resource(uri))[0].text


TOOLS = {t.name: t for t in (analyze_impact, read_doc)}


# ---------------------------------------------------------------------------
# Model — 키가 없으면 가짜 모델로 동작한다
# ---------------------------------------------------------------------------

def fake_model(messages):
    """OPENAI_API_KEY 없이도 전체 흐름을 볼 수 있게 하는 가짜 모델.

    지금까지 받은 도구 결과(ToolMessage) 개수만 보고 다음에 할 일을 정한다.
      0개 → analyze_impact 호출
      1개 → 그 결과 안의 evidence_uri로 read_doc 호출
      2개 → 두 결과를 합쳐 답변하고 끝

    단순히 순서만 박아 둔 게 아니라 **앞 도구의 결과를 읽어서 다음 도구의 인자를
    만든다**. 진짜 모델이 하는 일("도구 결과를 보고 다음 도구를 고른다")과 같은
    모양이라, 키 없이도 팀원이 흐름을 확인할 수 있다.
    """
    results = [m for m in messages if isinstance(m, ToolMessage)]
    if len(results) == 0:
        # 아키텍처 4.3 시퀀스(SC1 "허브를 11/25로 당기면?")의 가정 변경을 그대로 쓴다
        whatif = [{"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}]
        return AIMessage("", tool_calls=[{"id": "call_1", "name": "analyze_impact", "args": {"whatif": whatif}}])
    if len(results) == 1:
        uri = json.loads(results[0].content).get("evidence_uri", "doc://ECN-2026-014/3.2")
        return AIMessage("", tool_calls=[{"id": "call_2", "name": "read_doc", "args": {"uri": uri}}])
    return AIMessage(f"[가짜 모델] 영향 분석 결과: {results[0].content}\n근거: {results[1].content}")


if os.getenv("OPENAI_API_KEY"):
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")).bind_tools(list(TOOLS.values()))
else:
    llm = None

# Harness의 Instructions 요소. 교재는 prompts/*.md로 빼서 버전 관리하라고 한다.
# 뼈대라 한 줄만 두었다 → Agent 담당이 agent/prompts/ 로 옮길 것.
INSTRUCTIONS = "너는 BOM 영향 분석 도우미다. 수량·날짜는 도구 결과만 인용하고, 근거 문서는 read_doc으로 읽어 인용한다."


async def call_model(state: MessagesState):
    """그래프의 `agent` 노드. 모델에게 다음에 할 일을 물어본다."""
    if llm is None:
        return {"messages": [fake_model(state["messages"])]}
    return {"messages": [await llm.ainvoke([SystemMessage(INSTRUCTIONS), *state["messages"]])]}


# ---------------------------------------------------------------------------
# Graph — agent ↔ tools 반복
#
#     START → agent ──(도구를 고름)──→ tools ──┐
#                 ↑                            │
#                 └────────────────────────────┘
#                 └──(도구 안 고름)──→ END
# ---------------------------------------------------------------------------

async def emit(task_id: str, event: dict):
    """backend로 진행 이벤트를 보낸다 (아키텍처 9.2, 9.3).

    실패해도 작업은 계속한다 (아키텍처 4.2: "3회 재시도, 실패해도 작업은 계속").
    이벤트는 화면에 보여 주기 위한 것이라, 못 보냈다고 분석 자체를 멈출 이유가 없다.
    seq·ts·taskId는 backend가 붙여 준다.
    """
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            await c.post(f"{BACKEND_URL}/internal/tasks/{task_id}/events", json=event)
    except httpx.HTTPError:
        pass


async def run_tools(state: MessagesState, config):
    """그래프의 `tools` 노드. 모델이 고른 도구를 실제로 실행한다.

    모델은 "이 도구를 이 인자로 부르자"고 제안만 하고, 실행은 여기(Harness)가 한다
    (교재 6장). 그래서 진행 이벤트도 도구 실패 처리도 전부 이 자리에 있다.
    """
    task_id = config["configurable"]["thread_id"]
    out = []
    for call in state["messages"][-1].tool_calls:
        await emit(task_id, {"type": "TOOL_START", "tool": call["name"]})
        started, ok = time.monotonic(), True
        try:
            result = await TOOLS[call["name"]].ainvoke(call["args"])
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception as e:
            # 도구 오류로 작업을 죽이지 않는다. 오류 메시지를 모델에게 돌려주면
            # 모델이 retry_hint를 읽고 인자를 고쳐 다시 부를 수 있다
            # (교재 6장 "오류는 ToolMessage로 반환").
            ok, content = False, f"도구 오류: {e}"
        await emit(task_id, {"type": "TOOL_END", "tool": call["name"], "ok": ok,
                             "ms": int((time.monotonic() - started) * 1000)})
        out.append(ToolMessage(content, tool_call_id=call["id"], name=call["name"]))
    return {"messages": out}


def route(state: MessagesState):
    """모델이 도구를 골랐으면 tools로, 답변을 냈으면 끝."""
    return "tools" if state["messages"][-1].tool_calls else END


builder = StateGraph(MessagesState)
builder.add_node("agent", call_model)
builder.add_node("tools", run_tools)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", route)
builder.add_edge("tools", "agent")
# 아직 checkpointer를 붙이지 않았다. 승인 대기(interrupt) → 재개를 만들 때
# compile(checkpointer=...)로 상태를 저장해야 같은 thread_id로 이어갈 수 있다.
graph = builder.compile()


# ---------------------------------------------------------------------------
# API — backend만 부른다 (포트를 공개하지 않는다)
# ---------------------------------------------------------------------------

app = FastAPI()


async def run_task(task_id: str, question: str):
    """작업 하나를 끝까지 돌리고, 결과를 DONE 이벤트로 보낸다."""
    try:
        # recursion_limit 16 ≈ 최대 8턴 (agent·tools가 번갈아 도니 턴당 2).
        # 교재 6장 Harness 권장값 "최대 8턴". 120초 제한과 "같은 도구 3회"는 아직 없다.
        state = await graph.ainvoke({"messages": [HumanMessage(question)]},
                                    {"configurable": {"thread_id": task_id}, "recursion_limit": 16})
        # 근거 패널에 쓸 sources를 만든다 (아키텍처 8.2 형식).
        # read_doc 결과(ToolMessage)에는 어떤 uri로 읽었는지가 없어서,
        # 그 호출을 제안했던 AIMessage의 tool_calls에서 인자를 찾아 짝을 맞춘다.
        args = {c["id"]: c["args"] for m in state["messages"] if isinstance(m, AIMessage) for c in m.tool_calls}
        sources = [{"uri": args[m.tool_call_id]["uri"], "excerpt": m.content}
                   for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "read_doc"]
        await emit(task_id, {"type": "DONE", "status": "DONE", "answer": state["messages"][-1].content,
                             "sources": sources})
    except Exception as e:
        # 여기서 막지 않으면 화면이 DONE도 FAILED도 못 받고 영원히 기다린다
        await emit(task_id, {"type": "FAILED", "errorCode": type(e).__name__, "message": str(e)})


@app.post("/agent/tasks", status_code=202)
async def create_task(body: dict, background: BackgroundTasks):
    """② backend → agent 작업 실행 (아키텍처 9.2).

    backend를 기다리게 하지 않으려고 바로 202를 돌려주고 뒤에서 돌린다.
    진행 상황은 전부 이벤트로 backend에 간다.

    아직 없는 것: /agent/tasks/{id}/resume (승인 후 재개), 서비스 토큰 확인.
    """
    background.add_task(run_task, body["taskId"], body["question"])
    return {"accepted": True}
