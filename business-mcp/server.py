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
    try:
        r = httpx.post(f"{BACKEND_URL}/internal/analysis/impact", json={"whatif": whatif}, timeout=5)
    except httpx.HTTPError as e:
        raise ToolError(f"[UPSTREAM_TIMEOUT] 백엔드에 연결하지 못했습니다. 잠시 후 다시 시도하세요. ({e})")
    if r.is_error:
        raise ToolError(f"[HTTP_{r.status_code}] {r.text}")
    return r.json()


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=9101, path="/mcp", show_banner=False)
