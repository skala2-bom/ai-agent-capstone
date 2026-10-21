"""ADJUST 경로의 읽기 전용 도구 실행 규칙.

LangGraph나 MCP에 의존하지 않는 부분을 분리했다. Agent 노드는 이 함수의
결과를 ToolMessage로 바꾸고, 실제 호출은 MCP Client에 위임한다.
"""

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any


READ_ONLY_ADJUST_TOOLS = frozenset({
    "get_item", "get_inventory", "list_plans", "list_receipts", "compare_options",
})
TOOL_LABELS = {
    "get_item": "품목 조회 중",
    "get_inventory": "재고 조회 중",
    "list_plans": "생산 계획 조회 중",
    "list_receipts": "입고 예정 조회 중",
    "compare_options": "해결 방법 비교 중",
}
MAX_RESULT_CHARS = 4_000
MONETARY_FIELDS = frozenset({"unit_price", "amount", "added_cost", "expedite_cost_per_day", "delay_penalty"})
UNIT_PRICE_FIELDS = frozenset({"unit_price", "expedite_cost_per_day"})


def mask_financials(value: Any, role: str | None) -> Any:
    """backend 마스킹이 빠졌을 때 모델에 넘기기 전 금액을 다시 가린다."""
    hidden = set() if role == "APPROVER" else UNIT_PRICE_FIELDS if role == "BUYER" else MONETARY_FIELDS
    if isinstance(value, dict):
        return {key: None if key in hidden else mask_financials(item, role)
                for key, item in value.items()}
    if isinstance(value, list):
        return [mask_financials(item, role) for item in value]
    return value


async def execute_adjust_calls(
    calls: list[dict],
    invoke: Callable[[str, dict], Awaitable[Any]],
    emit: Callable[[dict], Awaitable[None]],
    role: str | None = None,
) -> tuple[list[dict], list[dict] | None]:
    """모델의 도구 제안을 검사·실행하고 ToolMessage 재료와 후보를 돌려준다.

    변경 도구는 여기서 절대 실행하지 않는다. `options`의 `tool`·`args`는
    그대로 보존하고, 공개할 수 없는 금액 값은 가려서 다음 단계에 넘긴다.
    """
    results = []
    options = None
    for call in calls:
        name = call.get("name", "")
        call_id = call.get("id", "")
        args = call.get("args")
        await emit({"type": "TOOL_START", "tool": name, "label": TOOL_LABELS.get(name, "도구 확인 중")})
        started = time.monotonic()
        ok = False
        try:
            if name not in READ_ONLY_ADJUST_TOOLS:
                raise ValueError(f"[TOOL_NOT_ALLOWED] {name}은 ADJUST 조회 단계에서 실행할 수 없습니다. 변경은 승인 후 execute_change에서만 합니다.")
            if not isinstance(args, dict):
                raise ValueError("[INVALID_TOOL_ARGS] 도구 인자는 JSON 객체여야 합니다.")
            data = mask_financials(await invoke(name, args), role)
            if name == "compare_options":
                if not isinstance(data, dict) or not isinstance(data.get("options"), list):
                    raise ValueError("[INVALID_TOOL_RESULT] compare_options 응답에 options 목록이 없습니다.")
                options = data["options"]
            content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            if len(content) > MAX_RESULT_CHARS:
                content = content[:MAX_RESULT_CHARS] + "\n[도구 결과가 4,000자를 넘어 잘렸습니다.]"
            ok = True
        except Exception as exc:
            # 업무 오류의 [코드]·retry_hint를 모델이 읽고 다시 시도할 수 있게 유지한다.
            content = f"도구 오류: {exc}"
        await emit({"type": "TOOL_END", "tool": name, "ok": ok,
                    "ms": int((time.monotonic() - started) * 1000)})
        results.append({"content": content, "tool_call_id": call_id, "name": name})
    return results, options
