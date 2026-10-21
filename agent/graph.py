"""Agent 그래프 — 아키텍처 5장 · contracts 2.2~2.7.

노드 이름은 설계도와 같다 (decisions 6.11 "설계도와 코드 노드 이름 1:1").

    START → classify_intent ─┬─ IMPACT → agent_impact ⇄ tools ───────────────┐
                             ├─ QA     → agent_qa ⇄ tools_rag ───────────────┤
                             ├─ ADJUST → adjust_prepare → compare             │
                             │            → agent_adjust → verify → collect_evidence → approval_gate
                             │                 ↑ 실패/거절          │ 승인 → execute_change
                             │                 └────────────────────┘          → refresh_impact → compare
                             │            다음 대상이 없거나 모두 보류되면 → respond
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
MAX_ACTIONS = 10            # 한 작업에서 실제 실행할 수 있는 변경 수

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
    current_impact: dict | None               # 가장 최근 DB + 아직 적용되지 않은 사용자 가정
    current_ops: list[dict]
    scope: list[str]                          # 최초 질문의 대상(품목|규칙); 임의로 범위를 넓히지 않음
    active_target: str | None
    blocked: dict[str, str]
    executions: list[dict]
    targets: list[dict]                       # 긴급 결품 + 과잉
    options: list[dict]                       # compare_options 후보
    recommended: str | None
    tried: list[str]                          # 검증 실패·거절된 tool+args 정규화 값 (ID 재사용 방지)
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
    search_hits: list[dict]
    evidence_error: str | None
    qa_searched: bool
    turn_count: int
    tool_counts: dict
    answer: str | None
    next_actions: list[dict]
    result: dict | None


# ---------------------------------------------------------------------------
# 이벤트 · 숨김 · MCP 호출
# ---------------------------------------------------------------------------

async def emit(task_id: str, event: dict, *, important: bool = False) -> httpx.Response | None:
    """backend로 진행 이벤트를 보낸다 (아키텍처 9.2·9.3).

    진행 이벤트(TOOL_START 등)는 한 번만 보내고 실패해도 작업을 계속한다.
    작업의 끝을 알리는 이벤트(DONE·FAILED·APPROVAL_REQUIRED, important=True)는 3번까지 보낸다 —
    이게 유실되면 backend는 작업이 끝났는지 모르고 화면은 계속 기다린다.
    backend 응답을 돌려준다 (APPROVAL_REQUIRED는 {approvalId} 또는 403 ROLE_NOT_ALLOWED, contracts 3.3).
    """
    tries = 3 if important else 1
    for i in range(tries):
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                # agent 토큰은 이 이벤트 경로에만 열린다 (backend ServiceTokens)
                r = await c.post(f"{BACKEND_URL}/internal/tasks/{task_id}/events", json=event,
                                 headers={"X-Service-Token": os.getenv("SERVICE_TOKEN", "")})
            if r.status_code < 500:
                return r
        except httpx.HTTPError:
            pass
        if i < tries - 1:
            await asyncio.sleep(0.5 * (i + 1))
    return None


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
            if (k == "unit_price" and hide_unit) or (k in ("amount", "added_cost", "saved_amount") and hide_amount):
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
    "search_docs": "근거 문서 검색 중",
    "split_po": "발주 분할 실행 중", "request_due_date_change": "입고일 변경 실행 중",
    "change_po_qty": "발주 수량 변경 실행 중", "cancel_po": "발주 취소 실행 중",
    "explode_bom": "BOM 펼치는 중", "where_used": "사용 제품 찾는 중", "get_supplier_metrics": "협력사 지표 조회 중",
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
        url = RAG_MCP_URL if name in ("read_doc", "search_docs") else BUSINESS_MCP_URL
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


def merge_sources(*groups: list[dict]) -> list[dict]:
    return list({(s["uri"], s["version"]): s for group in groups for s in group}.values())


async def read_hit(state: dict, hit: dict) -> tuple[dict | None, str | None]:
    """검색 결과가 허용한 URI만 원문으로 읽고, 검색 후 문서가 바뀌면 인용하지 않는다."""
    required = ("doc_id", "title", "clause_no", "version", "uri", "content")
    if not all(isinstance(hit.get(k), str) for k in required):
        return None, "검색 결과의 출처 형식이 올바르지 않습니다."
    if hit["uri"] != f"doc://{hit['doc_id']}/{hit['clause_no']}":
        return None, "검색 결과의 URI가 문서·조항과 다릅니다."
    ok, text = await call_tool(state, "read_doc", {"uri": hit["uri"]})
    if not ok:
        return None, str(text)
    if text != hit["content"]:
        return None, "검색 후 문서 원문이 변경되었습니다. 다시 검색해야 합니다."
    return {**{k: hit[k] for k in required if k != "content"},
            "effective_date": hit.get("effective_date"), "excerpt": text}, None


async def retrieve_sources(state: dict, query: str, doc_type: str | None = None) -> tuple[list[dict], str | None]:
    ok, hits = await call_tool(state, "search_docs", {"query": query[:4000], "top_k": 3, "doc_type": doc_type})
    if not ok or not isinstance(hits, list):
        return [], str(hits) if not ok else "검색 결과 형식이 올바르지 않습니다."
    sources, errors = [], []
    for hit in hits:
        source, error = await read_hit(state, hit)
        if source:
            sources.append(source)
        if error:
            errors.append(error)
    return sources, "; ".join(errors) or (None if sources else "관련 문서 검색 결과가 없습니다.")


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


def item_label(e: dict) -> str:
    """부품 이름. backend가 item_name을 주면 그걸, 없으면 ID 그대로 — ID를 번역해 지어내지 않는다."""
    return e.get("item_name") or e["item_id"]


def describe_shortage(s: dict, plans: list[dict]) -> str:
    when = "지금 발주하면 제때 도착" if s.get("orderable") else "새 발주로는 늦음, 기존 발주 조정 필요"
    return (f"{plan_label(plans, s.get('plan_id'), s.get('plan_date'))}: {item_label(s)} "
            f"{abs(s['projected_stock']):,}개 부족 · {when}")


def lead_text(text: str) -> str:
    """모델 답변의 첫 문단만, 목록·표·제목 줄이 나오기 전까지. 지시를 어기고 목록이나 마크다운 표를 써도
    (실제 gpt-4o-mini가 그랬다) 시스템 목록과 겹치지 않고, 틀린 숫자가 화면에 나가지 않게"""
    out = []
    for line in text.strip().splitlines():
        t = line.strip()
        if not t or t.startswith(("- ", "* ", "• ", "|", "#")) or (t.endswith(":") and len(t) <= 30):
            break
        out.append(t)
    return " ".join(out).replace("**", "")


def chain_note(impact: dict, ops: list[dict], plans: list[dict]) -> str:
    """연쇄 결품 = 질문에서 바꾸지 않은 계획에 생긴 결품. 계산으로 정확히 알 수 있어 모델에게 맡기지 않는다"""
    changed = {o.get("plan_id") for o in ops or [] if o.get("plan_id")}
    if not changed:
        return ""   # 발주 지연 같은 가정은 바꾼 계획이 없다 — 모든 결품이 그 지연 때문이다
    hit = []
    for sh in impact.get("shortages", []):
        label = plan_label(plans, sh.get("plan_id"), sh.get("plan_date"))
        if sh.get("plan_id") not in changed and label not in hit:
            hit.append(label)
    return f"연쇄 결품: 바꾸지 않은 {', '.join(hit)}도 공용 부품이 모자라게 됩니다." if hit else ""


def describe_impact(impact: dict, plans: list[dict]) -> str:
    """결과를 숫자 그대로 문장으로 옮긴다. 가짜 모델 답변과 ADJUST 요약에 쓴다 (모델 없이 숫자가 틀릴 일이 없다).

    모양은 "제목 n건:" 한 줄 + "- " 항목 줄, 구역 사이는 빈 줄. 화면이 이 모양을 목록으로 그린다 (decisions 6.27).
    """
    groups = [
        ("결품", [describe_shortage(s, plans) for s in impact.get("shortages", [])]),
        ("주의", [f"{plan_label(plans, w.get('plan_id'), w.get('plan_date'))}: {item_label(w)} 재고 {w['projected_stock']:,}개 "
                  f"· 안전재고 {w['threshold']:,}개 미만" for w in impact.get("warnings", [])]),
        ("과잉", [f"{item_label(e)} {e['qty']:,}개 남음 · {e['date']} 기준, 앞으로 {e.get('window_days', 30)}일 쓸 양보다 많음"
                  for e in impact.get("excesses", [])]),
    ]
    sections = [f"{title} {len(lines)}건:\n" + "\n".join("- " + line for line in lines) for title, lines in groups if lines]
    return "\n\n".join(sections) if sections else "결품·주의·과잉이 없습니다."


# ---------------------------------------------------------------------------
# 노드: classify_intent
# ---------------------------------------------------------------------------

async def classify_question(question: str, plans: list[dict], turns: int = 0) -> tuple[W.Classification, list[str], int]:
    """질문 → (분류, 검증 오류, 모델 호출 수). 평가 스크립트도 이 함수를 그대로 쓴다.

    모델이 뽑은 whatif가 검사를 통과하지 못하면 오류를 알려 주고 한 번 더 기회를 준다.
    """
    def check(c: W.Classification) -> list[str]:
        ops = [W.normalize(o) for o in c.whatif]
        errors = W.validate_ops(ops, plans) or W.consistency_errors(question, ops, plans)
        if not errors and ops and c.unclear:
            errors.append("유효한 whatif와 unclear가 함께 있습니다. 원래 생산일·제품으로 계획이 특정되는지 다시 확인하세요. "
                          "unclear에는 의도 선택 이유를 적지 않습니다. 정말 정보가 모자라면 해당 부분만 적으세요.")
        return errors

    if llm is None:
        try:
            c = W.rule_classify(question, plans)
        except ValueError:
            c = W.Classification(intent="IMPACT", unclear="날짜를 읽지 못했습니다. 실제 월/일을 확인해 주세요.")
        return c, check(c), turns
    msgs = [SystemMessage(prompt("classify", BASE_DATE=W.BASE_DATE.isoformat(), PLANS=plans_table(plans))),
            HumanMessage(question)]
    if W.explicit_adjust_request(question):
        msgs.append(SystemMessage("이 질문에는 명시적인 해결 요청이 있으므로 intent는 ADJUST입니다. "
                                  "'문제 있어?'와 함께 있어도 모순이 아닙니다. whatif를 추출하세요. "
                                  "unclear는 생산 계획·날짜·수량이 실제로 모호할 때만 씁니다."))
    for attempt in range(2):
        turns += 1
        c = await llm.with_structured_output(W.Classification, method=STRUCTURED).ainvoke(msgs)
        if c.intent == "IMPACT" and W.explicit_adjust_request(question):
            # 실제 SC2에서 긴 가정 뒤 "해결 방법 만들어 줘"를 놓쳤다. 명시적 요청의 분기만 보완한다.
            # 모델의 whatif는 그대로 검사하며, 역할 제한과 개별 승인은 이후 단계에서 적용한다.
            c = c.model_copy(update={"intent": "ADJUST"})
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
    """search_docs가 반환한 URI의 근거 문서 조항 원문을 읽는다."""
    return ""  # 실제 실행은 tools_rag 노드


@tool("search_docs")
def search_docs_tool(query: str, top_k: int = 5, doc_type: str | None = None) -> str:
    """규정·계약·ECN 조항을 검색한다. 반환된 URI로 read_doc을 호출한 뒤 인용한다."""
    return ""


async def agent_impact(state: AgentState) -> dict:
    turns = next_turn(state)
    if llm is None:
        if state.get("impact") is None:
            msg = AIMessage("", tool_calls=[{"id": f"call_{turns}", "name": "analyze_impact", "args": {}}])
        else:
            msg = AIMessage("[가짜 모델] 영향 분석 결과입니다.\n" + describe_impact(state["impact"], state["plans"]))
        return {"messages": [msg], "turn_count": turns}
    system = SystemMessage(prompt("impact", PLANS=plans_table(state["plans"]),
                                  SOURCES=json.dumps(state.get("evidence") or [], ensure_ascii=False)))
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
                if not state.get("impact"):
                    evidence, error = await retrieve_sources(state, state["question"])
                    updates["evidence"] = evidence
                    updates["evidence_error"] = error
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
        if not state.get("qa_searched"):
            msg = AIMessage("", tool_calls=[{"id": f"call_{turns}", "name": "search_docs",
                                            "args": {"query": state["question"], "top_k": 3}}])
        elif not state.get("evidence") and state.get("search_hits") and not state.get("evidence_error"):
            msg = AIMessage("", tool_calls=[{"id": f"call_{turns}", "name": "read_doc",
                                            "args": {"uri": state["search_hits"][0]["uri"]}}])
        elif not state.get("evidence"):
            msg = AIMessage("관련 규정을 확인하지 못했습니다. 문서 색인·검색 상태를 확인해 주세요.")
        else:
            e = state["evidence"][0]
            msg = AIMessage(f"[가짜 모델] 근거 조항 원문입니다.\n{e['excerpt']}\n(근거: {e['uri']})")
        return {"messages": [msg], "turn_count": turns}
    system = SystemMessage(prompt("qa"))
    msg = await llm.bind_tools([search_docs_tool, read_doc_tool]).ainvoke([system, *state["messages"]])
    return {"messages": [msg], "turn_count": turns}


async def tools_rag(state: AgentState) -> dict:
    counts = dict(state.get("tool_counts") or {})
    evidence = list(state.get("evidence") or [])
    hits = list(state.get("search_hits") or [])
    searched = state.get("qa_searched", False)
    error = state.get("evidence_error")
    out_msgs = []
    for call in state["messages"][-1].tool_calls:
        counts[call["name"]] = counts.get(call["name"], 0) + 1
        if counts[call["name"]] > MAX_SAME_TOOL:
            content = f"같은 도구를 {MAX_SAME_TOOL}번 넘게 불렀습니다. 지금까지의 결과로 답하세요."
        elif call["name"] == "search_docs":
            searched = True
            ok, res = await call_tool(state, "search_docs", call["args"])
            if ok and isinstance(res, list):
                hits = list({h["uri"]: h for h in [*hits, *res] if isinstance(h, dict) and "uri" in h}.values())
            else:
                error = str(res)
            content = json.dumps(res, ensure_ascii=False) if ok else str(res)
        elif call["name"] == "read_doc":
            hit = next((h for h in hits if h["uri"] == call["args"].get("uri")), None)
            if hit is None:
                content = "먼저 search_docs로 찾은 URI만 읽을 수 있습니다."
            else:
                source, error = await read_hit(state, hit)
                if source:
                    evidence = merge_sources(evidence, [source])
                content = json.dumps(source, ensure_ascii=False) if source else error
        else:
            content = f"이 경로에서 쓸 수 없는 도구입니다: {call['name']}"
        out_msgs.append(ToolMessage(content, tool_call_id=call["id"], name=call["name"]))
    return {"messages": out_msgs, "tool_counts": counts, "evidence": evidence,
            "search_hits": hits, "qa_searched": searched, "evidence_error": error}


# ---------------------------------------------------------------------------
# 노드: ADJUST 경로
# ---------------------------------------------------------------------------

async def adjust_prepare(state: AgentState) -> dict:
    """최초 분석은 보존하고, 결품·과잉의 최초 대상 범위를 고정한다."""
    ok, impact = await call_tool(state, "analyze_impact", {"whatif": state.get("whatif_ops") or []})
    if not ok:
        return {"failure": impact}
    targets = W.actionable(impact)
    return {"impact": impact, "current_impact": impact, "current_ops": state.get("whatif_ops") or [],
            "targets": targets, "scope": list(dict.fromkeys(W.target_key(t) for t in targets))}


def after_prepare(state: AgentState) -> str:
    return "compare" if not state.get("failure") and state.get("targets") else "respond"


async def compare(state: AgentState) -> dict:
    """한 품목·규칙씩 처리한다. 다른 품목의 기존 문제는 다음 승인에서 처리한다."""
    pending = [t for t in W.actionable(state["current_impact"])
               if W.target_key(t) in state["scope"] and W.target_key(t) not in (state.get("blocked") or {})]
    if not pending:
        return {"active_target": None, "targets": [], "selected_option": None}
    if len(state.get("executions") or []) >= MAX_ACTIONS:
        return {"failure": f"변경 {MAX_ACTIONS}건 한도에 도달했습니다. 남은 문제는 새 작업으로 확인하세요."}
    key = W.target_key(pending[0])
    targets = [t for t in pending if W.target_key(t) == key]
    ok, res = await call_tool(state, "compare_options",
                              {"whatif": state["current_ops"], "issues": targets})
    if not ok:
        return {"failure": res}
    options = [o for o in res.get("options", []) if W.target_key(o) == key]
    return {"active_target": key, "targets": targets, "options": options,
            "recommended": next((o["option_id"] for o in options if o.get("recommended")), None),
            "selected_option": None, "decision": None, "verify": None, "verify_count": 0, "reject_count": 0}


def after_compare(state: AgentState) -> str:
    return "respond" if state.get("failure") or not state.get("active_target") else "agent_adjust"


class Pick(BaseModel):
    option_id: str = Field(description="고른 후보의 option_id. 후보 목록에 있는 값만")
    reason: str = Field(description="왜 골랐는지 한두 문장")


def candidates(state: AgentState) -> list[dict]:
    tried = set(state.get("tried") or [])
    return [o for o in state.get("options") or [] if o.get("feasible") and o.get("resolves")
            and not o.get("side_effects") and W.option_key(o) not in tried]


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
    return "verify" if state.get("selected_option") else "skip_target"


async def verify(state: AgentState) -> dict:
    """고른 대안의 tool·args를 **그대로** whatif에 더해 다시 계산하고 판정한다 (contracts 2.4)."""
    opt = state["selected_option"]
    ops = list(state["current_ops"]) + [W.option_op(opt)]
    ok, after = await call_tool(state, "analyze_impact", {"whatif": ops}, label="해결안 검증 중")
    verdict = W.judge(state["current_impact"], after, state["targets"]) if ok else {"passed": False, "error": after}
    verdict["option_id"] = opt["option_id"]
    verdict["target"] = state["active_target"]
    verdict["remaining_issues"] = opt.get("remaining_issues") or []
    verdict["side_effects"] = opt.get("side_effects") or []
    log = list(state.get("verify_log") or []) + [verdict]
    if verdict["passed"]:
        return {"verify": verdict, "verify_log": log}
    return {"verify": verdict, "verify_log": log, "verify_count": state.get("verify_count", 0) + 1,
            "tried": list(state.get("tried") or []) + [W.option_key(opt)]}


def after_verify(state: AgentState) -> str:
    if state["verify"]["passed"]:
        return "collect_evidence"
    return "skip_target" if state.get("verify_count", 0) > MAX_VERIFY_RETRIES else "agent_adjust"


async def collect_evidence(state: AgentState) -> dict:
    opt = state["selected_option"]
    sources, error = await retrieve_sources(
        state, f"{opt['item_id']} {opt['tool']} {opt.get('summary', '')} 공급계약 승인 조건", "CONTRACT")
    # SC3의 설계변경은 계약뿐 아니라 시행일이 있는 ECN 원문을 확인한다.
    if opt["item_id"].startswith("CASE-DOOR"):
        ecn, ecn_error = await retrieve_sources(state, f"{opt['item_id']} 설계변경 사용 제한 취소", "ECN")
        sources = merge_sources(sources, ecn)
        error = error or ecn_error
    if error or not sources:
        return {"evidence_error": error or "승인 근거를 확보하지 못했습니다."}
    return {"selected_option": {**opt, "evidence": sources},
            "evidence": merge_sources(state.get("evidence") or [], sources), "evidence_error": None}


def after_evidence(state: AgentState) -> str:
    return "skip_target" if state.get("evidence_error") else "approval_gate"


def skip_target(state: AgentState) -> dict:
    blocked = dict(state.get("blocked") or {})
    reason = state.get("evidence_error") or (
        "거절 재제안 한도를 넘었습니다." if state.get("reject_count", 0) > MAX_REJECTS
        else "검증을 통과하고 부작용이 없는 미시도 후보가 없습니다.")
    if state.get("verify") and not state["verify"].get("passed"):
        reason += " 남은 문제: " + json.dumps(state["verify"].get("remaining_issues") or
                                           state["verify"].get("unresolved") or [], ensure_ascii=False)
    elif not state.get("evidence_error") and not state.get("rejections"):
        problems = [o for o in state.get("options") or [] if o.get("remaining_issues") or o.get("side_effects")]
        for opt in problems[:3]:
            reason += f" {opt['option_id']}: " + json.dumps(
                {"remaining_issues": opt.get("remaining_issues", []),
                 "side_effects": opt.get("side_effects", [])}, ensure_ascii=False)
    blocked[state["active_target"]] = reason
    return {"blocked": blocked, "selected_option": None, "decision": None, "evidence_error": None}


def approval_gate(state: AgentState) -> dict:
    """변경 도구 앞에서 멈추고 팀장 결정을 기다린다 (Harness Guardrails, contracts 2.7·3.3).

    interrupt는 그래프를 멈추면서 아래 값을 밖(run)으로 넘기고, 재개되면 팀장 결정을 돌려준다.
    재개 때 이 노드는 **처음부터 다시** 실행되므로 interrupt 앞에서는 이벤트·저장 같은 일을 하지 않는다.
    APPROVAL_REQUIRED 이벤트는 그래프가 멈춘 뒤 run이 보낸다. approvalId는 backend가 만든다.
    """
    opt = state["selected_option"]
    decision = interrupt({
        "tool": opt["tool"], "args": opt["args"],
        "summary": f"{opt.get('summary', '')} (실제 발주 변경입니다. 협력사 협의·확약 여부를 확인한 뒤 승인하세요.)",
        "amount": opt.get("added_cost"), "evidence": opt.get("evidence") or [],
        "option_id": opt["option_id"],
        "verify": {"passed": True, "warnings_after": state["verify"].get("warnings_after", [])},
    })
    if decision.get("decision") == "APPROVED":
        return {"decision": decision}
    return {"decision": decision, "reject_count": state.get("reject_count", 0) + 1,
            "tried": list(state.get("tried") or []) + [W.option_key(opt)],
            "rejections": list(state.get("rejections") or [])
            + [{"option_id": opt["option_id"], "reason": decision.get("reason")}]}


def after_gate(state: AgentState) -> str:
    if state["decision"].get("decision") == "APPROVED":
        return "execute_change"
    return "skip_target" if state.get("reject_count", 0) > MAX_REJECTS else "agent_adjust"


async def execute_change(state: AgentState) -> dict:
    """승인 후 최신 후보·영향을 다시 검사한다. 인자·비용이 바뀌면 기존 승인으로 실행하지 않는다."""
    opt, decision = state["selected_option"], state["decision"]
    ok_before, before = await call_tool(state, "analyze_impact", {"whatif": state["current_ops"]})
    ok_options, latest = await call_tool(state, "compare_options",
                                        {"whatif": state["current_ops"], "issues": state["targets"]})
    current = next((o for o in latest.get("options", []) if W.option_key(o) == W.option_key(opt)
                    and W.target_key(o) == state["active_target"]), None) if ok_options else None
    if (not ok_before or current is None or not current.get("feasible") or not current.get("resolves") or current.get("side_effects")
            or any(current.get(k) != opt.get(k) for k in ("added_cost", "saved_amount", "risk"))):
        return {"executed": {"ok": False, "error": "승인 대기 중 데이터/대안 조건이 달라졌거나 확인에 실패했습니다. 새로 분석·승인해 주세요."}}
    ok_after, after = await call_tool(state, "analyze_impact",
                                      {"whatif": [*state["current_ops"], W.option_op(opt)]})
    if not ok_after or not W.judge(before, after, state["targets"])["passed"]:
        return {"executed": {"ok": False, "error": "승인 후 재검증에 실패하여 실행을 보류했습니다."}}
    if opt["tool"] not in await destructive_tools(state):
        return {"executed": {"ok": False, "error": f"{opt['tool']}는 승인 대상 변경 도구가 아닙니다."}}
    ok, res = await call_tool(state, opt["tool"], {**opt["args"], "approval_id": decision.get("approvalId")})
    record = {"ok": ok, "result": res if ok else None, "error": None if ok else res}
    if not ok:
        return {"executed": record}
    return {"executed": record,
            "executions": [*(state.get("executions") or []), {"tool": opt["tool"], "args": opt["args"],
                           "target_rule": opt["target_rule"], "item_id": opt["item_id"],
                           "approval_id": decision["approvalId"], "result": res}],
            "current_ops": W.remaining_ops(state["current_ops"], opt)}


def after_execution(state: AgentState) -> str:
    return "refresh_impact" if state["executed"]["ok"] else "respond"


async def refresh_impact(state: AgentState) -> dict:
    # DB는 이미 변경됐다. 실행한 op를 다시 넣으면 분할·감량이 중복 반영된다.
    ok, impact = await call_tool(state, "analyze_impact", {"whatif": state["current_ops"]},
                                label="실행 후 남은 문제 확인 중")
    if not ok:
        return {"failure": "변경은 실행됐지만 실행 후 분석을 확인하지 못했습니다: " + str(impact)}
    return {"current_impact": impact}


def after_refresh(state: AgentState) -> str:
    return "respond" if state.get("failure") else "compare"


# ---------------------------------------------------------------------------
# 노드: respond — 최종 답변과 결과 정리
# ---------------------------------------------------------------------------

def describe_execution(res: dict) -> str:
    """변경 도구 응답(contracts 2.1)을 사람이 읽는 한 줄로. 모양을 모르면 그대로 보여 준다."""
    if "new" in res and "original" in res:   # split_po
        o, n = res["original"], res["new"]
        return (f"실행 결과: {o['po_id']}는 {o['qty']:,}개 · {o['due_date']} 입고로 줄었고, "
                f"새 발주 {n['po_id']}가 {n['qty']:,}개 · {n['due_date']} 입고로 생겼습니다.")
    if "before" in res and "after" in res:   # request_due_date_change
        if "qty" in res["after"]:
            return f"실행 결과: {res.get('po_id')} 수량이 {res['before'].get('qty')} → {res['after']['qty']}개로 바뀌었습니다."
        if "status" in res["after"]:
            return f"실행 결과: {res.get('po_id')} 상태가 {res['before'].get('status')} → {res['after']['status']}로 바뀌었습니다."
        return (f"실행 결과: {res.get('po_id')} 입고 예정일이 "
                f"{res['before'].get('due_date')} → {res['after'].get('due_date')}로 바뀌었습니다.")
    return f"실행 결과: {json.dumps(res, ensure_ascii=False)}"


def _adjust_answer(state: AgentState) -> str:
    plans, impact = state.get("plans") or [], state.get("current_impact") or state.get("impact") or {}
    executions = state.get("executions") or []
    # 구역마다 "제목:" + "- " 항목, 사이는 빈 줄 — 화면이 목록으로 그린다 (decisions 6.27)
    parts = [f"승인된 변경 {len(executions)}건을 실행했습니다." if executions else "현재 분석 결과입니다."]
    if executions:
        parts.append("실행한 변경:\n" + "\n".join("- " + describe_execution(e["result"]).removeprefix("실행 결과: ")
                                                for e in executions))
    held = []
    if state.get("executed") and not state["executed"]["ok"]:
        held.append("실행 보류/실패: " + str(state["executed"]["error"]))
    held += [f"{key}: {reason}" for key, reason in (state.get("blocked") or {}).items()]
    held += [f"거절 사유: {rejection.get('reason')}" for rejection in state.get("rejections") or []]
    if held:
        parts.append("보류·거절:\n" + "\n".join("- " + h for h in held))
    if state.get("whatif_ops"):
        parts.append("질문의 생산계획 가정은 저장하지 않았습니다. 아래는 현재 DB에 그 가정을 더해 다시 분석한 결과입니다.")
    parts.append(describe_impact(impact, plans))
    if any(s.get("orderable") for s in impact.get("shortages", [])):
        parts.append("지금 발주하면 제때 오는 부족분은 안내만 했고, 새 발주는 만들지 않았습니다.")
    return "\n\n".join(parts)


def respond(state: AgentState) -> dict:
    intent, plans = state.get("intent"), state.get("plans") or []
    if state.get("failure"):
        answer = f"처리를 마치지 못했습니다: {state['failure']}"
        if state.get("executions"):
            answer = _adjust_answer(state) + "\n" + answer
    elif intent == "CLARIFY":
        answer = ("질문에서 바꿀 계획이나 날짜를 하나로 정하지 못했습니다: " + (state.get("clarify") or "")
                  + "\n예: \"허브를 11/25로 당기면 문제 있어?\"")
    elif intent == "ADJUST":
        answer = _adjust_answer(state)
    else:
        last = next((m for m in reversed(state.get("messages") or []) if isinstance(m, AIMessage)), None)
        answer = last.content if last else ""
        impact = state.get("impact") or {}
        if intent == "IMPACT" and impact:
            # 목록은 모델이 옮겨 적지 않고 계산 결과에서 그대로 붙인다. 모델은 결론·설명 문장만 (prompts/impact.md).
            # 실제 모델이 6건 중 5건만 옮기고 "새 발주로는 늦음"을 "주문할 수 없다"로 바꿔 쓴 적이 있다 (decisions 6.27)
            answer = "\n\n".join(x for x in (lead_text(answer), chain_note(impact, state.get("whatif_ops"), plans)) if x)
            if any(impact.get(k) for k in ("shortages", "warnings", "excesses")):
                answer = (answer + "\n\n" if answer else "") + describe_impact(impact, plans)
    if state.get("notice"):
        answer = f"({state['notice']})\n{answer}"
    if state.get("evidence"):
        answer += "\n\n확인한 근거:\n" + "\n".join(
            f"- {s['title']} 제{s['clause_no']}조 ({s['version']})" for s in state["evidence"])
    elif intent == "QA":
        answer = "관련 원문을 확인하지 못해 규정 답변을 확정할 수 없습니다. 문서 색인·검색 상태를 확인해 주세요."
    if state.get("evidence_error"):
        answer += "\n근거 확인 미완료: " + state["evidence_error"]

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
        result.update({"executions": state.get("executions") or [],
                       "post_execution_impact": state.get("current_impact"),
                       "remaining_whatif": state.get("current_ops") or [],
                       "blocked_targets": state.get("blocked") or {}})
    return {"answer": answer, "next_actions": next_actions, "result": result}


# ---------------------------------------------------------------------------
# 그래프 조립 (checkpointer는 main.py가 붙인다)
# ---------------------------------------------------------------------------

builder = StateGraph(AgentState)
for name, fn in [("classify_intent", classify_intent), ("agent_impact", agent_impact), ("tools", tools),
                 ("agent_qa", agent_qa), ("tools_rag", tools_rag), ("adjust_prepare", adjust_prepare),
                 ("compare", compare), ("agent_adjust", agent_adjust), ("verify", verify),
                 ("collect_evidence", collect_evidence), ("skip_target", skip_target),
                 ("approval_gate", approval_gate), ("execute_change", execute_change),
                 ("refresh_impact", refresh_impact), ("respond", respond)]:
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
builder.add_conditional_edges("agent_adjust", after_adjust, ["verify", "skip_target"])
builder.add_conditional_edges("verify", after_verify, ["collect_evidence", "agent_adjust", "skip_target"])
builder.add_conditional_edges("collect_evidence", after_evidence, ["approval_gate", "skip_target"])
builder.add_edge("skip_target", "compare")
builder.add_conditional_edges("approval_gate", after_gate, ["execute_change", "agent_adjust", "skip_target"])
builder.add_conditional_edges("execute_change", after_execution, ["refresh_impact", "respond"])
builder.add_conditional_edges("refresh_impact", after_refresh, ["compare", "respond"])
builder.add_edge("respond", END)
