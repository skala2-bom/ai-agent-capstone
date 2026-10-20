"""Agent 그래프 — 아키텍처 5장 · contracts 2.2~2.7.

노드 이름은 설계도와 같다 (decisions 6.11 "설계도와 코드 노드 이름 1:1").

    START → classify_intent ─┬─ IMPACT → agent_impact ⇄ tools ───────────────┐
                             ├─ QA     → agent_qa ⇄ tools_rag ───────────────┤
                             ├─ ADJUST → adjust_prepare → compare             │
                             │            → agent_adjust → verify ──(통과)──→ approval_gate
                             │                 ↑   (실패·최대 2회) │            │ 승인 → execute_change ─┤
                             │                 └──────────────────┴── 거절(사유 반영·최대 2회)          │
                             └─ CLARIFY ─────────────────────────────────────────────────→ respond → END

모델(LLM)이 판단하는 노드: classify_intent, agent_impact, agent_qa, agent_adjust
코드가 실행하는 노드: tools, tools_rag, adjust_prepare, compare, verify, approval_gate,
                      execute_change, respond

원칙 (교재 6장 · 아키텍처 1.1)
  - 수량·날짜는 모델이 만들지 않는다. whatif는 classify_intent가 뽑은 뒤 코드가 검사하고,
    해결안의 실행 인자는 compare_options가 준 tool·args를 그대로 쓴다.
  - 변경 도구는 approval_gate(interrupt)를 거쳐야만 실행된다. 실행 직전에 destructive_hint도 다시 본다.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Annotated, Any, TypedDict

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from pydantic import BaseModel, Field

import whatif as W

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8080")
BUSINESS_MCP_URL = os.getenv("BUSINESS_MCP_URL", "http://business-mcp:9101/mcp")
RAG_MCP_URL = os.getenv("RAG_MCP_URL", "http://rag-mcp:9102/mcp")

# Harness 한도 (교재 권장값 그대로, decisions 6.7)
MAX_TURNS = 8               # 모델 호출 횟수
MAX_SAME_TOOL = 3           # 같은 도구를 부르는 횟수 (모델이 고르는 도구 기준)
TOOL_TIMEOUT_S = 10         # 도구 1회 (아키텍처 4.2)
MAX_VERIFY_RETRIES = 2      # verify 실패 후 다른 대안으로 다시 (contracts 2.4)
MAX_REJECTS = 2             # 팀장 거절 후 다시 제안 (contracts 3.4)

# rag-mcp에 search_docs가 생기기 전까지 모델이 읽을 수 있는 조항 목록.
# 이 목록 밖의 주소를 지어내 부르지 않게 지시문에 그대로 넣는다.
KNOWN_CLAUSES = ["doc://ECN-2026-014/3.2"]

PROMPTS = Path(__file__).parent / "prompts"


def prompt(name: str, **values: str) -> str:
    """지시문은 코드가 아니라 prompts/*.md에 둔다 (Harness Instructions, 버전 관리)."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


# ---------------------------------------------------------------------------
# Model — 키가 없으면 가짜 모델(규칙)로 동작한다
# ---------------------------------------------------------------------------

if os.getenv("OPENAI_API_KEY"):
    from langchain_openai import ChatOpenAI

    # max_retries: 429·503 등 일시 오류를 SDK가 다시 시도한다 (Harness Recovery, 교재 권장 2회).
    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0, timeout=30, max_retries=2)
    if os.getenv("FALLBACK_MODEL"):
        # 대체 모델. decisions 6.11은 제공자를 다르게 두라고 한다 — 키를 받으면 여기만 바꾼다.
        llm = llm.with_fallbacks([ChatOpenAI(model=os.environ["FALLBACK_MODEL"], temperature=0, timeout=30)])
else:
    llm = None


# 구조화 출력은 function_calling 방식으로 받는다. 기본(json_schema)은 gpt-4o-mini에서 날짜 값 뒤에
# JSON 조각이 섞여 나오는 일이 있었다 ("2026-11-25}],", 평가셋 SC1·SC3). 검사가 막긴 했지만 매번 재시도하게 된다.
STRUCTURED = "function_calling"


class LimitExceeded(Exception):
    """Harness Agent Loop 한도 초과. run 쪽에서 FAILED(LOOP_LIMIT)로 바꾼다."""


class ToolUnavailable(Exception):
    """작업을 시작하는 데 꼭 필요한 도구(생산 계획 조회)를 못 쓸 때. run이 FAILED(TOOL_UNAVAILABLE)로 바꾼다."""


def next_turn(state: dict) -> int:
    turns = state.get("turn_count", 0) + 1
    if turns > MAX_TURNS:
        raise LimitExceeded(f"모델 호출이 {MAX_TURNS}회를 넘었습니다.")
    return turns


# ---------------------------------------------------------------------------
# State — 노드끼리 주고받는 값. checkpointer가 단계마다 저장한다 (재개에 필요한 값은 전부 여기)
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]   # 대화 (이어 붙임)
    task_id: str
    thread_id: str
    trace_id: str
    user_id: str | None
    role: str | None                          # PLANNER | BUYER | APPROVER (금액 숨김·ADJUST 권한)
    question: str
    plans: list[dict]                         # 생산 계획 표 (plan_id → 제품 이름)
    intent: str                               # IMPACT | ADJUST | QA | CLARIFY
    whatif_ops: list[dict]                    # 가정 변경 (contracts 2.2 형식)
    whatif_source: str                        # agent(질문에서 뽑음) | backend(자동 작업, contracts 2.6)
    clarify: str | None                       # 되물을 내용
    notice: str | None                        # 답변 앞에 붙일 안내 (예: 권한 때문에 IMPACT로 답함)
    failure: str | None                       # 도구 오류로 끝낼 때
    impact: dict | None                       # analyze_impact 결과 (변경 적용 후, 해결안 적용 전)
    targets: list[dict]                       # 해결할 결품 (orderable=false)
    options: list[dict]                       # compare_options 후보
    recommended: str | None
    tried: list[str]                          # 검증 실패·거절된 option_id
    selected_option: dict | None              # 고른 후보 (compare_options에서 그대로 복사)
    pick_reason: str | None
    verify: dict | None                       # 마지막 verify 판정
    verify_log: list[dict]
    verify_count: int
    reject_count: int
    rejections: list[dict]
    decision: dict | None                     # 팀장 결정 (approval_gate에서 받음)
    executed: dict | None                     # 변경 도구 실행 결과
    evidence: list[dict]                      # 읽은 근거 조항 (성공한 것만)
    turn_count: int
    tool_counts: dict
    answer: str | None
    next_actions: list[dict]
    result: dict | None


# ---------------------------------------------------------------------------
# 이벤트 · 숨김 · MCP 호출
# ---------------------------------------------------------------------------

async def emit(task_id: str, event: dict, *, important: bool = False) -> None:
    """backend로 진행 이벤트를 보낸다 (아키텍처 9.2·9.3).

    진행 이벤트(TOOL_START 등)는 한 번만 보내고 실패해도 작업을 계속한다.
    작업의 끝을 알리는 이벤트(DONE·FAILED·APPROVAL_REQUIRED, important=True)는 3번까지 보낸다 —
    이게 유실되면 backend는 작업이 끝났는지 모르고 화면은 계속 기다린다.
    """
    tries = 3 if important else 1
    for i in range(tries):
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.post(f"{BACKEND_URL}/internal/tasks/{task_id}/events", json=event)
            if r.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        if i < tries - 1:
            await asyncio.sleep(0.5 * (i + 1))


def mask(value: Any, role: str | None) -> Any:
    """금액 2차 숨김 (contracts 1.2 SEC-001). 1차는 backend가 한다.

    키는 남기고 값만 null로 바꾼다 — 키가 사라지면 역할마다 모양이 달라진다 (contracts 2.2).
    단가는 팀장만, 총액·추가 비용은 구매·팀장만 본다.
    """
    hide_unit = role != "APPROVER"
    hide_amount = role not in ("BUYER", "APPROVER")
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if (k == "unit_price" and hide_unit) or (k in ("amount", "added_cost") and hide_amount):
                out[k] = None
            else:
                out[k] = mask(v, role)
        return out
    if isinstance(value, list):
        return [mask(v, role) for v in value]
    return value


LABELS = {
    "analyze_impact": "영향 분석 중", "compare_options": "해결 방법 비교 중", "list_plans": "생산 계획 조회 중",
    "get_item": "품목 조회 중", "read_doc": "근거 문서 읽는 중",
    "split_po": "발주 분할 실행 중", "request_due_date_change": "입고일 변경 실행 중",
}


def _headers(state: dict) -> dict:
    """요청자 헤더 (contracts 1.1). 역할은 도구 인자가 아니라 헤더로만 넘긴다."""
    h = {"X-Task-Id": state.get("task_id") or "", "X-User-Id": state.get("user_id") or "",
         "X-Role": state.get("role") or ""}
    if os.getenv("SERVICE_TOKEN"):
        h["X-Service-Token"] = os.environ["SERVICE_TOKEN"]
    return h


async def call_tool(state: dict, name: str, args: dict, *, label: str | None = None,
                    quiet: bool = False) -> tuple[bool, Any]:
    """MCP 도구 실행. 성공하면 (True, 결과), 실패하면 (False, 오류 문장).

    도구 오류로 작업을 죽이지 않는다. 오류 문장(retry_hint 포함)을 모델이나 판정에 돌려준다.
    quiet=True면 진행 이벤트를 보내지 않는다 (제품 이름 조회 같은 내부 준비 작업).
    """
    task_id = state.get("task_id", "")
    if not quiet:
        await emit(task_id, {"type": "TOOL_START", "tool": name, "label": label or LABELS.get(name, name)})
    started, ok = time.monotonic(), True
    try:
        url = RAG_MCP_URL if name == "read_doc" else BUSINESS_MCP_URL
        async with Client(StreamableHttpTransport(url, headers=_headers(state))) as c:
            if name == "read_doc":
                # rag-mcp의 Resource를 읽는다. 모델은 Tool만 부를 수 있어 read_doc이라는 이름으로 감싼다 (교재 3장)
                result = (await asyncio.wait_for(c.read_resource(args["uri"]), TOOL_TIMEOUT_S))[0].text
            else:
                result = (await asyncio.wait_for(c.call_tool(name, args), TOOL_TIMEOUT_S)).data
    except asyncio.TimeoutError:
        ok, result = False, f"[UPSTREAM_TIMEOUT] {name} 도구가 {TOOL_TIMEOUT_S}초 안에 답하지 않았습니다."
    except Exception as e:
        ok, result = False, f"도구 오류: {e}"
    if not quiet:
        await emit(task_id, {"type": "TOOL_END", "tool": name, "ok": ok,
                             "ms": int((time.monotonic() - started) * 1000)})
    return ok, result


_destructive: set[str] | None = None
_product_names: dict[str, str] = {}


async def destructive_tools(state: dict) -> set[str]:
    """승인이 필요한 도구 = business-mcp가 destructive_hint=true로 표시한 도구 (이름으로 판단하지 않는다)."""
    global _destructive
    if _destructive is None:
        async with Client(StreamableHttpTransport(BUSINESS_MCP_URL, headers=_headers(state))) as c:
            tools = await c.list_tools()
        _destructive = {t.name for t in tools if t.annotations and t.annotations.destructive_hint}
    return _destructive


async def load_plans(state: dict) -> list[dict]:
    """생산 계획 표 + 제품 이름. classify_intent가 "허브" → PP-05를 찾는 데 쓴다 (Harness Context)."""
    ok, res = await call_tool(state, "list_plans", {}, label="생산 계획 조회 중")
    if not ok:
        raise ToolUnavailable(res)
    plans = res["plans"]
    for p in plans:
        pid = p["product_id"]
        if pid not in _product_names:   # 제품 이름은 거의 안 바뀌어 프로세스 동안 캐시한다
            ok2, item = await call_tool(state, "get_item", {"item_id": pid}, quiet=True)
            _product_names[pid] = item["name"] if ok2 else pid
        p["product_name"] = _product_names[pid]
    return plans


def plans_table(plans: list[dict]) -> str:
    rows = [f"| {p['plan_id']} | {p['product_name']} ({p['product_id']}) | {p['plan_date']} | {p['qty']}대 |"
            for p in plans]
    return "\n".join(["| plan_id | 제품 | 생산일 | 수량 |", "|---|---|---|---|", *rows])


def plan_label(plans: list[dict], plan_id: str | None, plan_date: str | None = None) -> str:
    """계획 이름. 날짜는 결과에 있는 값(가정 변경 후 생산일)을 쓴다 — 표의 날짜는 변경 전이다."""
    p = next((p for p in plans if p["plan_id"] == plan_id), None)
    return f"{p['product_name']} 생산({plan_id}, {plan_date or p['plan_date']})" if p else str(plan_id)


def describe_shortage(s: dict, plans: list[dict]) -> str:
    when = "지금 발주하면 제때 도착 (추가 발주 안내)" if s.get("orderable") else "새 발주로는 늦음 → 기존 발주 조정 필요"
    return (f"{plan_label(plans, s.get('plan_id'), s.get('plan_date'))}: {s['item_id']} "
            f"{abs(s['projected_stock'])}개 부족 — {when}")


def describe_impact(impact: dict, plans: list[dict]) -> str:
    """결과를 숫자 그대로 문장으로 옮긴다. 가짜 모델 답변과 ADJUST 요약에 쓴다 (모델 없이 숫자가 틀릴 일이 없다)."""
    lines = [f"- 결품: {describe_shortage(s, plans)}" for s in impact.get("shortages", [])]
    lines += [f"- 경고: {plan_label(plans, w.get('plan_id'), w.get('plan_date'))}: {w['item_id']} 재고 {w['projected_stock']}개 "
              f"(안전재고 {w['threshold']}개 미만)" for w in impact.get("warnings", [])]
    lines += [f"- 과잉: {e['item_id']} {e['qty']}개 ({e['date']} 기준)" for e in impact.get("excesses", [])]
    return "\n".join(lines) if lines else "결품·경고·과잉이 없습니다."


# ---------------------------------------------------------------------------
# 노드: classify_intent
# ---------------------------------------------------------------------------

async def classify_question(question: str, plans: list[dict], turns: int = 0) -> tuple[W.Classification, list[str], int]:
    """질문 → (분류, 검증 오류, 모델 호출 수). 평가 스크립트도 이 함수를 그대로 쓴다.

    모델이 뽑은 whatif가 검사를 통과하지 못하면 오류를 알려 주고 한 번 더 기회를 준다.
    """
    def check(c: W.Classification) -> list[str]:
        ops = [W.normalize(o) for o in c.whatif]
        return W.validate_ops(ops, plans) or W.consistency_errors(question, ops, plans)

    if llm is None:
        c = W.rule_classify(question, plans)
        return c, check(c), turns
    msgs = [SystemMessage(prompt("classify", BASE_DATE=W.BASE_DATE.isoformat(), PLANS=plans_table(plans))),
            HumanMessage(question)]
    for attempt in range(2):
        turns += 1
        c = await llm.with_structured_output(W.Classification, method=STRUCTURED).ainvoke(msgs)
        errors = check(c)
        if not errors:
            return c, [], turns
        msgs += [AIMessage(c.model_dump_json()),
                 HumanMessage("whatif 검사 실패: " + "; ".join(errors) + ". 생산 계획 표를 다시 보고 고쳐라.")]
    return c, errors, turns


async def classify_intent(state: AgentState) -> dict:
    plans = await load_plans(state)
    out = {"plans": plans}
    # 자동 작업(SC4 입고 지연 입력 후)은 backend가 whatif를 채워 보낸다. 모델이 추측할 이유가 없다 (contracts 2.6)
    if state.get("whatif_source") == "backend":
        ops = [W.normalize(o) for o in state.get("whatif_ops") or []]
        errors = W.validate_ops(ops, plans)
        if errors:
            return {**out, "intent": "CLARIFY", "clarify": "자동 작업의 가정 변경이 올바르지 않습니다: " + "; ".join(errors)}
        return {**out, "intent": "IMPACT", "whatif_ops": ops}

    c, errors, turns = await classify_question(state["question"], plans, state.get("turn_count", 0))
    if turns > MAX_TURNS:
        raise LimitExceeded(f"모델 호출이 {MAX_TURNS}회를 넘었습니다.")
    out["turn_count"] = turns
    if errors or c.unclear:
        return {**out, "intent": "CLARIFY", "clarify": c.unclear or "; ".join(errors)}
    ops = [W.normalize(o) for o in c.whatif]
    intent = c.intent
    # 같은 대화에서 "그럼 해결 방법 찾아 줘"처럼 변경을 다시 말하지 않으면, 앞에서 분석한 변경을 이어서 쓴다
    if intent == "ADJUST" and not ops and state.get("whatif_ops"):
        ops = state["whatif_ops"]
    notice = None
    if intent == "ADJUST" and state.get("role") not in ("BUYER", "APPROVER"):
        # 권한표: 발주 조정 요청은 구매·팀장만 (A2). 역할을 모르면 가장 좁은 권한으로 본다
        intent = "IMPACT"
        notice = "발주 조정 요청은 구매 담당자·팀장만 할 수 있어 영향 분석으로 답합니다."
    return {**out, "intent": intent, "whatif_ops": ops, "notice": notice}


def route_intent(state: AgentState) -> str:
    return {"IMPACT": "agent_impact", "ADJUST": "adjust_prepare", "QA": "agent_qa"}.get(state["intent"], "respond")


# ---------------------------------------------------------------------------
# 노드: IMPACT 경로 — agent_impact ⇄ tools
# ---------------------------------------------------------------------------

@tool("analyze_impact")
def analyze_impact_tool() -> str:
    """질문의 가정 변경(시스템이 이미 넣어 둠)을 적용했을 때 결품·경고·과잉을 계산한다. 인자 없음."""
    return ""  # 실제 실행은 tools 노드가 State의 whatif로 한다. 모델이 인자를 만들지 않게 하려는 것


@tool("read_doc")
def read_doc_tool(uri: str) -> str:
    """근거 문서 조항 원문을 읽는다. uri 예: doc://ECN-2026-014/3.2"""
    return ""  # 실제 실행은 tools_rag 노드


async def agent_impact(state: AgentState) -> dict:
    turns = next_turn(state)
    if llm is None:
        if state.get("impact") is None:
            msg = AIMessage("", tool_calls=[{"id": f"call_{turns}", "name": "analyze_impact", "args": {}}])
        else:
            msg = AIMessage("[가짜 모델] 영향 분석 결과입니다.\n" + describe_impact(state["impact"], state["plans"]))
        return {"messages": [msg], "turn_count": turns}
    system = SystemMessage(prompt("impact", PLANS=plans_table(state["plans"])))
    msg = await llm.bind_tools([analyze_impact_tool]).ainvoke([system, *state["messages"]])
    return {"messages": [msg], "turn_count": turns}


async def tools(state: AgentState) -> dict:
    """agent_impact가 고른 도구를 실행한다. analyze_impact의 whatif는 State 값을 넣는다."""
    counts = dict(state.get("tool_counts") or {})
    out_msgs, updates = [], {}
    for call in state["messages"][-1].tool_calls:
        counts[call["name"]] = counts.get(call["name"], 0) + 1
        if counts[call["name"]] > MAX_SAME_TOOL:
            content = f"같은 도구를 {MAX_SAME_TOOL}번 넘게 불렀습니다. 지금까지의 결과로 답하세요."
        elif call["name"] == "analyze_impact":
            ok, res = await call_tool(state, "analyze_impact", {"whatif": state.get("whatif_ops") or []})
            if ok:
                updates["impact"] = res
            content = json.dumps(mask(res, state.get("role")), ensure_ascii=False) if ok else res
        else:
            content = f"이 경로에서 쓸 수 없는 도구입니다: {call['name']}"
        out_msgs.append(ToolMessage(content, tool_call_id=call["id"], name=call["name"]))
    return {"messages": out_msgs, "tool_counts": counts, **updates}


def after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    if not getattr(last, "tool_calls", None):
        return "respond"
    return "tools" if state["intent"] == "IMPACT" else "tools_rag"


# ---------------------------------------------------------------------------
# 노드: QA 경로 — agent_qa ⇄ tools_rag
# ---------------------------------------------------------------------------

async def agent_qa(state: AgentState) -> dict:
    turns = next_turn(state)
    if llm is None:
        if not state.get("evidence"):
            msg = AIMessage("", tool_calls=[{"id": f"call_{turns}", "name": "read_doc", "args": {"uri": KNOWN_CLAUSES[0]}}])
        else:
            e = state["evidence"][0]
            msg = AIMessage(f"[가짜 모델] 근거 조항 원문입니다.\n{e['excerpt']}\n(근거: {e['uri']})")
        return {"messages": [msg], "turn_count": turns}
    system = SystemMessage(prompt("qa", CLAUSES="\n".join(f"- {u}" for u in KNOWN_CLAUSES)))
    msg = await llm.bind_tools([read_doc_tool]).ainvoke([system, *state["messages"]])
    return {"messages": [msg], "turn_count": turns}


async def tools_rag(state: AgentState) -> dict:
    counts = dict(state.get("tool_counts") or {})
    evidence = list(state.get("evidence") or [])
    out_msgs = []
    for call in state["messages"][-1].tool_calls:
        counts[call["name"]] = counts.get(call["name"], 0) + 1
        if counts[call["name"]] > MAX_SAME_TOOL:
            content = f"같은 도구를 {MAX_SAME_TOOL}번 넘게 불렀습니다. 지금까지의 결과로 답하세요."
        elif call["name"] == "read_doc":
            ok, res = await call_tool(state, "read_doc", {"uri": call["args"].get("uri", "")})
            if ok:  # 실패한 읽기는 근거가 아니다. 오류 문장이 근거 패널에 뜨지 않게 성공한 것만 남긴다
                evidence.append({"uri": call["args"]["uri"], "excerpt": res})
            content = res
        else:
            content = f"이 경로에서 쓸 수 없는 도구입니다: {call['name']}"
        out_msgs.append(ToolMessage(content, tool_call_id=call["id"], name=call["name"]))
    return {"messages": out_msgs, "tool_counts": counts, "evidence": evidence}


# ---------------------------------------------------------------------------
# 노드: ADJUST 경로
# ---------------------------------------------------------------------------

async def adjust_prepare(state: AgentState) -> dict:
    """변경을 적용한 영향을 계산하고, 해결할 결품(orderable=false)을 고른다."""
    ok, impact = await call_tool(state, "analyze_impact", {"whatif": state.get("whatif_ops") or []})
    if not ok:
        return {"failure": impact}
    return {"impact": impact, "targets": W.actionable(impact)}


def after_prepare(state: AgentState) -> str:
    return "compare" if not state.get("failure") and state.get("targets") else "respond"


async def compare(state: AgentState) -> dict:
    ok, res = await call_tool(state, "compare_options", {"issues": state["targets"]})
    if not ok:
        return {"failure": res}
    return {"options": res.get("options", []), "recommended": res.get("recommended_option_id")}


def after_compare(state: AgentState) -> str:
    return "respond" if state.get("failure") else "agent_adjust"


class Pick(BaseModel):
    option_id: str = Field(description="고른 후보의 option_id. 후보 목록에 있는 값만")
    reason: str = Field(description="왜 골랐는지 한두 문장")


def candidates(state: AgentState) -> list[dict]:
    tried = set(state.get("tried") or [])
    return [o for o in state.get("options") or [] if o.get("feasible") and o["option_id"] not in tried]


async def agent_adjust(state: AgentState) -> dict:
    """후보 중 **option_id 하나만** 고른다. 실행 인자는 코드가 후보에서 그대로 복사한다 (decisions 6.3)."""
    cands = candidates(state)
    if not cands:
        return {"selected_option": None}
    turns = state.get("turn_count", 0)
    if llm is None:
        rec = state.get("recommended")
        pick = Pick(option_id=rec if rec in {o["option_id"] for o in cands} else cands[0]["option_id"],
                    reason="[가짜 모델] 계산 서비스의 추천안을 골랐습니다.")
    else:
        turns = next_turn(state)
        context = {
            "targets": state["targets"],
            "candidates": mask(cands, state.get("role")),
            "recommended_option_id": state.get("recommended"),
            "verify_failures": [v for v in state.get("verify_log") or [] if not v.get("passed")],
            "rejections": state.get("rejections") or [],
        }
        pick = await llm.with_structured_output(Pick, method=STRUCTURED).ainvoke(
            [SystemMessage(prompt("adjust")), HumanMessage(json.dumps(context, ensure_ascii=False))])
    chosen = next((o for o in cands if o["option_id"] == pick.option_id), None)
    if chosen is None:  # 목록에 없는 ID를 내면 모델 값을 버리고 첫 후보로 (지어낸 대안을 실행하지 않는다)
        chosen, pick.reason = cands[0], f"모델이 목록에 없는 후보({pick.option_id})를 골라 첫 후보로 바꿨습니다."
    return {"selected_option": chosen, "pick_reason": pick.reason, "turn_count": turns}


def after_adjust(state: AgentState) -> str:
    return "verify" if state.get("selected_option") else "respond"


async def verify(state: AgentState) -> dict:
    """고른 대안의 tool·args를 **그대로** whatif에 더해 다시 계산하고 판정한다 (contracts 2.4)."""
    opt = state["selected_option"]
    ops = list(state.get("whatif_ops") or []) + [W.option_op(opt)]
    ok, after = await call_tool(state, "analyze_impact", {"whatif": ops}, label="해결안 검증 중")
    verdict = W.judge(state["impact"], after, state["targets"]) if ok else {"passed": False, "error": after}
    verdict["option_id"] = opt["option_id"]
    log = list(state.get("verify_log") or []) + [verdict]
    if verdict["passed"]:
        return {"verify": verdict, "verify_log": log}
    return {"verify": verdict, "verify_log": log, "verify_count": state.get("verify_count", 0) + 1,
            "tried": list(state.get("tried") or []) + [opt["option_id"]]}


def after_verify(state: AgentState) -> str:
    if state["verify"]["passed"]:
        return "approval_gate"
    return "respond" if state.get("verify_count", 0) > MAX_VERIFY_RETRIES else "agent_adjust"


def approval_gate(state: AgentState) -> dict:
    """변경 도구 앞에서 멈추고 팀장 결정을 기다린다 (Harness Guardrails, contracts 2.7·3.3).

    interrupt는 그래프를 멈추면서 아래 값을 밖(run)으로 넘기고, 재개되면 팀장 결정을 돌려준다.
    재개 때 이 노드는 **처음부터 다시** 실행되므로 interrupt 앞에서는 이벤트·저장 같은 일을 하지 않는다.
    APPROVAL_REQUIRED 이벤트는 그래프가 멈춘 뒤 run이 보낸다. approvalId는 backend가 만든다.
    """
    opt = state["selected_option"]
    decision = interrupt({
        "tool": opt["tool"], "args": opt["args"], "summary": opt.get("summary"),
        "amount": opt.get("added_cost"), "evidence": opt.get("evidence") or [],
        "option_id": opt["option_id"],
        "verify": {"passed": True, "warnings_after": state["verify"].get("warnings_after", [])},
    })
    if decision.get("decision") == "APPROVED":
        return {"decision": decision}
    return {"decision": decision, "reject_count": state.get("reject_count", 0) + 1,
            "tried": list(state.get("tried") or []) + [opt["option_id"]],
            "rejections": list(state.get("rejections") or [])
            + [{"option_id": opt["option_id"], "reason": decision.get("reason")}]}


def after_gate(state: AgentState) -> str:
    if state["decision"].get("decision") == "APPROVED":
        return "execute_change"
    return "respond" if state.get("reject_count", 0) > MAX_REJECTS else "agent_adjust"


async def execute_change(state: AgentState) -> dict:
    """승인된 tool·args에 approval_id만 더해 실행한다. backend가 승인과 같은지 다시 확인한다 (contracts 2.7)."""
    opt, decision = state["selected_option"], state["decision"]
    if opt["tool"] not in await destructive_tools(state):
        return {"executed": {"ok": False, "error": f"{opt['tool']}는 승인 대상 변경 도구가 아닙니다."}}
    ok, res = await call_tool(state, opt["tool"], {**opt["args"], "approval_id": decision.get("approvalId")})
    return {"executed": {"ok": ok, "result": res if ok else None, "error": None if ok else res}}


# ---------------------------------------------------------------------------
# 노드: respond — 최종 답변과 결과 정리
# ---------------------------------------------------------------------------

def _adjust_answer(state: AgentState) -> str:
    plans, impact = state.get("plans") or [], state.get("impact") or {}
    if not state.get("targets"):
        lines = ["조치가 필요한 결품이 없습니다 (새 발주로 늦는 결품 없음)."]
        normal = [s for s in impact.get("shortages", []) if s.get("orderable")]
        if normal:
            lines.append("지금 발주하면 제때 들어오는 부족분:")
            lines += [f"- {describe_shortage(s, plans)}" for s in normal]
        return "\n".join(lines)
    head = "해결할 결품:\n" + "\n".join(f"- {describe_shortage(t, plans)}" for t in state["targets"])
    opt, executed, decision = state.get("selected_option"), state.get("executed"), state.get("decision")
    if executed:
        if executed["ok"]:
            return f"{head}\n\n승인된 해결안을 실행했습니다: {opt.get('summary')}\n실행 결과: " \
                   f"{json.dumps(executed['result'], ensure_ascii=False)}"
        return f"{head}\n\n승인은 받았지만 실행에 실패했습니다: {executed['error']}"
    if decision and decision.get("decision") == "REJECTED":
        reasons = "; ".join(f"{r['option_id']}: {r['reason']}" for r in state.get("rejections") or [])
        return f"{head}\n\n거절이 {MAX_REJECTS}회를 넘어 더 제안하지 않습니다. 거절 사유: {reasons}"
    fails = [v for v in state.get("verify_log") or [] if not v.get("passed")]
    lines = [head, "", "검증을 통과한 해결안이 없습니다."]
    for v in fails:
        why = v.get("error") or "; ".join(
            [f"{describe_shortage(s, plans)} 남음" for s in v.get("unresolved", [])]
            + [f"{describe_shortage(s, plans)} 새로 생김" for s in v.get("new_shortages", [])])
        lines.append(f"- {v['option_id']}: {why}")
    return "\n".join(lines)


def respond(state: AgentState) -> dict:
    intent, plans = state.get("intent"), state.get("plans") or []
    if state.get("failure"):
        answer = f"분석 중 도구 오류가 났습니다: {state['failure']}"
    elif intent == "CLARIFY":
        answer = ("질문에서 바꿀 계획이나 날짜를 하나로 정하지 못했습니다: " + (state.get("clarify") or "")
                  + "\n예: \"허브를 11/25로 당기면 문제 있어?\"")
    elif intent == "ADJUST":
        answer = _adjust_answer(state)
    else:
        last = next((m for m in reversed(state.get("messages") or []) if isinstance(m, AIMessage)), None)
        answer = last.content if last else ""
    if state.get("notice"):
        answer = f"({state['notice']})\n{answer}"

    next_actions = []
    impact = state.get("impact") or {}
    if intent == "IMPACT" and W.actionable(impact):
        # 진단 뒤 해결로 이어 가기. 자동으로 넘어가지 않고 사람이 고른다 (같은 threadId로 ADJUST 요청)
        if state.get("role") in ("BUYER", "APPROVER"):
            next_actions.append({"type": "ADJUST", "label": "해결 방법 찾기"})
        else:
            next_actions.append({"type": "REQUEST_ADJUST", "label": "구매팀에 해결 요청"})
    result = {"intent": intent, "whatif": state.get("whatif_ops") or [], "impact": impact or None}
    if intent == "ADJUST":
        result.update({k: state.get(k) for k in
                       ("targets", "selected_option", "pick_reason", "verify_log", "decision", "executed")})
    return {"answer": answer, "next_actions": next_actions, "result": result}


# ---------------------------------------------------------------------------
# 그래프 조립 (checkpointer는 main.py가 붙인다)
# ---------------------------------------------------------------------------

builder = StateGraph(AgentState)
for name, fn in [("classify_intent", classify_intent), ("agent_impact", agent_impact), ("tools", tools),
                 ("agent_qa", agent_qa), ("tools_rag", tools_rag), ("adjust_prepare", adjust_prepare),
                 ("compare", compare), ("agent_adjust", agent_adjust), ("verify", verify),
                 ("approval_gate", approval_gate), ("execute_change", execute_change), ("respond", respond)]:
    builder.add_node(name, fn)
builder.add_edge(START, "classify_intent")
builder.add_conditional_edges("classify_intent", route_intent,
                              ["agent_impact", "adjust_prepare", "agent_qa", "respond"])
builder.add_conditional_edges("agent_impact", after_agent, ["tools", "respond"])
builder.add_edge("tools", "agent_impact")
builder.add_conditional_edges("agent_qa", after_agent, ["tools_rag", "respond"])
builder.add_edge("tools_rag", "agent_qa")
builder.add_conditional_edges("adjust_prepare", after_prepare, ["compare", "respond"])
builder.add_conditional_edges("compare", after_compare, ["agent_adjust", "respond"])
builder.add_conditional_edges("agent_adjust", after_adjust, ["verify", "respond"])
builder.add_conditional_edges("verify", after_verify, ["approval_gate", "agent_adjust", "respond"])
builder.add_conditional_edges("approval_gate", after_gate, ["execute_change", "agent_adjust", "respond"])
builder.add_edge("execute_change", "respond")
builder.add_edge("respond", END)
