"""Agent 그래프 — 아키텍처 5장 · contracts 2.2~2.7.

노드 이름은 설계도와 같다 (decisions 6.11 "설계도와 코드 노드 이름 1:1").

    START → classify_intent ─┬─ IMPACT → agent_impact ⇄ tools ───────────────┐
                             ├─ QA     → agent_qa ⇄ tools_rag ───────────────┤
                             ├─ ADJUST → 품목별 compare → 선택 → 검증 → 근거 → save_proposal
                             │            → check_batch → register_proposals → approval_gate
                             │                 v3: 검토 체크 / 수정 필요 → task_batch에서 상태 관리
                             │                     task 실행 → backend 전체 재검증·원자 반영 → respond
                             │                 AI 재요청 → 해당 대상만 compare → 재제안
                             │                 v2 기존 작업: 개별 승인 → execute_change → refresh_impact
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
import uuid
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
import receipt_delay as D
import classification_feedback as F
import observability as O
import model_runtime as M
from context_budget import tool_content

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8080")
BUSINESS_MCP_URL = os.getenv("BUSINESS_MCP_URL", "http://business-mcp:9101/mcp")
RAG_MCP_URL = os.getenv("RAG_MCP_URL", "http://rag-mcp:9102/mcp")

# Harness 한도: 일반 8턴, 다건 ADJUST 64턴 (decisions 6.29).
MAX_TURNS = 8               # 모델 호출 횟수
MAX_SAME_TOOL = 3           # 같은 도구를 부르는 횟수 (모델이 고르는 도구 기준)
TOOL_TIMEOUT_S = 10         # 도구 1회 (아키텍처 4.2)
MAX_VERIFY_RETRIES = 2      # verify 실패 후 다른 대안으로 다시 (contracts 2.4)
MAX_REJECTS = 2             # 팀장 거절 후 다시 제안 (contracts 3.4)
MAX_ACTIONS = 10            # 한 작업에서 실제 실행할 수 있는 변경 수
MAX_PROPOSALS = MAX_ACTIONS * (MAX_REJECTS + 1)
MAX_ADJUST_TURNS = int(os.getenv("MAX_ADJUST_TURNS", "64"))
execution_store = None      # lifespan injects the durable agent-only journal
app_graph = None            # lifespan이 넣는다. 이어 묻기에서 앞 작업의 저장된 State를 읽는다 (decisions 6.44)

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

llm, fallback_llm = M.make_models()


# 구조화 출력은 function_calling 방식으로 받는다. 기본(json_schema)은 gpt-4o-mini에서 날짜 값 뒤에
# JSON 조각이 섞여 나오는 일이 있었다 ("2026-11-25}],", 평가셋 SC1·SC3). 검사가 막긴 했지만 매번 재시도하게 된다.
STRUCTURED = "function_calling"


class LimitExceeded(Exception):
    """Harness Agent Loop 한도 초과. run 쪽에서 FAILED(LOOP_LIMIT)로 바꾼다."""


class ToolUnavailable(Exception):
    """작업을 시작하는 데 꼭 필요한 도구(생산 계획 조회)를 못 쓸 때. run이 FAILED(TOOL_UNAVAILABLE)로 바꾼다."""


def next_turn(state: dict) -> int:
    turns = state.get("turn_count", 0) + 1
    limit = MAX_ADJUST_TURNS if state.get("intent") == "ADJUST" else MAX_TURNS
    if turns > limit:
        raise LimitExceeded(f"모델 호출이 {limit}회를 넘었습니다.")
    return turns


# ---------------------------------------------------------------------------
# State — 노드끼리 주고받는 값. checkpointer가 단계마다 저장한다 (재개에 필요한 값은 전부 여기)
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    coordination: dict | None
    receipt_delay: dict | None
    plan_changes: list[dict]
    messages: Annotated[list, add_messages]   # 대화 (이어 붙임)
    task_id: str
    thread_id: str
    trace_id: str                            # 최초 실행 ID. 재개 회차의 ID는 observability 컨텍스트에서 관리
    user_id: str | None
    role: str | None                          # PLANNER | BUYER | APPROVER (금액 숨김·ADJUST 권한)
    question: str
    plans: list[dict]                         # 생산 계획 표 (plan_id → 제품 이름)
    intent: str                               # IMPACT | ADJUST | QA | CLARIFY
    whatif_ops: list[dict]                    # 가정 변경 (contracts 2.2 형식)
    whatif_source: str                        # agent(질문에서 뽑음) | backend(자동 작업, contracts 2.6)
    parent_thread_id: str | None              # 이어서 묻는 질문이면 앞 작업의 thread (backend가 소유자 확인 후 넘김)
    clarify: str | None                       # 되물을 내용
    clarification: dict | None                # 사용자 안내: 단계·대상·문제 필드·다음 행동
    classification_attempts: list[dict]        # 검증에 실패한 추출값도 체크포인트에 보존
    notice: str | None                        # 답변 앞에 붙일 안내 (예: 권한 때문에 IMPACT로 답함)
    inherited: str | None                     # 이어 묻기로 앞 작업에서 이어받은 가정 (답변 맨 앞에 밝힌다, 6.44)
    failure: str | None                       # 도구 오류로 끝낼 때
    impact: dict | None                       # analyze_impact 결과 (변경 적용 후, 해결안 적용 전)
    extend: list[dict]                        # 지연이 더 길어질 수 있는 발주 [{po_id, base_due}] (6.50)
    scenarios: list[dict]                     # 장기화 대비 1·4·12주 결과 [{weeks, delay_days, due_dates, impact}]
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
    proposals: list[dict]                    # immutable option/verification + per-card lifecycle
    active_proposal: str | None
    replan_target: str | None
    replan_constraint: dict | None            # 팀장이 재요청하며 정한 {po_id?, new_due_date?, split_qty?} (decisions 6.47)
    processed_commands: list[str]            # inbox replay after a checkpoint/worker crash
    workflow_version: int
    batch_context: dict
    batch_revision: int
    batch_status: str
    batch_error: str | None
    batch_last_request: str | None
    batch_last_command: dict | None
    manager_reviews: dict


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
    event = O.enrich(event)
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
    with O.tool_span(name, args) as report:
        ok, result = await _call_tool(state, name, args, label=label, quiet=quiet)
        report.update(ok=ok, errorCode=None if ok else ("UPSTREAM_TIMEOUT" if "UPSTREAM_TIMEOUT" in str(result) else "TOOL_ERROR"))
        return ok, result


async def _call_tool(state: dict, name: str, args: dict, *, label: str | None = None,
                     quiet: bool = False) -> tuple[bool, Any]:
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


async def load_pos(state: dict) -> list[dict]:
    """열린 발주 표: 발주 번호 없이 "세미온반도체 MCU"라고 물어도 고를 수 있게, 기간을 날짜로 바꿀 때도 쓴다 (6.50·6.51)."""
    ok, res = await call_tool(state, "list_receipts", {"status": "OPEN"}, label="발주 입고 예정일 조회 중")
    if not ok:
        raise ToolUnavailable(res)
    pos = []
    for po in res["purchase_orders"]:
        if po.get("status") != "OPEN":
            continue
        iid = po["item_id"]
        if po.get("item_name"):
            _product_names[iid] = po["item_name"]
        if iid not in _product_names:   # 품목 이름도 제품 이름과 같은 캐시를 쓴다
            ok2, item = await call_tool(state, "get_item", {"item_id": iid}, quiet=True)
            _product_names[iid] = item["name"] if ok2 else iid
        pos.append({"po_id": po["po_id"], "item_id": iid, "item_name": _product_names[iid],
                    "supplier_name": po.get("supplier_name"), "qty": po["qty"], "due_date": po["due_date"],
                    "status": po["status"]})
    return pos


def pos_table(pos: list[dict]) -> str:
    rows = [f"| {p['po_id']} | {p['item_name']} ({p['item_id']}) | {p.get('supplier_name') or '-'} | {p['qty']}개 | {p['due_date']} |"
            for p in pos]
    return "\n".join(["| po_id | 품목 | 협력사 | 수량 | 입고 예정일 |", "|---|---|---|---|---|", *rows])


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


def describe_shortage(s: dict, plans: list[dict], basis: bool = False) -> str:
    when = "지금 발주하면 제때 도착" if s.get("orderable") else "새 발주로는 늦음, 기존 발주 조정 필요"
    line = (f"{plan_label(plans, s.get('plan_id'), s.get('plan_date'))}: {item_label(s)} "
            f"{abs(s['projected_stock']):,}개 부족 · {when}")
    return line + (f" ({basis_text(s['basis'], plans)})" if basis and s.get("basis") else "")


def basis_text(b: dict, plans: list[dict]) -> str:
    """결품 근거: 재고 + 그날까지 입고 − 그날까지 소요(생산별). backend 계산을 그대로 옮긴다 (decisions 6.50)"""
    names = {p["plan_id"]: p["product_name"] for p in plans}
    uses = " · ".join(f"{names.get(u['plan_id'], u['plan_id'])} {u['plan_id']} {u['qty']:,}" for u in b["uses"])
    received = sum(r["qty"] for r in b["receipts"])
    return f"재고 {b['on_hand']:,} + 입고 {received:,} − 소요 {sum(u['qty'] for u in b['uses']):,} [{uses}]"


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
        ("결품", [describe_shortage(s, plans, basis=True) for s in impact.get("shortages", [])]),
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

async def classify_question(question: str, plans: list[dict], turns: int = 0,
                            previous: dict | None = None, receipts: list[dict] | None = None,
                            diagnostics: dict | None = None, pos: list[dict] | None = None) -> tuple[W.Classification, list[str], int]:
    """질문 → (분류, 검증 오류, 모델 호출 수). 평가 스크립트도 이 함수를 그대로 쓴다.

    모델이 뽑은 whatif가 검사를 통과하지 못하면 오류를 알려 주고 한 번 더 기회를 준다.
    previous는 이어 묻는 질문일 때 앞 작업의 질문·가정이다. 모델은 이번 질문에서 새로 바뀐 것만 뽑고
    follow_up을 정한다. 앞 가정과 합치는 건 classify_intent의 코드가 한다.
    """
    # 이어 묻기에서 모델이 앞 가정을 다시 적어도 그 날짜는 질문에 있던 것이다
    inherited_dates = [o[a] for o in (previous or {}).get("whatif_ops") or [] for a in W.DATE_ARGS if o.get(a)]
    inherited_pos = {o["po_id"] for o in (previous or {}).get("whatif_ops") or [] if o.get("po_id")}
    pos = pos if pos is not None else receipts or []
    issues = []

    def fail(code, message, **details):
        issues.append(F.issue(code, message, **details))
        return [message]

    def record(c, errors):
        if not issues and (errors or c.unclear):
            issues.extend(F.validation_issue(e) for e in errors)
            if not errors:
                issues.append(F.issue("MISSING_INFORMATION", c.unclear, kind="NEEDS_INPUT",
                                      action="위에서 확인을 요청한 정보만 추가로 알려 주세요."))
        if diagnostics is not None:
            extracted = c.model_dump()
            diagnostics.setdefault("attempts", []).append({"extracted": extracted, "issues": list(issues)})
            diagnostics["clarification"] = F.feedback(list(issues), extracted) if issues else None

    def check(c: W.Classification) -> list[str]:
        issues.clear()
        calculated_ops = []
        def as_mention(op):
            return D.DelayMention(po_id=op.po_id, new_due_date=op.new_due_date,
                                  delay_days=op.delay_days, may_extend=op.may_extend)

        # main's whatif delay fields and rag's item/date mentions share the same
        # receipt lookup, duration guard and diagnostics. Keep scenario metadata.
        if receipts is not None and not c.receipt_delays:
            legacy = [o for o in c.whatif if o.op == "request_due_date_change"]
            c.receipt_delays = [as_mention(o) for o in legacy]
            c.whatif = [o for o in c.whatif if o.op != "request_due_date_change"]
        if receipts is not None and c.intent != "QA" and not c.receipt_delays and not c.follow_up:
            return fail("MISSING_EXTRACTION", "질문에서 입고 지연 조건을 추출하지 못했습니다.",
                        field="receipt_delays")
        if c.receipt_delays:
            if receipts is None:
                return fail("RECEIPTS_UNAVAILABLE", "입고 지연을 계산할 발주 정보를 조회하지 못했습니다.")
            # Do not let a model pick one PO from an ambiguous item/date query.
            mentions = [d if not d.po_id or d.po_id.upper() in question.upper()
                        else d.model_copy(update={"po_id": None}) for d in c.receipt_delays]
            resolved, errors = D.resolve(mentions, receipts, W.BASE_DATE, issues=issues)
            if errors:
                return errors
            # A model can split different POs across the two supported formats.
            ids = {o['po_id'] for o in resolved}
            extra = [o for o in c.whatif if o.op == "request_due_date_change" and o.po_id not in ids]
            if extra:
                c.receipt_delays += [as_mention(o) for o in extra]
                c.whatif = [o for o in c.whatif if o not in extra]
                mentions += [as_mention(o).model_copy(update={"po_id": o.po_id if o.po_id and o.po_id.upper() in question.upper() else None}) for o in extra]
                resolved, errors = D.resolve(mentions, receipts, W.BASE_DATE, issues=issues)
                if errors:
                    return errors
            for mention, op in zip(mentions, resolved):
                # A stated absolute date is sufficient. The model may also have
                # calculated its day difference; resolve() already checked agreement.
                # Only duration-only dates need an exemption from the date guard.
                if mention.delay_days is not None and not mention.new_due_date:
                    if mention.delay_days not in D.stated_delay_days(question):
                        row = next(r for r in receipts if r["po_id"] == op["po_id"])
                        return fail("UNSTATED_DELAY", f"질문에 없는 지연 기간을 AI가 {mention.delay_days}일로 해석했습니다. 현재 입고일은 {row['due_date']}입니다.",
                                    target=f"{row.get('item_name', row['item_id'])} · {op['po_id']}",
                                    field="delay_days", value=mention.delay_days,
                                    action="새 입고일을 이미 적었다면 같은 질문을 다시 시도해 주세요. 날짜·기간이 없다면 정확한 새 입고일 또는 지연 기간을 알려 주세요.")
                    calculated_ops.append(op)
            # Same PO must have one exact condition; never silently overwrite conflicting extraction.
            known = {o['po_id']: o for o in resolved}
            extension = {o['po_id']: d.may_extend for d, o in zip(mentions, resolved)}
            kept = []
            for op in c.whatif:
                if op.po_id in known:
                    duplicate = W.normalize(op)
                    if op.op == "request_due_date_change":
                        duplicates, errors = D.resolve([as_mention(op)], receipts, W.BASE_DATE, issues=issues)
                        if errors:
                            return errors
                        duplicate = duplicates[0]
                    if duplicate != known[op.po_id]:
                        return fail("CONFLICTING_EXTRACTION", f"동일 발주의 새 입고일을 {op.new_due_date}와 {known[op.po_id]['new_due_date']}로 다르게 해석했습니다.",
                                    target=op.po_id, field="new_due_date", value=op.new_due_date)
                    extension[op.po_id] = extension[op.po_id] or op.may_extend
                else:
                    kept.append(op)
            c.whatif = kept + [W.WhatifOp(**o, delay_days=d.delay_days if not d.new_due_date else None,
                                         may_extend=extension[o['po_id']]) for d, o in zip(mentions, resolved)]
        ops = [W.normalize(o) for o in c.whatif]
        # Exempt only the exact PO/date computed from a stated duration and an actual receipt.
        # An unrelated plan or PO with the same date must still pass main's date guard.
        stated_ops = [o for o in ops if o not in calculated_ops]
        errors = W.consistency_errors(question, [], plans) if not ops else []
        issues.extend(F.validation_issue(e) for e in errors)
        for op in ops:
            found = (W.validate_ops([op], plans) or W.consistency_errors(question, [op], plans)
                     or (W.unstated_date_errors(question, [op], inherited_dates) if op in stated_ops else [])
                     or W.po_errors(question, [op], pos, inherited_pos))
            errors.extend(found)
            issues.extend(F.validation_issue(e, op) for e in found)
        if not errors and ops and c.unclear:
            return fail("CONFLICTING_CLARIFICATION",
                        "변경 조건은 추출했지만 AI가 추가 확인이 필요하다고도 판단했습니다. 확인 요청 내용: " + c.unclear,
                        target=", ".join(dict.fromkeys(o.get("po_id") or o.get("plan_id") or "미지정" for o in ops)),
                        field="unclear", value=c.unclear,
                        action="표시된 확인 요청이 질문에 이미 포함되어 있다면 같은 질문을 다시 시도해 주세요. 빠진 내용이 있다면 그 부분만 추가해 주세요.")
        return errors

    if llm is None:
        try:
            c = W.rule_classify(question, plans)
        except ValueError:
            c = W.Classification(intent="IMPACT", unclear="날짜를 읽지 못했습니다. 실제 월/일을 확인해 주세요.")
        if receipts is not None and D.relevant(question):
            try:
                c.receipt_delays = D.fake_mentions(question, receipts, W.BASE_DATE)
            except ValueError:
                errors = ["입고 날짜를 읽지 못했습니다. 실제 월/일을 확인해 주세요."]
                record(c, errors)
                return c, errors, turns
            c.whatif = [o for o in c.whatif if o.op != "request_due_date_change"]
            if c.receipt_delays and not c.whatif:
                c.unclear = None
        errors = check(c)
        record(c, errors)
        return c, errors, turns
    msgs = [SystemMessage(prompt("classify", BASE_DATE=W.BASE_DATE.isoformat(), PLANS=plans_table(plans),
                                 POS=pos_table(pos) if pos else "(없음)"))]
    if receipts is not None:
        table = [{k: r.get(k) for k in ('po_id', 'item_id', 'item_name', 'due_date', 'qty', 'status')} for r in receipts]
        msgs.append(SystemMessage(prompt("classify_receipt_delay", RECEIPTS=json.dumps(table, ensure_ascii=False))))
    if previous:
        msgs.append(SystemMessage(prompt("classify_follow_up", PREV_QUESTION=previous["question"],
                                         PREV_WHATIF=json.dumps(previous["whatif_ops"], ensure_ascii=False))))
    msgs.append(HumanMessage(question))
    if W.explicit_adjust_request(question):
        msgs.append(SystemMessage(prompt("classify_adjust")))
    for attempt in range(2):
        turns += 1
        c = await M.invoke(llm, fallback_llm, msgs, schema=W.Classification)
        if c.intent == "IMPACT" and W.explicit_adjust_request(question):
            # 실제 SC2에서 긴 가정 뒤 "해결 방법 만들어 줘"를 놓쳤다. 명시적 요청의 분기만 보완한다.
            # 모델의 whatif는 그대로 검사하며, 역할 제한과 개별 승인은 이후 단계에서 적용한다.
            c = c.model_copy(update={"intent": "ADJUST"})
        errors = check(c)
        record(c, errors)
        if not errors:
            return c, [], turns
        msgs += [AIMessage(c.model_dump_json()),
                 HumanMessage("whatif 검사 실패: " + "; ".join(errors) + "\n" + prompt("classify_retry"))]
    return c, errors, turns


async def classify_intent(state: AgentState) -> dict:
    plans = await load_plans(state)
    out = {"plans": plans}
    coordination = state.get("coordination") or {}
    if coordination.get("mode") == "SOLVE":
        ops = [W.normalize(o) for o in coordination.get("planOps") or []]
        errors = W.validate_ops(ops, plans) + W.coordination_errors(coordination, ops)
        if coordination.get("direction") != "PLAN_TO_BUY" or state.get("role") != "BUYER":
            errors.append("구매 담당자의 연결된 해결 요청만 실행안을 만들 수 있습니다.")
        if errors:
            return {**out, "intent": "CLARIFY", "clarify": "; ".join(errors)}
        return {**out, "intent": "ADJUST", "whatif_ops": ops,
                "notice": "생산관리가 동의한 날짜·수량을 유지하여 해결안을 찾습니다. 최종 실행 시 발주와 생산계획을 함께 반영합니다."}
    # 자동 작업(SC4 입고 지연 입력 후)은 backend가 whatif를 채워 보낸다. 모델이 추측할 이유가 없다 (contracts 2.6)
    if state.get("whatif_source") == "backend":
        ops = [W.normalize(o) for o in state.get("whatif_ops") or []]
        errors = W.validate_ops(ops, plans)
        if errors:
            return {**out, "intent": "CLARIFY", "clarify": "자동 작업의 가정 변경이 올바르지 않습니다: " + "; ".join(errors)}
        return {**out, "intent": "IMPACT", "whatif_ops": ops}

    previous = await previous_turn(state)
    receipt_question = D.relevant(state["question"]) and not coordination
    # Main's PO table and rag's receipt validation use the same OPEN snapshot.
    pos = await load_pos(state) if llm is not None or receipt_question else []
    receipts = pos if receipt_question else None
    diagnostics = {}
    c, errors, turns = await classify_question(state["question"], plans, state.get("turn_count", 0), previous, diagnostics=diagnostics, pos=pos,
                                               **({"receipts": receipts} if receipts is not None else {}))
    if turns > MAX_TURNS:
        raise LimitExceeded(f"모델 호출이 {MAX_TURNS}회를 넘었습니다.")
    out["turn_count"] = turns
    out["classification_attempts"] = diagnostics.get("attempts", [])
    if errors or c.unclear:
        return {**out, "intent": "CLARIFY", "clarify": "; ".join(errors) or c.unclear,
                "clarification": diagnostics.get("clarification")}
    ops = W.mark_extension(state["question"], [o.model_dump(exclude_none=True) for o in c.whatif])
    extend = []
    if any(o.get("delay_days") or o.get("may_extend") for o in ops):
        # "약 1주 늦어진다" → 원래 입고 예정일 + 7일, "더 길어지면" → 1·4·12주 비교 대상 (decisions 6.50)
        due_dates = {p["po_id"]: p["due_date"] for p in pos}
        ops, extend, errors = W.resolve_delays(ops, due_dates)
        if errors:
            return {**out, "intent": "CLARIFY", "clarify": "; ".join(errors)}
    ops = [W.normalize(o) for o in ops]
    intent = c.intent
    notices, inherited = [], None
    # 이어 묻기 (decisions 6.44): "그럼 해결 방법 만들어 줘"처럼 변경을 다시 말하지 않거나 "거기에 …"처럼 덧붙이면
    # 앞 작업의 가정에 이번 변경을 얹는다. 새 주제(follow_up 아님)는 앞 가정을 쓰지 않는다
    if previous and previous["whatif_ops"] and intent in ("IMPACT", "ADJUST") and (c.follow_up or (intent == "ADJUST" and not ops)):
        kept = [o for o in previous["whatif_ops"] if W.op_target(W.normalize(o)) not in {W.op_target(n) for n in ops}]
        ops = W.merge_ops(previous["whatif_ops"], ops)
        # 장기화 비교도 이어받는다: "그럼 12주까지 가면?"에 1·4·12주를 다시 보인다 (decisions 6.51, 변형 V8)
        extend = extend or [e for e in previous.get("extend") or [] if e["po_id"] in {o.get("po_id") for o in ops}]
        if kept:
            # 가정은 저장되지 않는다 — 무엇을 이어받았는지 답변에 밝힌다 (안전성 재현 1·3)
            inherited = "앞 질문의 가정을 이어서 계산했습니다: " + ", ".join(op_text(o, plans) for o in kept)
    if intent == "ADJUST" and state.get("role") not in ("BUYER", "APPROVER"):
        # 권한표: 발주 조정 요청은 구매·팀장만 (A2). 역할을 모르면 가장 좁은 권한으로 본다
        intent = "IMPACT"
        notices.append("발주 조정 요청은 구매 담당자·팀장만 할 수 있어 영향 분석으로 답합니다.")
    if coordination:
        if coordination.get("direction") == "PLAN_TO_BUY" and intent != "QA":
            if not ops:
                ops = [W.normalize(o) for o in coordination.get("planOps") or []]
            errors = W.coordination_errors(coordination, ops)
            if errors:
                return {**out, "intent": "CLARIFY", "clarify": "; ".join(errors)}
            intent = "IMPACT"
            notices.append("추가 질문은 분석만 합니다. 실행은 부서 요청에 연결된 기존 해결 작업에서 진행하세요.")
        elif coordination.get("direction") == "BUY_TO_PLAN" and intent != "QA":
            errors = W.coordination_errors(coordination, ops)
            if errors:
                return {**out, "intent": "CLARIFY", "clarify": "; ".join(errors)}
            intent = "IMPACT"
            notices = ["확정된 입고 지연을 기준으로 분석했습니다. 생산계획은 부서 요청함에서 변경안을 검증한 뒤 확정하세요."]
    # notice는 해결안 실행 뒤 다시 쓰이므로(refresh_impact) 이어받은 가정은 따로 둔다
    delay = D.snapshot(state["question"], ops, receipts) if receipts is not None and intent == "IMPACT" else None
    if delay:
        dates = ", ".join(f"{f['poId']} {f['before']['due_date']} → {f['after']['due_date']}" for f in delay['changes'])
        notices.append(f"입고일 {dates} 기준 분석입니다. 아직 실제 입고일은 변경하지 않았습니다.")
    return {**out, "intent": intent, "whatif_ops": ops, "notice": " / ".join(notices) or None, "inherited": inherited,
            "receipt_delay": delay, "extend": extend if intent == "IMPACT" else []}


MAX_PREVIOUS_TURNS = 3   # 가정을 찾아 거슬러 올라가는 앞 질문 수


async def previous_turn(state: AgentState) -> dict | None:
    """앞 작업의 질문과 (이어받은 것까지 합친) 가정. 앞 작업이 승인 대기 중이어도 저장된 State에서 읽는다.

    바로 앞이 규정 질문(QA)이나 되묻기(CLARIFY)처럼 가정을 다루지 않는 질문이면 그 앞으로 거슬러 올라간다
    (decisions 6.48). "허브 당기면?" → "규정이 뭐야?" → "그럼 해결 방법"에서 허브 가정을 잃지 않게.
    영향·해결 질문인데 가정이 비었으면("지금 계획대로 가면?") 그 질문이 곧 '지금 상태'라는 뜻이라 멈춘다.
    """
    parent = state.get("parent_thread_id")
    for _ in range(MAX_PREVIOUS_TURNS):
        if not parent or app_graph is None:
            return None
        values = (await app_graph.aget_state({"configurable": {"thread_id": parent}})).values
        if not values:
            return None
        if values.get("whatif_ops") or values.get("intent") not in ("QA", "CLARIFY"):
            return {"question": values.get("question", ""), "whatif_ops": values.get("whatif_ops") or [],
                    "extend": values.get("extend") or []}
        parent = values.get("parent_thread_id")
    return None


def op_text(op: dict, plans: list[dict]) -> str:
    """가정 변경 한 줄. 답변에 이어받은 가정을 밝힐 때 쓴다."""
    o = W.normalize(op)
    plan = plan_label(plans, o.get("plan_id")) if o.get("plan_id") else None
    return {
        "reschedule_plan": lambda: f"{plan} → {o.get('new_date')}",
        "change_plan_qty": lambda: f"{plan} → {o.get('new_qty'):,}대",
        "request_due_date_change": lambda: f"{o.get('po_id')} 입고 → {o.get('new_due_date')}",
        "split_po": lambda: f"{o.get('po_id')}에서 {o.get('split_qty'):,}개를 {o.get('new_due_date')}에 먼저",
        "change_po_qty": lambda: f"{o.get('po_id')} → {o.get('new_qty'):,}개",
        "cancel_po": lambda: f"{o.get('po_id')} 취소",
    }.get(o["op"], lambda: json.dumps(o, ensure_ascii=False))()


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
    msg = await M.invoke(llm, fallback_llm, [system, *state["messages"]], tools=[analyze_impact_tool])
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
                    if state.get("extend"):
                        updates["scenarios"] = await delay_scenarios(state)
            body = mask(res, state.get("role"))
            if updates.get("scenarios"):
                body = {**body, "scenarios": scenario_summary(updates["scenarios"], state["plans"])}
            content = json.dumps(body, ensure_ascii=False) if ok else res
        else:
            content = f"이 경로에서 쓸 수 없는 도구입니다: {call['name']}"
        out_msgs.append(ToolMessage(tool_content(content), tool_call_id=call["id"], name=call["name"]))
    return {"messages": out_msgs, "tool_counts": counts, **updates}


async def delay_scenarios(state: dict) -> list[dict]:
    """장기화 대비: 대상 발주만 원래 입고 예정일 + 1·4·12주로 바꿔 각각 계산한다. 모델이 아니라 코드가 돈다 (6.50)."""
    out = []
    for days in W.SCENARIO_DAYS:
        ops = W.scenario_ops(state.get("whatif_ops") or [], state["extend"], days)
        ok, res = await call_tool(state, "analyze_impact", {"whatif": ops}, quiet=True)
        if not ok:
            return []   # 비교를 못 해도 통보 기준 분석은 그대로 답한다
        due = {o["po_id"]: o["new_due_date"] for o in ops if o.get("po_id") in {e["po_id"] for e in state["extend"]}}
        out.append({"weeks": days // 7, "delay_days": days, "due_dates": due, "impact": res})
    return out


def shortage_key(s: dict) -> tuple:
    return s.get("item_id"), s.get("plan_id"), s.get("plan_date")


def scenario_summary(scenarios: list[dict], plans: list[dict]) -> list[str]:
    """기간마다 한 줄: 입고일, 결품 수, 앞 기간보다 새로 생긴 결품. "어디부터 무너지나"를 계산으로 보여 준다"""
    lines, seen = [], set()
    for sc in scenarios:
        shortages = sc["impact"].get("shortages", [])
        new = [s for s in shortages if shortage_key(s) not in seen]
        seen |= {shortage_key(s) for s in shortages}
        due = ", ".join(f"{po} 입고 {d}" for po, d in sc["due_dates"].items())
        head = f"{sc['weeks']}주 지연({due}): 결품 {len(shortages)}건"
        brief = lambda xs: ", ".join(f"{plan_label(plans, s.get('plan_id'), s.get('plan_date'))} {item_label(s)} "
                                     f"{abs(s['projected_stock']):,}개" for s in xs)
        if not shortages:
            lines.append(head)
        elif sc is scenarios[0]:
            lines.append(f"{head} · {brief(shortages)}")
        else:
            lines.append(head + (f" · 새로 {brief(new)}" if new else " · 새로 생긴 결품 없음"))
    return lines


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
    msg = await M.invoke(llm, fallback_llm, [system, *state["messages"]], tools=[search_docs_tool, read_doc_tool])
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
        out_msgs.append(ToolMessage(tool_content(content), tool_call_id=call["id"], name=call["name"]))
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
    """모든 대상의 안을 먼저 준비한다. 아직 승인되지 않은 안을 DB 가정에 합치지 않는다."""
    prepared = {p["target"] for p in state.get("proposals") or []}
    replan = state.get("replan_target")
    pending = [t for t in W.actionable(state.get("current_impact") or {})
               if W.target_key(t) in state["scope"] and W.target_key(t) not in (state.get("blocked") or {})
               and (W.target_key(t) == replan if replan else W.target_key(t) not in prepared)]
    if not pending:
        return {"active_target": None, "targets": [], "selected_option": None, "replan_target": None}
    key = W.target_key(pending[0])
    targets = [t for t in pending if W.target_key(t) == key]
    if (len(prepared) >= MAX_ACTIONS and not replan) or len(state.get("proposals") or []) >= MAX_PROPOSALS:
        return {"active_target": key, "targets": targets, "options": [], "selected_option": None,
                "evidence_error": "작업당 제안 한도에 도달했습니다. 남은 대상은 새 작업으로 요청하세요."}
    constraint = state.get("replan_constraint") if replan else None
    ok, res = await call_tool(state, "compare_options",
                              {"whatif": state["current_ops"], "issues": targets, **({"constraint": constraint} if constraint else {})})
    if not ok:
        return {"active_target": key, "targets": targets, "options": [], "selected_option": None,
                "evidence_error": "대안 조회 실패: " + str(res)}
    options = [o for o in res.get("options", []) if W.target_key(o) == key]
    constraint_error = None
    if constraint and not any(o.get("resolves") and not o.get("side_effects") for o in options):
        # 요청 조건으로는 못 푼다: 왜 안 되는지(리드타임상 가장 빠른 가능일 등)와 남는 문제를 팀장에게 그대로 돌려준다
        left = next((o.get("remaining_issues") or o.get("side_effects") for o in options), None)
        constraint_error = " ".join(["요청 조건(" + constraint_text(constraint) + ")으로는 해결안을 만들 수 없습니다.",
                                     *res.get("constraint_notes", []),
                                     *(["남는 문제: " + json.dumps(left, ensure_ascii=False)] if left else [])])
    return {"active_target": key, "targets": targets, "options": options,
            "recommended": next((o["option_id"] for o in options if o.get("recommended")), None),
            "selected_option": None, "decision": None, "verify": None, "verify_count": 0,
            "evidence_error": constraint_error,
            "reject_count": sum(p["target"] == key and p["status"] == "REJECTED" for p in state.get("proposals") or [])}


def after_compare(state: AgentState) -> str:
    return "check_batch" if not state.get("active_target") else "agent_adjust"


class Pick(BaseModel):
    option_id: str = Field(description="고른 후보의 option_id. 후보 목록에 있는 값만")
    reason: str = Field(description="왜 골랐는지 한두 문장")


def constraint_text(c: dict) -> str:
    """팀장 조건 한 줄: 발주 · 날짜 · 수량 중 정한 것만."""
    parts = [c.get("po_id"), c.get("new_due_date"), f"{c['split_qty']:,}개" if c.get("split_qty") else None]
    return " · ".join(str(x) for x in parts if x)


def candidates(state: AgentState) -> list[dict]:
    tried = set(state.get("tried") or [])
    # A shared PO cannot have two independently approved mutations in flight.
    reserved = {p["option"]["args"].get("po_id") for p in state.get("proposals") or []
                if p["status"] not in ("REJECTED", "BLOCKED", "EXECUTED")}
    return [o for o in state.get("options") or [] if o.get("feasible") and o.get("resolves")
            and not o.get("side_effects") and W.option_key(o) not in tried
            and o.get("args", {}).get("po_id") not in reserved]


async def agent_adjust(state: AgentState) -> dict:
    """후보 중 **option_id 하나만** 고른다. 실행 인자는 코드가 후보에서 그대로 복사한다 (decisions 6.3)."""
    cands = candidates(state)
    if not cands or state.get("evidence_error"):
        return {"selected_option": None}
    turns = state.get("turn_count", 0)
    if state.get("replan_target") and state.get("replan_constraint"):
        # 팀장 조건 재제안: 후보가 이미 그 조건으로만 계산돼 있다. 모델이 고르지 않고 계산 순서의 첫 안을 쓴다
        return {"selected_option": cands[0], "turn_count": turns,
                "pick_reason": "팀장 요청 조건(" + constraint_text(state["replan_constraint"]) + ")을 반영했습니다."}
    if llm is None:
        rec = state.get("recommended")
        pick = Pick(option_id=rec if rec in {o["option_id"] for o in cands} else cands[0]["option_id"],
                    reason="[가짜 모델] 계산 서비스의 추천안을 골랐습니다.")
    else:
        try:
            turns = next_turn(state)
        except LimitExceeded as e:
            return {"selected_option": None, "evidence_error": str(e)}
        context = {
            "targets": state["targets"],
            "candidates": mask(cands, state.get("role")),
            "recommended_option_id": state.get("recommended"),
            "verify_failures": [v for v in state.get("verify_log") or [] if not v.get("passed")],
            "rejections": state.get("rejections") or [],
        }
        try:
            pick = await M.invoke(llm, fallback_llm,
                [SystemMessage(prompt("adjust")), HumanMessage(json.dumps(context, ensure_ascii=False))], schema=Pick)
        except Exception as e:
            return {"selected_option": None, "turn_count": turns, "evidence_error": f"대안 선택 실패: {type(e).__name__}"}
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
    return "skip_target" if state.get("evidence_error") else "save_proposal"


# 거절 뒤 재검토 결과 (decisions 6.30). 거절한 제안에 붙여 진행 이벤트·작업 결과로 함께 나간다.
# 코드는 사람이 다음에 무엇을 할지가 갈리는 단위로 나눈다: 새 카드 확인 / 조건 검토 / 다시 요청 / 직접 결정
OUTCOMES = {
    "REPROPOSED": ("다른 대안을 다시 제안했습니다.", "바로 아래 새 승인 카드를 확인해 주세요."),
    "NO_ALTERNATIVE": ("현재 조건에서 제시할 수 있는 추가 대안을 찾지 못했습니다.",
                       "거절 사유의 조건(수량·날짜 등)을 담당자가 검토해 주세요. 조건을 바꿔 새로 요청할 수 있습니다."),
    "REVIEW_FAILED": ("다른 대안을 검토하는 중 오류가 나서 결론을 내지 못했습니다. 대안이 없다는 뜻은 아닙니다.",
                      "잠시 뒤 같은 요청을 다시 해 주세요. 계속되면 시스템 담당자에게 알려 주세요."),
    "LIMIT": ("재제안 한도에 도달해 더 제안하지 않습니다.", "담당자가 조건을 정해 새로 요청해 주세요."),
    "WITHDRAWN": ("요청자가 승인 요청을 보내지 않았습니다.", "필요하면 조건을 바꿔 새로 요청해 주세요."),
}


def target_name(state: dict, target: str) -> str:
    """'INV-003|CASE-DOOR-V1' → 부품 이름. 영향 결과의 item_name을 쓰고, 없으면 ID 그대로"""
    item = target.split("|", 1)[-1]
    impact = state.get("current_impact") or state.get("impact") or {}
    for key in ("shortages", "warnings", "excesses"):
        for e in impact.get(key) or []:
            if e.get("item_id") == item and e.get("item_name"):
                return e["item_name"]
    return item


def with_outcome(state: dict, target: str, code: str, detail: str | None = None) -> list[dict] | None:
    """이 대상의 마지막 거절 제안에 재검토 결과를 붙인 proposals. 거절이 없던 대상이면 None"""
    proposals = [dict(p) for p in state.get("proposals") or []]
    rejected = [p for p in proposals if p["target"] == target and p["status"] == "REJECTED"]
    if not rejected:
        return None
    message, next_action = OUTCOMES[code]
    rejected[-1]["outcome"] = {"code": code, "item_name": target_name(state, target),
                               "message": message, "next_action": next_action, "detail": detail}
    return proposals


def outcome_code(state: dict) -> str:
    error = state.get("evidence_error") or ""
    if error.startswith("작업당 제안 한도") or state.get("reject_count", 0) > MAX_REJECTS:
        return "LIMIT"
    if error.startswith("요청 조건("):   # 팀장 조건으로는 못 푼다: 오류가 아니라 대안 없음, 이유는 detail로 (6.47)
        return "NO_ALTERNATIVE"
    return "REVIEW_FAILED" if error else "NO_ALTERNATIVE"


def skip_target(state: AgentState) -> dict:
    blocked = dict(state.get("blocked") or {})
    code, detail = outcome_code(state), state.get("evidence_error")
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
    updates = {"blocked": blocked, "selected_option": None, "decision": None, "evidence_error": None,
               "replan_target": None, "replan_constraint": None}
    proposals = with_outcome(state, state["active_target"], code, detail)
    if proposals is not None:
        updates["proposals"] = proposals
    return updates


def save_proposal(state: AgentState) -> dict:
    proposals = with_outcome(state, state["active_target"], "REPROPOSED") or list(state.get("proposals") or [])
    previous = [p for p in proposals if p["target"] == state["active_target"]]
    revision = len(previous) + 1
    proposal_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{state['task_id']}:{state['active_target']}:{revision}").hex
    proposals.append({"proposal_id": proposal_id, "revision": revision, "target": state["active_target"],
                      "replaces": previous[-1]["proposal_id"] if previous else None,
                      "previous_approval_id": previous[-1].get("approval_id") if previous else None,
                      "option": state["selected_option"], "targets": state["targets"],
                      "verify": state["verify"], "pick_reason": state.get("pick_reason"),
                      "status": "PREPARED", "approval_id": None})
    return {"proposals": proposals, "replan_target": None, "replan_constraint": None}


async def check_batch(state: AgentState) -> dict:
    """각각 검증한 안들의 동시 적용도 검사한다. 부분 승인에는 실행 직전 검증을 별도로 적용한다."""
    proposals = [dict(p) for p in state.get("proposals") or []]
    new = [p for p in proposals if p["status"] == "PREPARED"]
    if not new:
        return {}
    batch = [p for p in proposals if p["status"] in ("PREPARED", "PENDING")]
    ok, after = await call_tool(state, "analyze_impact", {
        "whatif": [*state["current_ops"], *(W.option_op(p["option"]) for p in batch)]}, label="여러 해결안 함께 검증 중")
    verdict = W.judge(state["current_impact"], after, [t for p in batch for t in p["targets"]]) if ok else {"passed": False}
    blocked = dict(state.get("blocked") or {})
    if not verdict["passed"]:
        for p in new:
            p.update(status="BLOCKED", reason="해결안 조합 검증에 실패하여 승인 요청을 보류했습니다.")
            blocked[p["target"]] = p["reason"]
    return {"proposals": proposals, "blocked": blocked}


def problem_text(targets: list[dict]) -> str:
    """승인 카드의 고정된 품목별 문제. 계산 결과에서 만들고 구매 담당자의 전체 문제 설명과 구분한다"""
    lines = []
    for t in targets:
        name = t.get("item_name") or t.get("item_id")
        if t.get("rule_id") == "INV-003":
            lines.append(f"{name} {t.get('qty', 0):,}개 과잉 ({t.get('date')} 기준, 앞으로 {t.get('window_days', 30)}일 쓸 양보다 많음)")
        else:
            when = "지금 발주하면 제때 도착" if t.get("orderable") else "새 발주로는 늦어 기존 발주 조정 필요"
            lines.append(f"{t.get('product_name') or t.get('plan_id')} 생산({t.get('plan_date')}, {t.get('plan_id')})에 "
                         f"{name} {t.get('qty', abs(t.get('projected_stock', 0))):,}개 부족 · {when}")
    return "; ".join(lines)


def approval_payload(p: dict) -> dict:
    opt = p["option"]
    previous = f"[재제안 {p['revision']}차 · 이전 요청 {p['previous_approval_id']}] " if p.get("previous_approval_id") else ""
    return {
        "tool": opt["tool"], "args": opt["args"],
        "summary": f"{previous}{opt.get('summary', '')} (실제 발주 변경입니다. 협력사 협의·확약 여부를 확인한 뒤 승인하세요.)",
        "amount": opt.get("added_cost"), "evidence": opt.get("evidence") or [],
        "problem": problem_text(p.get("targets") or []),
        "option_id": opt["option_id"], "proposalId": p["proposal_id"], "revision": p["revision"],
        "replaces": p.get("replaces"), "previousApprovalId": p.get("previous_approval_id"), "itemId": opt["item_id"],
        "verify": {"passed": p["verify"].get("passed", False), "warnings_after": p["verify"].get("warnings_after", [])},
    }


def proposal_event(p: dict, state: dict) -> dict:
    return mask({"type": "PROPOSAL_PROGRESS", "proposalId": p["proposal_id"],
                 "approvalId": p.get("approval_id"), "revision": p["revision"], "replaces": p.get("replaces"),
                 "itemId": p["option"]["item_id"], "summary": p["option"].get("summary"),
                 "proposalStatus": p["status"], "message": p.get("reason"), "outcome": p.get("outcome"),
                 "round": len(state.get("processed_commands", []))}, state.get("role"))


def register_proposals(state: AgentState) -> dict:
    # Checkpoint all proposals BEFORE any registration HTTP request.
    bindings = interrupt({"stage": "register", "proposals": [p for p in state["proposals"] if p["status"] == "PREPARED"]})
    proposals = [{**p, **bindings[p["proposal_id"]]} if p["proposal_id"] in bindings else p for p in state["proposals"]]
    return {"proposals": proposals}


WAITING = {"PENDING", "REGISTRATION_UNKNOWN", "EXECUTION_UNKNOWN"}


def after_batch(state: AgentState) -> str:
    if any(p["status"] == "PREPARED" for p in state.get("proposals") or []):
        return "register_proposals"
    return after_registration(state)


def after_registration(state: AgentState) -> str:
    if state.get("workflow_version") == 3 and state.get("proposals") and state.get("batch_status") not in {"COMPLETED", "CANCELLED", "REJECTED"}:
        if not all(p["status"] == "WITHDRAWN" for p in state["proposals"]):
            return "approval_gate"
    return "approval_gate" if any(p["status"] in WAITING for p in state.get("proposals") or []) else "respond"


def approval_gate(state: AgentState) -> dict:
    """One persistent inbox wait; a decision identifies its own card, never list order."""
    decision = interrupt({"stage": "decisions"})
    proposals = [dict(p) for p in state["proposals"]]
    if state.get("workflow_version") == 3 and decision.get("decision") == "BATCH_DONE":
        result = decision["result"]
        executed = {e["approval_id"] for e in result.get("executions", [])}
        for p in proposals:
            if p.get("approval_id") in executed:
                p["status"] = "EXECUTED"
            elif p["status"] == "PENDING":
                p["status"] = "WITHDRAWN"
        return {"proposals": proposals, "decision": decision, "batch_status": result["status"],
                "batch_error": None, "executions": result.get("executions", []),
                "current_impact": result.get("impact", state.get("current_impact")),
                "current_ops": result.get("remainingWhatif", state.get("current_ops", [])),
                "plan_changes": result.get("planChanges", []),
                "notice": ("부서 요청이 최종 거절되었습니다: " + str(result.get("reason", ""))) if result["status"] == "REJECTED"
                    else "task 전체를 취소했습니다. 발주 변경은 실행하지 않았습니다." if result["status"] == "CANCELLED" else None,
                "processed_commands": [*state.get("processed_commands", []), decision["command_id"]]}
    if state.get("workflow_version") == 3 and decision.get("decision") == "APPROVED":
        raise ValueError("개별 승인으로 실행할 수 없습니다. task 검토 명령을 사용하세요.")
    p = next(p for p in proposals if p.get("approval_id") == decision["approvalId"] and p["status"] == "PENDING")
    if decision["decision"] == "WITHDRAWN":
        # 요청자가 보내지 않음 (decisions 6.33): 재제안하지 않고 그 품목만 끝낸다. 다른 카드는 계속 기다린다
        p.update(status="WITHDRAWN", reason=decision.get("reason"))
        message, next_action = OUTCOMES["WITHDRAWN"]
        p["outcome"] = {"code": "WITHDRAWN", "item_name": target_name(state, p["target"]),
                        "message": message, "next_action": next_action, "detail": None}
        return {"proposals": proposals, "decision": decision,
                "processed_commands": [*state.get("processed_commands", []), decision["command_id"]]}
    p.update(status="EXECUTING" if decision["decision"] == "APPROVED" else "REJECTED", reason=decision.get("reason"))
    updates = {"proposals": proposals, "decision": decision, "active_proposal": p["proposal_id"],
               "active_target": p["target"], "targets": p["targets"], "selected_option": p["option"],
               "verify": p["verify"], "pick_reason": p.get("pick_reason"), "executed": None,
               "processed_commands": [*state.get("processed_commands", []), decision["command_id"]]}
    if decision.get("decision") == "APPROVED":
        return updates
    updates.update({"reject_count": sum(x["target"] == p["target"] and x["status"] == "REJECTED" for x in proposals),
                    "tried": [*state.get("tried", []), W.option_key(p["option"])],
                    "rejections": [*state.get("rejections", []), {"proposal_id": p["proposal_id"],
                                   "option_id": p["option"]["option_id"], "reason": decision.get("reason")}],
                    "replan_target": p["target"],
                    # 팀장이 날짜·수량을 정해 재요청하면 그 조건으로만 다시 계산한다 (decisions 6.47). 숫자는 입력칸 값 그대로
                    "replan_constraint": decision.get("constraint") or None})
    return updates


def after_gate(state: AgentState) -> str:
    if state["decision"].get("decision") == "BATCH_DONE":
        return "respond"
    if state["decision"].get("decision") == "WITHDRAWN":
        return after_registration(state)
    if state["decision"].get("decision") == "APPROVED":
        return "execute_change"
    return "skip_target" if state.get("reject_count", 0) > MAX_REJECTS else "replan_prepare"


async def replan_prepare(state: AgentState) -> dict:
    ok, impact = await call_tool(state, "analyze_impact", {"whatif": state["current_ops"]})
    if ok:
        return {"current_impact": impact}
    updates = {"blocked": {**state.get("blocked", {}), state["replan_target"]: "거절 후 최신 분석에 실패했습니다."}}
    proposals = with_outcome(state, state["replan_target"], "REVIEW_FAILED", "거절 후 최신 분석에 실패했습니다: " + str(impact))
    if proposals is not None:
        updates["proposals"] = proposals
    return updates


def execution_updates(state, result):
    opt, decision = state["selected_option"], state["decision"]
    return {"executed": {"ok": True, "result": result, "error": None},
            "executions": [*(e for e in state.get("executions", []) if e["approval_id"] != decision["approvalId"]),
                           {"tool": opt["tool"], "args": opt["args"], "target_rule": opt["target_rule"],
                            "item_id": opt["item_id"], "approval_id": decision["approvalId"], "result": result}],
            "current_ops": W.remaining_ops(state["current_ops"], opt)}


async def execute_change(state: AgentState) -> dict:
    """승인 후 최신 후보·영향을 다시 검사한다. 인자·비용이 바뀌면 기존 승인으로 실행하지 않는다."""
    opt, decision = state["selected_option"], state["decision"]
    if execution_store:
        p = next(p for p in state["proposals"] if p["proposal_id"] == state["active_proposal"])
        await execution_store.event(state["task_id"], proposal_event(p, state))
        old = await execution_store.attempt(decision["approvalId"])
        if old:
            if old["status"] == "SUCCEEDED":
                return execution_updates(state, old["result"])
            return {"executed": {"ok": False, "unknown": True,
                                 "error": "이전 실행 응답을 확정할 수 없어 재실행하지 않았습니다. 발주·변경 이력을 확인해 주세요."}}
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
    try:
        allowed_tools = await asyncio.wait_for(destructive_tools(state), TOOL_TIMEOUT_S)
    except Exception:
        return {"executed": {"ok": False, "error": "변경 도구의 승인 필요 표시를 확인하지 못해 실행을 보류했습니다."}}
    if opt["tool"] not in allowed_tools:
        return {"executed": {"ok": False, "error": f"{opt['tool']}는 승인 대상 변경 도구가 아닙니다."}}
    if execution_store and not await execution_store.begin_attempt(state["task_id"], state["active_proposal"], decision["approvalId"]):
        return {"executed": {"ok": False, "unknown": True, "error": "이미 실행을 시도한 승인입니다. 이력을 확인해 주세요."}}
    ok, res = await call_tool(state, opt["tool"], {**opt["args"], "approval_id": decision.get("approvalId")})
    if execution_store:
        await execution_store.finish_attempt(decision["approvalId"], ok, res)
    if not ok:
        return {"executed": {"ok": False, "unknown": True, "error": str(res)}}
    return execution_updates(state, res)


def record_execution(state: AgentState) -> dict:
    proposals = [dict(p) for p in state["proposals"]]
    p = next(p for p in proposals if p["proposal_id"] == state["active_proposal"])
    execution = state["executed"]
    p.update(status="EXECUTED" if execution["ok"] else "EXECUTION_UNKNOWN" if execution.get("unknown") else "BLOCKED",
             execution=execution, reason=execution.get("error"))
    blocked = dict(state.get("blocked") or {})
    if not execution["ok"]:
        blocked[p["target"]] = execution["error"]
    return {"proposals": proposals, "blocked": blocked}


async def refresh_impact(state: AgentState) -> dict:
    # DB는 이미 변경됐다. 실행한 op를 다시 넣으면 분할·감량이 중복 반영된다.
    ok, impact = await call_tool(state, "analyze_impact", {"whatif": state["current_ops"]},
                                label="실행 후 남은 문제 확인 중")
    if not ok:
        return {"current_impact": None, "notice": "실행 후 분석을 확인하지 못했습니다: " + str(impact)}
    return {"current_impact": impact, "notice": None}


def after_refresh(state: AgentState) -> str:
    return after_registration(state)


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
    plans, impact = state.get("plans") or [], state.get("current_impact")
    executions = state.get("executions") or []
    # 구역마다 "제목:" + "- " 항목, 사이는 빈 줄 — 화면이 목록으로 그린다 (decisions 6.27)
    parts = [f"승인된 변경 {len(executions)}건을 실행했습니다." if executions else "현재 분석 결과입니다."]
    if not executions and not state.get("proposals") and not state.get("scope"):
        # 해결 요청인데 풀 문제가 없다: 분석만 하고 끝난 것처럼 보이지 않게 이유를 먼저 말한다 (decisions 6.48)
        if state.get("whatif_ops"):
            parts = ["이 가정으로 다시 계산하니 기존 발주를 조정해야 할 결품·과잉이 없어 해결안을 만들지 않았습니다."
                     + (" 앞 작업에서 실행한 변경으로 이미 풀렸을 수 있습니다." if state.get("inherited") else "")]
        else:
            parts = ["지금 데이터에는 기존 발주를 조정해야 할 결품·과잉이 없어 해결안을 만들지 않았습니다. "
                     "어떤 변경에 대한 해결 방법인지 함께 알려 주세요. 예: \"허브를 11/25로 당기면 생기는 결품 해결 방법 만들어 줘\""]
    if executions:
        parts.append("실행한 변경:\n" + "\n".join("- " + describe_execution(e["result"]).removeprefix("실행 결과: ")
                                                for e in executions))
    held = []
    if state.get("executed") and not state["executed"]["ok"]:
        held.append("실행 보류/실패: " + str(state["executed"]["error"]))
    blocked = state.get("blocked") or {}
    proposals = state.get("proposals") or []
    # 거절 뒤 재검토 결과 (decisions 6.30): 품목마다 결과 · 거절 사유 · 다음 행동
    reviewed = [p for p in proposals if p.get("outcome")]
    reviewed_targets = {p["target"] for p in reviewed if p["outcome"]["code"] != "REPROPOSED"}
    for p in reviewed:
        o = p["outcome"]
        line = f"{o.get('item_name') or target_name(state, p['target'])}: {o['message']}"
        if p.get("reason"):
            line += f" ({'메모' if o['code'] == 'WITHDRAWN' else '거절 사유'}: {p['reason']})"
        held.append(line + f" · {o['next_action']}")
    held += [f"{target_name(state, key)}: {reason}" for key, reason in blocked.items() if key not in reviewed_targets]
    # 여러 품목을 먼저 준비하는 흐름(decisions 6.29)에서 보류된 제안
    held += [f"{target_name(state, p['target'])}: {p.get('reason', '보류')}" for p in proposals
             if p["status"] == "BLOCKED" and p["target"] not in blocked]
    if held:
        parts.append("거절·보류 결과:\n" + "\n".join("- " + h for h in held))
    if state.get("plan_changes"):
        parts.append("생산관리의 요청에 따라 생산계획도 함께 변경했습니다:\n" + "\n".join(
            f"- {p['planId']}: {p['before']['plan_date']} · {p['before']['qty']}개 → {p['after']['plan_date']} · {p['after']['qty']}개"
            for p in state["plan_changes"]))
    elif state.get("whatif_ops"):
        parts.append("질문의 생산계획 가정은 저장하지 않았습니다. 아래는 현재 DB에 그 가정을 더해 다시 분석한 결과입니다.")
    if impact is not None:
        parts.append(describe_impact(impact, plans))
    else:
        parts.append("최신 영향 결과를 확인하지 못했습니다. 최초 분석을 실행 후 결과로 사용하지 않습니다.")
    if any(s.get("orderable") for s in (impact or {}).get("shortages", [])):
        parts.append("지금 발주하면 제때 오는 부족분은 안내만 했고, 새 발주는 만들지 않았습니다.")
    return "\n\n".join(parts)


def respond(state: AgentState) -> dict:
    intent, plans = state.get("intent"), state.get("plans") or []
    detail = None
    if intent == "CLARIFY":
        detail = state.get("clarification") or F.feedback([F.issue("MISSING_INFORMATION",
            state.get("clarify") or "분석할 대상과 변경 조건을 확인해 주세요.", kind="NEEDS_INPUT",
            action="위에서 확인을 요청한 내용만 추가로 알려 주세요.")])
    if state.get("failure"):
        answer = f"처리를 마치지 못했습니다: {state['failure']}"
        if state.get("executions"):
            answer = _adjust_answer(state) + "\n" + answer
    elif intent == "CLARIFY":
        answer = F.answer(detail)
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
            if state.get("scenarios"):
                lines = scenario_summary(state["scenarios"], plans)
                answer += (f"\n\n장기화 대비 비교 {len(lines)}건:\n" + "\n".join("- " + x for x in lines)
                           + "\n\n4주·12주는 협력사가 확정한 날짜가 아닌 장기화 대비 가정입니다.")
    if state.get("notice"):
        answer = f"({state['notice']})\n{answer}"
    if state.get("inherited"):
        answer = f"({state['inherited']})\n{answer}"
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
    if state.get("scenarios"):
        result["scenarios"] = state["scenarios"]   # 통보 기준 impact는 그대로, 비교만 덧붙인다 (contracts 49)
    if intent == "CLARIFY":
        result["clarification"] = detail
    if intent == "IMPACT" and state.get("receipt_delay"):
        result["receipt_delay"] = state["receipt_delay"]
    if state.get("coordination"):
        result["coordinationId"] = state["coordination"]["id"]
        result["plan_changes"] = state.get("plan_changes") or []
    if intent == "ADJUST":
        result.update({k: state.get(k) for k in
                       ("targets", "selected_option", "pick_reason", "verify_log", "decision", "executed")})
        result.update({"executions": state.get("executions") or [],
                       "post_execution_impact": state.get("current_impact"),
                       "remaining_whatif": state.get("current_ops") or [],
                       "blocked_targets": state.get("blocked") or {},
                       "proposals": state.get("proposals") or [],
                       "approval_summary": {s: sum(p["status"] == s for p in state.get("proposals") or [])
                                            for s in ("EXECUTED", "REJECTED", "BLOCKED", "WITHDRAWN")}})
    return {"answer": answer, "next_actions": next_actions, "result": result}


# ---------------------------------------------------------------------------
# 그래프 조립 (checkpointer는 main.py가 붙인다)
# ---------------------------------------------------------------------------

builder = StateGraph(AgentState)
builder.add_node("batch_idle", lambda state: {})
builder.add_edge("batch_idle", "approval_gate")
for name, fn in [("classify_intent", classify_intent), ("agent_impact", agent_impact), ("tools", tools),
                 ("agent_qa", agent_qa), ("tools_rag", tools_rag), ("adjust_prepare", adjust_prepare),
                 ("compare", compare), ("agent_adjust", agent_adjust), ("verify", verify),
                 ("collect_evidence", collect_evidence), ("skip_target", skip_target),
                 ("save_proposal", save_proposal), ("check_batch", check_batch),
                 ("register_proposals", register_proposals), ("replan_prepare", replan_prepare),
                 ("approval_gate", approval_gate), ("execute_change", execute_change),
                 ("record_execution", record_execution),
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
builder.add_conditional_edges("compare", after_compare, ["agent_adjust", "check_batch"])
builder.add_conditional_edges("agent_adjust", after_adjust, ["verify", "skip_target"])
builder.add_conditional_edges("verify", after_verify, ["collect_evidence", "agent_adjust", "skip_target"])
builder.add_conditional_edges("collect_evidence", after_evidence, ["save_proposal", "skip_target"])
builder.add_edge("save_proposal", "compare")
builder.add_conditional_edges("check_batch", after_batch, ["register_proposals", "approval_gate", "respond"])
builder.add_conditional_edges("register_proposals", after_registration, ["approval_gate", "respond"])
builder.add_edge("skip_target", "compare")
builder.add_conditional_edges("approval_gate", after_gate,
                              ["execute_change", "replan_prepare", "skip_target", "approval_gate", "respond"])
builder.add_edge("replan_prepare", "compare")
builder.add_edge("execute_change", "record_execution")
builder.add_edge("record_execution", "refresh_impact")
builder.add_conditional_edges("refresh_impact", after_refresh, ["approval_gate", "respond"])
builder.add_edge("respond", END)
