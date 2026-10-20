"""business-mcp — Tool 계층 (아키텍처 3장, 8.1).

업무 기능을 AI가 부를 수 있는 **도구**로 공개한다.

금지 (교재 5장)
  - DB 직접 접근 금지. 업무 데이터는 전부 backend `/internal/**`를 거친다.
  - 업무 규칙 복제 금지. 계산·검사는 backend가 하고, 여기서는 전달만 한다.
    (아키텍처 1.1 "계산은 백엔드에" — 규칙이 두 곳에 생기면 화면 조회와
     AI 분석이 서로 다른 숫자를 내기 시작한다)

이 서버가 실제로 하는 일은 셋뿐이다.
  1. 도구 이름·설명·인자 스키마를 모델에게 보여 준다 (docstring이 곧 설명)
  2. backend를 부른다
  3. backend의 오류 응답을 ToolError 한 문장으로 바꿔 던진다

지금은 도구가 `analyze_impact` 하나뿐이다. 아키텍처 8.1의 나머지 도구
(get_item, explode_bom, where_used, list_plans, get_inventory, list_receipts,
 compare_options, get_supplier_metrics, 그리고 승인이 필요한 변경 도구 4개)는
MCP 담당이 같은 모양으로 늘린다.
  - split_po 절차·검사 규칙: docs/decisions.md 3절
  - get_supplier_metrics가 읽는 평가지표: docs/decisions.md 2절
  - 변경 도구에는 annotations의 destructive_hint = true를 붙인다. Harness는
    도구 이름이 아니라 이 값을 보고 승인 앞에서 멈춘다 (아키텍처 8.1).
"""

import os

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8080")

mcp = FastMCP("business-mcp")


@mcp.tool
def analyze_impact(whatif: list[dict]) -> dict:
    """가정 변경(whatif)을 적용했을 때 생기는 결품·과잉 목록을 돌려준다.

    whatif 예: [{"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}]
    수량 단위는 개, 날짜는 YYYY-MM-DD. 계산은 백엔드가 하고 이 도구는 전달만 한다.
    """
    # ↑ 이 docstring이 모델에게 그대로 도구 설명으로 전달된다.
    #   "받는 값 · 돌려주는 값 · 단위"를 반드시 적는다 (교재 2장).
    #   단위를 빼면 모델이 100을 "개"인지 "박스"인지 모른 채 인자를 만든다.
    try:
        r = httpx.post(f"{BACKEND_URL}/internal/analysis/impact", json={"whatif": whatif}, timeout=5)
    except httpx.HTTPError as e:
        # 연결 자체가 안 될 때. 아키텍처 8.1의 UPSTREAM_TIMEOUT에 해당한다.
        raise ToolError(f"[UPSTREAM_TIMEOUT] 백엔드에 연결하지 못했습니다. 잠시 후 다시 시도하세요. ({e})")
    if r.is_error:
        # backend는 업무 오류를 {error, message, retry_hint} 형태로 준다 (아키텍처 8.1).
        # 실제 구현에서는 그 셋을 한 문장으로 합쳐 던진다. 예:
        #   ToolError("[BELOW_MOQ] 최소 발주 수량 미만입니다. 최소 수량은 300개입니다. ...")
        # retry_hint를 꼭 넣는 이유: ToolError는 모델에게 ToolMessage로 돌아가고,
        # 모델은 그 힌트를 읽고 인자를 고쳐 **스스로 다시 부른다** (교재 2장).
        raise ToolError(f"[HTTP_{r.status_code}] {r.text}")
    return r.json()


if __name__ == "__main__":
    # compose에서는 Streamable HTTP를 쓴다 (교재 4장).
    # path="/mcp"를 빠뜨리면 Client가 404를 받는다 — 가장 자주 나는 실수.
    mcp.run(transport="http", host="0.0.0.0", port=9101, path="/mcp", show_banner=False)
