"""가정 변경(whatif) — 형식, 검증, verify 판정 (contracts 2.2·2.4·2.6).

여기에는 **순수 함수만** 둔다. 네트워크도 모델도 부르지 않으므로 평가셋(eval/golden.json)으로
바로 시험할 수 있다.

whatif 한 건의 모양은 backend `/internal/analysis/impact`가 받는 그대로다.
    {"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}

op 이름은 변경 도구 이름과 같다 (decisions 6.12). 그래서 verify는 compare_options가 준
대안의 tool·args를 **손대지 않고** {"op": tool, **args}로 넣는다 — 모델이 인자를 옮겨 적다
틀릴 여지가 없다 (decisions 6.3).
"""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

BASE_DATE = date(2026, 11, 2)  # 시연 기준일 (contracts 1.4)

# op마다 필요한 인자. 순서는 backend 계약 표(contracts 2.2)와 같다.
OP_ARGS: dict[str, list[str]] = {
    "reschedule_plan": ["plan_id", "new_date"],
    "change_plan_qty": ["plan_id", "new_qty"],
    "request_due_date_change": ["po_id", "new_due_date"],
    "split_po": ["po_id", "split_qty", "new_due_date"],
    "change_po_qty": ["po_id", "new_qty"],
    "cancel_po": ["po_id"],
}
DATE_ARGS = {"new_date", "new_due_date"}
QTY_ARGS = {"new_qty", "split_qty"}
PO_ID = re.compile(r"^PO-\d{4}-\d{3}$")


# ---------------------------------------------------------------------------
# classify_intent의 출력 형식 (모델의 structured output)
# ---------------------------------------------------------------------------

class WhatifOp(BaseModel):
    """가정 변경 한 건. 쓰지 않는 칸은 비운다."""
    op: Literal["reschedule_plan", "change_plan_qty", "request_due_date_change",
                "split_po", "change_po_qty", "cancel_po"]
    plan_id: str | None = Field(None, description="생산 계획 ID. 예: PP-05")
    new_date: str | None = Field(None, description="새 생산일 YYYY-MM-DD")
    new_qty: int | None = Field(None, description="새 수량 (대 또는 개, 숫자만)")
    po_id: str | None = Field(None, description="발주 ID. 예: PO-2610-106")
    new_due_date: str | None = Field(None, description="새 입고 예정일 YYYY-MM-DD")
    split_qty: int | None = Field(None, description="떼어 먼저 받을 수량(개)")


class Classification(BaseModel):
    """질문 분류 결과."""
    intent: Literal["IMPACT", "ADJUST", "QA"]
    whatif: list[WhatifOp] = Field(default_factory=list)
    unclear: str | None = Field(None, description="계획·날짜를 하나로 정할 수 없을 때만, 무엇이 모호한지")


def normalize(op: dict | BaseModel) -> dict:
    """op 이름과 그 op에 필요한 인자만 남긴다. 순서도 계약대로 맞춘다."""
    d = op.model_dump() if isinstance(op, BaseModel) else dict(op)
    name = d.get("op")
    return {"op": name, **{k: d.get(k) for k in OP_ARGS.get(name, [])}}


def validate_ops(ops: list[dict], plans: list[dict]) -> list[str]:
    """whatif를 backend에 보내기 전에 검사한다 (Harness Guardrails).

    모델이 만든 값이라 표에 없는 계획, 형식이 틀린 날짜가 섞일 수 있다. 여기서 걸러 모델에게
    고칠 기회를 한 번 주고, 그래도 틀리면 사용자에게 되묻는다. 발주 ID는 형식만 본다 —
    있는지 여부는 backend가 NOT_FOUND와 비슷한 ID 목록으로 알려 준다.
    """
    plan_ids = {p["plan_id"] for p in plans}
    errors = []
    for i, op in enumerate(ops, 1):
        name = op.get("op")
        if name not in OP_ARGS:
            errors.append(f"{i}번째: 모르는 op '{name}'")
            continue
        for arg in OP_ARGS[name]:
            v = op.get(arg)
            if v in (None, ""):
                errors.append(f"{i}번째 {name}: {arg}가 비어 있음")
            elif arg == "plan_id" and v not in plan_ids:
                errors.append(f"{i}번째 {name}: 표에 없는 plan_id '{v}'")
            elif arg == "po_id" and not PO_ID.match(str(v)):
                errors.append(f"{i}번째 {name}: po_id 형식이 아님 '{v}' (예: PO-2610-106)")
            elif arg in DATE_ARGS:
                try:
                    if date.fromisoformat(str(v)) <= BASE_DATE:
                        errors.append(f"{i}번째 {name}: {arg} {v}는 기준일 {BASE_DATE} 이후여야 함")
                except ValueError:
                    errors.append(f"{i}번째 {name}: {arg} '{v}'는 YYYY-MM-DD가 아님")
            elif arg in QTY_ARGS and (not isinstance(v, int) or v <= 0):
                errors.append(f"{i}번째 {name}: {arg}는 1 이상의 정수여야 함")
    return errors


def consistency_errors(question: str, ops: list[dict], plans: list[dict]) -> list[str]:
    """질문에 적힌 원래 생산일과 고른 계획이 맞는지 본다.

    "11/30 플러그 생산은 500대로"처럼 같은 제품의 계획 중 하나의 생산일이 질문에 있으면,
    고른 plan_id가 그 날짜의 계획이어야 한다. 모델이 같은 제품의 다른 계획(PP-01)을 고르는
    실수를 실제로 했다 (평가셋 SC2). 형식 검사로는 안 잡혀서 따로 본다.
    """
    mentioned = {infer_date(*map(int, m)) for m in MD.findall(question)}
    by_id = {p["plan_id"]: p for p in plans}
    errors = []
    for op in ops:
        plan = by_id.get(op.get("plan_id"))
        if not plan:
            continue
        same_product = [p for p in plans if p["product_id"] == plan["product_id"]]
        named = [p for p in same_product if p["plan_date"] in mentioned and p["plan_date"] != op.get("new_date")]
        if named and plan not in named:
            errors.append(f"{op['op']}: 질문에 적힌 생산일 {', '.join(p['plan_date'] for p in named)}의 계획은 "
                          f"{', '.join(p['plan_id'] for p in named)}인데 {plan['plan_id']}({plan['plan_date']})를 골랐음")
    return errors


def same_ops(a: list[dict], b: list[dict]) -> bool:
    """두 whatif가 같은가. 순서는 보지 않는다 (같은 계획을 두 번 바꾸는 질문은 없다고 본다)."""
    key = lambda o: tuple(sorted((k, str(v)) for k, v in normalize(o).items()))
    return sorted(map(key, a)) == sorted(map(key, b))


# ---------------------------------------------------------------------------
# 가짜 모델용 규칙 분류 (OPENAI_API_KEY가 없을 때)
# ---------------------------------------------------------------------------

PRODUCT_WORDS = {"허브": "FG-HUB", "스위치": "FG-SWITCH", "플러그": "FG-PLUG", "도어": "FG-DOOR"}
MD = re.compile(r"(\d{1,2})/(\d{1,2})")


def infer_date(month: int, day: int) -> str:
    """연도 없는 날짜를 기준일 이후 가장 가까운 날짜로 바꾼다. 11/25 → 2026-11-25, 1/4 → 2027-01-04."""
    d = date(BASE_DATE.year, month, day)
    return (d if d > BASE_DATE else date(BASE_DATE.year + 1, month, day)).isoformat()


def rule_classify(question: str, plans: list[dict]) -> Classification:
    """키 없이도 평가셋 질문이 돌도록 하는 단순 규칙. 실제 서비스에서는 모델이 한다."""
    q = question
    if re.search(r"해결|대안|방법|조정", q):
        intent = "ADJUST"
    elif re.search(r"규정|조항|계약|통지서|ECN", q):
        intent = "QA"
    else:
        intent = "IMPACT"
    ops: list[WhatifOp] = []
    po = re.search(r"PO-\d{4}-\d{3}", q)
    if po and "입고" in q:
        dates = MD.findall(q)
        if dates:
            ops.append(WhatifOp(op="request_due_date_change", po_id=po.group(),
                                new_due_date=infer_date(*map(int, dates[-1]))))
        return Classification(intent=intent, whatif=ops)
    for clause in re.split(r"(?<=고)\s+|,\s*|그리고\s+", q):
        product = next((pid for word, pid in PRODUCT_WORDS.items() if word in clause), None)
        if not product:
            continue
        candidates = [p for p in plans if p["product_id"] == product]
        mentioned = {infer_date(*map(int, m)) for m in MD.findall(clause)}
        target = re.search(r"(\d{1,2})/(\d{1,2})\s*로", clause)
        new_date = infer_date(*map(int, target.groups())) if target else None
        by_date = [p for p in candidates if p["plan_date"] in mentioned - {new_date}]
        plan = by_date[0] if len(by_date) == 1 else candidates[0] if len(candidates) == 1 else None
        if plan is None:
            return Classification(intent=intent, unclear=f"{product} 계획이 여러 개라 어느 생산인지 모릅니다.")
        qty = re.search(r"(\d[\d,]*)\s*대", clause)
        if qty and re.search(r"늘|줄|대로", clause):
            ops.append(WhatifOp(op="change_plan_qty", plan_id=plan["plan_id"], new_qty=int(qty.group(1).replace(",", ""))))
        elif new_date:
            ops.append(WhatifOp(op="reschedule_plan", plan_id=plan["plan_id"], new_date=new_date))
    return Classification(intent=intent, whatif=ops)


# ---------------------------------------------------------------------------
# verify 판정 (contracts 2.4)
# ---------------------------------------------------------------------------

def shortage_key(s: dict) -> tuple:
    return (s.get("item_id"), s.get("plan_id"), s.get("plan_date"))


def actionable(impact: dict) -> list[dict]:
    """해결안이 필요한 결품. orderable=true(지금 발주하면 됨)는 정상 안내라 빼고 넘긴다."""
    return [s for s in impact.get("shortages", []) if not s.get("orderable")]


def option_op(option: dict) -> dict:
    """대안 → whatif 한 건. tool·args를 그대로 쓴다. approval_id는 계산에 필요 없어 뺀다."""
    return {"op": option["tool"], **{k: v for k, v in option["args"].items() if k != "approval_id"}}


def judge(before: dict, after: dict, targets: list[dict]) -> dict:
    """대안을 적용하기 전(before)과 후(after)를 비교해 통과 여부를 정한다.

    통과 = 대상 결품이 모두 사라졌고 **그리고** 새로 생기거나 더 커진 결품이 없다.
    경고(안전재고 미달)는 통과를 막지 않는다 — 결품이 아니라 여유가 줄어든 상태다 (decisions 1.2).
    대신 결과에 남겨 승인 카드와 답변에서 보이게 한다.
    """
    before_by_key = {shortage_key(s): s for s in before.get("shortages", [])}
    after_by_key = {shortage_key(s): s for s in after.get("shortages", [])}
    unresolved = [after_by_key[shortage_key(t)] for t in targets if shortage_key(t) in after_by_key]
    target_keys = {shortage_key(t) for t in targets}
    new_or_worse = [
        s for k, s in after_by_key.items()
        if k not in target_keys and (k not in before_by_key
                                     or s.get("projected_stock", 0) < before_by_key[k].get("projected_stock", 0))
    ]
    return {
        "passed": not unresolved and not new_or_worse,
        "unresolved": unresolved,
        "new_shortages": new_or_worse,
        "warnings_after": after.get("warnings", []),
    }
