"""business-mcp — Tool 계층 (아키텍처 3장, 8.1 · contracts.md 2.1).

업무 기능을 AI가 부를 수 있는 **도구**로 공개한다.

금지 (교재 5장)
  - DB 직접 접근 금지. 업무 데이터는 전부 backend `/internal/**`를 거친다.
  - 업무 규칙 복제 금지. 계산·검사는 backend가 하고, 여기서는 전달만 한다.
    (아키텍처 1.1 "계산은 백엔드에" — 규칙이 두 곳에 생기면 화면 조회와
     AI 분석이 서로 다른 숫자를 내기 시작한다)

이 서버가 실제로 하는 일은 넷뿐이다.
  1. 도구 이름·설명·인자 스키마를 모델에게 보여 준다 (docstring이 곧 설명)
  2. backend를 부른다
  3. 요청자 헤더(X-Task-Id·X-User-Id·X-Role)를 넘기고, 서비스 토큰은 business-mcp 자신의 것으로 교체한다 (contracts 1.1)
  4. backend의 오류 응답을 `[코드] 메시지 retry_hint` 한 문장의 ToolError로 바꿔 던진다 (contracts 2.5)

도구 13개 (contracts 2.1). 2차 5개(explode_bom, where_used, get_supplier_metrics,
change_po_qty, cancel_po)도 등록되어 있다.

변경 도구에는 annotations의 destructive_hint = true를 붙인다. agent의 Harness는
도구 이름이 아니라 이 값을 보고 승인 앞에서 멈춘다 (아키텍처 8.1).
"""

import os

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8080")
TIMEOUT_S = 5  # 아키텍처 4.2: business-mcp → backend 5초

mcp = FastMCP("business-mcp")



@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """docker healthcheck용. 프로세스가 요청을 받는지만 본다.

    backend는 보지 않는다 — 도구를 부를 때 연결하고, 실패는 그 도구의 ToolError가 된다.
    backend까지 보면 backend 하나가 죽을 때 이 서버와 agent까지 unhealthy로 번진다 (decisions 6.41).
    """
    return JSONResponse({"ok": True})


READ = ToolAnnotations(read_only_hint=True, destructive_hint=False)
CHANGE = ToolAnnotations(read_only_hint=False, destructive_hint=True)


def _forward_headers() -> dict:
    """agent가 붙인 요청자 헤더를 그대로 backend로 넘긴다 (contracts 1.1).

    역할을 도구 인자로 받지 않는 이유: 인자는 모델이 채우는 값이라 조작될 수 있다.
    해석하지 않고 넘기기만 한다 — 금액 마스킹은 backend가 한다 (SEC-001).
    """
    headers = get_http_headers(include_all=True)
    out = {k: v for k, v in headers.items() if k.lower().startswith("x-") and k.lower() != "x-service-token"}
    # 서비스 토큰은 넘겨받은 것(agent 것)을 버리고 business-mcp 자기 토큰을 쓴다.
    # backend는 서비스마다 열어 주는 경로가 다르다 — agent 토큰으로는 업무 API가 안 열린다 (backend ServiceTokens)
    if os.getenv("SERVICE_TOKEN"):
        out["X-Service-Token"] = os.environ["SERVICE_TOKEN"]
    return out


def _call(method: str, path: str, *, json: dict | None = None, params: dict | None = None):
    """backend `/internal/**` 호출. 오류는 contracts 2.5 형식의 ToolError로 바꾼다."""
    try:
        r = httpx.request(method, f"{BACKEND_URL}/internal{path}", json=json,
                          params={k: v for k, v in (params or {}).items() if v is not None},
                          headers=_forward_headers(), timeout=TIMEOUT_S)
    except httpx.TimeoutException:
        raise ToolError("[UPSTREAM_TIMEOUT] 백엔드 응답이 늦습니다. 잠시 후 다시 시도하세요.")
    except httpx.HTTPError as e:
        raise ToolError(f"[UPSTREAM_TIMEOUT] 백엔드에 연결하지 못했습니다. 잠시 후 다시 시도하세요. ({e})")
    if r.is_error:
        # backend는 업무 오류를 {error, message, retry_hint}로 준다. 상태 코드가 아니라 error 코드로 읽는다.
        # retry_hint를 꼭 넣는 이유: ToolError는 모델에게 ToolMessage로 돌아가고,
        # 모델은 그 힌트를 읽고 인자를 고쳐 **스스로 다시 부른다** (교재 2장).
        try:
            body = r.json()
            message = f"[{body['error']}] {body['message']} {body.get('retry_hint') or ''}".strip()
        except (ValueError, KeyError, TypeError):
            message = f"[HTTP_{r.status_code}] {r.text[:300]}"
        raise ToolError(message)
    return r.json()


# ---------------------------------------------------------------------------
# 조회 4개
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
def get_item(item_id: str) -> dict:
    """품목 하나의 정보를 돌려준다. 완제품(FG-HUB 등)·부품(MCU 등) 모두 가능.

    돌려주는 값: item_id, name(한글 이름), type(FG|ASSY|PART), unit, lead_time_days(일),
    moq(개), safety_stock(개), high_value, unit_price(원, 역할에 따라 가려짐), supplier_id.
    없는 ID면 비슷한 ID 목록과 함께 NOT_FOUND 오류.
    """
    return _call("GET", f"/items/{item_id}")


@mcp.tool(annotations=READ)
def get_inventory(type: str | None = None, item_id: str | None = None) -> dict:
    """현재 재고(기준일 2026-11-02)를 돌려준다. type은 FG(완제품) 또는 PART(부품), 둘 다 선택.

    돌려주는 값: as_of, items[{item_id, name, type, on_hand(개), safety_stock(개)}].
    """
    return _call("GET", "/inventory", params={"type": type, "item_id": item_id})


@mcp.tool(annotations=READ)
def list_plans(date_from: str | None = None, date_to: str | None = None) -> dict:
    """생산 계획 목록. date_from·date_to는 YYYY-MM-DD, 양끝 포함, 둘 다 선택.

    돌려주는 값: plans[{plan_id(PP-01 등), product_id(FG-HUB 등), plan_date, qty(대), status}].
    """
    return _call("GET", "/plans", params={"from": date_from, "to": date_to})


@mcp.tool(annotations=READ)
def list_receipts(item_id: str | None = None, date_from: str | None = None,
                  date_to: str | None = None, status: str = "OPEN") -> dict:
    """부품 입고 예정 발주 목록. 기본은 아직 안 들어온(OPEN) 발주. 날짜는 입고 예정일 기준 YYYY-MM-DD.

    돌려주는 값: purchase_orders[{po_id, item_id, supplier_id, qty(개), unit_price(원), amount(원),
    order_date, due_date(입고 예정일), status, parent_po_id}].
    """
    return _call("GET", "/purchase-orders",
                 params={"status": status, "item_id": item_id, "from": date_from, "to": date_to})


# ---------------------------------------------------------------------------
# 계산 2개
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
def analyze_impact(whatif: list[dict]) -> dict:
    """가정 변경(whatif)을 적용했을 때 생기는 결품·경고·과잉 목록을 돌려준다 (contracts 2.2).

    whatif 예: [{"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}]
    op: reschedule_plan · change_plan_qty · request_due_date_change · split_po · change_po_qty · cancel_po.
    수량 단위는 개, 날짜는 YYYY-MM-DD. 빈 목록이면 지금 계획 그대로 판정한다.
    돌려주는 값: shortages(projected_stock<0), warnings(0≤재고<안전재고), excesses(고가 부품만).
    계산은 backend가 하고 이 도구는 전달만 한다.
    """
    # ↑ 이 docstring이 모델에게 그대로 도구 설명으로 전달된다.
    #   "받는 값 · 돌려주는 값 · 단위"를 반드시 적는다 (교재 2장).
    return _call("POST", "/analysis/impact", json={"whatif": whatif})


@mcp.tool(annotations=READ)
def compare_options(issues: list[dict], whatif: list[dict] | None = None) -> dict:
    """결품·과잉(issues)을 푸는 해결 대안을 비교해 돌려준다 (contracts 2.3).

    whatif: analyze_impact에 넣었던 것과 같은 가정 변경. 결품은 가정 위에서만 생기므로 대안도 그 위에서 만든다.
    issues: analyze_impact의 shortages/excesses 항목을 넣는다 (item_id만 본다). 비우면 전부.
    돌려주는 값: options[{option_id, type(SPLIT_PO|DUE_DATE_CHANGE|CHANGE_QTY|CANCEL_PO), item_id,
    target_rule(INV-001 결품|INV-003 과잉), summary,
    tool, args(그대로 실행에 쓰는 인자), added_cost(원), saved_amount, cost_level, risk(LOW|MID|HIGH), feasible,
    resolves(그 품목·규칙의 문제를 해결하나), side_effects[{type(NEW_SHORTAGE|WORSENED_SHORTAGE), item_id,
    product_id, plan_id, plan_date, before_shortage_qty, after_shortage_qty, increase_qty}](이 대안 때문에 새로 생기거나
    커진 결품), remaining_issues[](풀려던 결품·과잉 중 남은 것), recommended}], recommended_option_id.
    tool·args는 고치지 말고 그대로 쓴다. 모델이 인자를 다시 만들면 숫자가 틀어질 수 있다.
    """
    return _call("POST", "/analysis/options", json={"whatif": whatif or [], "issues": issues})


# ---------------------------------------------------------------------------
# 2차 조회 3개 (contracts 2.1) — B 파트가 추가 (10/20 밤)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ)
def explode_bom(product_id: str, on_date: str | None = None) -> dict:
    """완제품(FG-HUB 등) 하나를 부품 단위로 펼친다. on_date(YYYY-MM-DD, 기본 기준일 2026-11-02) 기준 BOM을 쓴다.

    설계변경이 있으면 날짜에 따라 부품이 다르다 (도어센서 케이스: 11/30까지 옛 케이스, 12/1부터 새 케이스).
    돌려주는 값: product_id, name, on_date, tree[{level, parent_id, item_id, name, type(ASSY|PART), qty_per, qty}],
    parts[{item_id, name, qty}] (완제품 1대에 드는 구매 부품 개수 합계). 없는 제품이면 NOT_FOUND(완제품 ID 목록).
    """
    return _call("GET", f"/bom/{product_id}/explode", params={"on_date": on_date})


@mcp.tool(annotations=READ)
def where_used(item_id: str, on_date: str | None = None) -> dict:
    """부품 하나가 들어가는 완제품과 1대당 개수. 어떤 부품이 모자라면 어느 제품이 영향받는지 볼 때 쓴다.

    돌려주는 값: item_id, on_date, products[{product_id, name, qty_per_unit(개)}]. 2개 이상이면 공용 부품이다.
    """
    return _call("GET", f"/bom/where-used/{item_id}", params={"on_date": on_date})


@mcp.tool(annotations=READ)
def get_supplier_metrics(item_id: str | None = None) -> dict:
    """협력사 × 부품 평가지표. item_id를 빼면 전부. 주 협력사가 먼저, 대체 협력사(is_primary=false)가 뒤에 온다.

    돌려주는 값: metrics[{supplier_id, supplier_name, is_primary, item_id, item_name, unit_price(원),
    lead_time_days(일), expedite_cost_per_day(원/개·일), split_fee(원), delay_penalty(원), on_time_rate(%),
    quality_score(0~100), evaluated_at, evaluated_by}]. 단가는 역할에 따라 null로 가려진다.
    이 값은 조회만 할 수 있다 (입력은 사람이 화면에서만, decisions 2.4).
    """
    return _call("GET", "/suppliers/metrics", params={"item_id": item_id})


# ---------------------------------------------------------------------------
# 변경 4개 — 승인 필요 (destructive_hint = true)
# ---------------------------------------------------------------------------

@mcp.tool(annotations=CHANGE)
def request_due_date_change(po_id: str, new_due_date: str, approval_id: str) -> dict:
    """발주 하나의 입고 예정일을 바꾼다. 팀장 승인을 받은 approval_id가 있어야 한다.

    new_due_date는 YYYY-MM-DD. 그 발주의 발주일 + 리드타임보다 이를 수 없다 (LEADTIME_INFEASIBLE,
    retry_hint에 가장 빠른 가능일). 돌려주는 값: po_id, before{due_date}, after{due_date}, status.
    """
    return _call("POST", f"/purchase-orders/{po_id}/due-date",
                 json={"new_due_date": new_due_date, "approval_id": approval_id})


@mcp.tool(annotations=CHANGE)
def split_po(po_id: str, split_qty: int, new_due_date: str, approval_id: str) -> dict:
    """발주를 둘로 나눠 split_qty개(개 단위)만 new_due_date(YYYY-MM-DD)에 먼저 받는다.

    원 발주는 수량만 줄고 입고일은 그대로다. 팀장 승인을 받은 approval_id가 있어야 한다.
    돌려주는 값: original{po_id, qty, due_date}, new{po_id, qty, due_date, parent_po_id}.
    """
    return _call("POST", f"/purchase-orders/{po_id}/split",
                 json={"split_qty": split_qty, "new_due_date": new_due_date, "approval_id": approval_id})


@mcp.tool(annotations=CHANGE)
def change_po_qty(po_id: str, new_qty: int, approval_id: str) -> dict:
    """발주 수량을 new_qty개로 바꾼다 (과잉 재고를 줄일 때). 팀장 승인을 받은 approval_id가 있어야 한다.

    new_qty는 그 부품의 최소 발주 수량(MOQ) 이상이어야 한다 (BELOW_MOQ, retry_hint에 최소 수량).
    0개로 만들려면 cancel_po를 쓴다. 돌려주는 값: po_id, before{qty}, after{qty}, status.
    """
    return _call("POST", f"/purchase-orders/{po_id}/qty", json={"new_qty": new_qty, "approval_id": approval_id})


@mcp.tool(annotations=CHANGE)
def cancel_po(po_id: str, approval_id: str) -> dict:
    """발주를 취소한다 (설계변경으로 못 쓰게 된 부품, 쓸 곳 없는 과잉). 팀장 승인을 받은 approval_id가 있어야 한다.

    이미 입고·취소된 발주는 PO_NOT_OPEN. 돌려주는 값: po_id, before{status: OPEN}, after{status: CANCELLED}.
    """
    return _call("POST", f"/purchase-orders/{po_id}/cancel", json={"approval_id": approval_id})


if __name__ == "__main__":
    # compose에서는 Streamable HTTP를 쓴다 (교재 4장).
    # path="/mcp"를 빠뜨리면 Client가 404를 받는다 — 가장 자주 나는 실수.
    mcp.run(transport="http", host="0.0.0.0", port=9101, path="/mcp", show_banner=False)
