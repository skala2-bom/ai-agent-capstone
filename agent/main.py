import json
import os
import time

import httpx
from fastapi import BackgroundTasks, FastAPI
from fastmcp import Client
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8080")
BUSINESS_MCP_URL = os.getenv("BUSINESS_MCP_URL", "http://business-mcp:9101/mcp")
RAG_MCP_URL = os.getenv("RAG_MCP_URL", "http://rag-mcp:9102/mcp")


# ---- Tools: MCP 도구를 LangChain 도구로 감싼다 ----

@tool
async def analyze_impact(whatif: list[dict]) -> dict:
    """가정 변경(whatif)을 적용했을 때 생기는 결품·과잉 목록을 돌려준다.
    whatif 예: [{"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}]"""
    async with Client(BUSINESS_MCP_URL) as c:
        return (await c.call_tool("analyze_impact", {"whatif": whatif})).data


@tool
async def read_doc(uri: str) -> str:
    """근거 문서 조항 원문을 읽는다. uri 예: doc://ECN-2026-014/3.2"""
    # 모델은 Tool만 부를 수 있으므로 rag-mcp Resource를 가상 도구로 연결한다 (교재 3장)
    async with Client(RAG_MCP_URL) as c:
        return (await c.read_resource(uri))[0].text


TOOLS = {t.name: t for t in (analyze_impact, read_doc)}


# ---- Model: OPENAI_API_KEY가 없으면 정해진 순서로 도구를 부르는 가짜 모델 ----

def fake_model(messages):
    results = [m for m in messages if isinstance(m, ToolMessage)]
    if len(results) == 0:
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

INSTRUCTIONS = "너는 BOM 영향 분석 도우미다. 수량·날짜는 도구 결과만 인용하고, 근거 문서는 read_doc으로 읽어 인용한다."


async def call_model(state: MessagesState):
    if llm is None:
        return {"messages": [fake_model(state["messages"])]}
    return {"messages": [await llm.ainvoke([SystemMessage(INSTRUCTIONS), *state["messages"]])]}


# ---- Graph: agent ↔ tools 반복 (도구 실행 전후로 진행 이벤트 발행) ----

async def emit(task_id: str, event: dict):
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            await c.post(f"{BACKEND_URL}/internal/tasks/{task_id}/events", json=event)
    except httpx.HTTPError:
        pass  # 이벤트 전달이 실패해도 작업은 계속한다


async def run_tools(state: MessagesState, config):
    task_id = config["configurable"]["thread_id"]
    out = []
    for call in state["messages"][-1].tool_calls:
        await emit(task_id, {"type": "TOOL_START", "tool": call["name"]})
        started, ok = time.monotonic(), True
        try:
            result = await TOOLS[call["name"]].ainvoke(call["args"])
            content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        except Exception as e:  # 도구 오류는 모델에게 돌려줘서 다시 판단하게 한다
            ok, content = False, f"도구 오류: {e}"
        await emit(task_id, {"type": "TOOL_END", "tool": call["name"], "ok": ok,
                             "ms": int((time.monotonic() - started) * 1000)})
        out.append(ToolMessage(content, tool_call_id=call["id"], name=call["name"]))
    return {"messages": out}


def route(state: MessagesState):
    return "tools" if state["messages"][-1].tool_calls else END


builder = StateGraph(MessagesState)
builder.add_node("agent", call_model)
builder.add_node("tools", run_tools)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", route)
builder.add_edge("tools", "agent")
graph = builder.compile()


# ---- API ----

app = FastAPI()


async def run_task(task_id: str, question: str):
    try:
        # recursion_limit 16 ≈ 최대 8턴 (Harness Agent Loop)
        state = await graph.ainvoke({"messages": [HumanMessage(question)]},
                                    {"configurable": {"thread_id": task_id}, "recursion_limit": 16})
        args = {c["id"]: c["args"] for m in state["messages"] if isinstance(m, AIMessage) for c in m.tool_calls}
        sources = [{"uri": args[m.tool_call_id]["uri"], "excerpt": m.content}
                   for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "read_doc"]
        await emit(task_id, {"type": "DONE", "status": "DONE", "answer": state["messages"][-1].content,
                             "sources": sources})
    except Exception as e:
        await emit(task_id, {"type": "FAILED", "errorCode": type(e).__name__, "message": str(e)})


@app.post("/agent/tasks", status_code=202)
async def create_task(body: dict, background: BackgroundTasks):
    background.add_task(run_task, body["taskId"], body["question"])
    return {"accepted": True}
