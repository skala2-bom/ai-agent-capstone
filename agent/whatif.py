"""가정 변경(whatif) — 형식, 검증, verify 판정 (contracts 2.2·2.4·2.6).

여기에는 **순수 함수만** 둔다. 네트워크도 모델도 부르지 않으므로 평가셋(eval/golden.json)으로
바로 시험할 수 있다.

whatif 한 건의 모양은 backend `/internal/analysis/impact`가 받는 그대로다.
    {"op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25"}

op 이름은 변경 도구 이름과 같다 (decisions 6.12). 그래서 verify는 compare_options가 준
대안의 tool·args를 **손대지 않고** {"op": tool, **args}로 넣는다 — 모델이 인자를 옮겨 적다
틀릴 여지가 없다 (decisions 6.3).
"""

import json
import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from receipt_delay import DelayMention, name_key

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
PLAN_OPS = {"reschedule_plan", "change_plan_qty"}


def coordination_errors(context: dict, ops: list[dict]) -> list[str]:
    if any(o.get("op") not in PLAN_OPS for o in ops):
        return ["부서 요청의 생산계획 검토에서는 생산 날짜·수량만 바꿀 수 있습니다. 발주 변경은 구매 승인 절차가 필요합니다."]
    if context.get("direction") == "PLAN_TO_BUY":
        expected = sorted((normalize(o) for o in context.get("planOps") or []), key=lambda o: (o.get("plan_id", ""), o["op"]))
        actual = sorted((normalize(o) for o in ops), key=lambda o: (o.get("plan_id", ""), o["op"]))
        if actual != expected:
            return ["생산관리가 전송한 날짜·수량과 다릅니다. 조건 변경은 생산관리의 새 요청이 필요합니다."]
    return []
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
    delay_days: int | None = Field(
        None, description="request_due_date_change에서 새 날짜 없이 '약 1주 늦어진다'처럼 기간만 말할 때의 일수 "
                          "(1주=7, 2주=14, 3일=3). 이때 new_due_date는 비운다. 날짜는 시스템이 원래 입고 예정일에 더한다")
    may_extend: bool | None = Field(
        None, description="이 발주의 지연이 더 길어질 경우를 함께 보자고 하면 true (예: '더 길어지면', '장기화되면')")


class Classification(BaseModel):
    """질문 분류 결과."""
    intent: Literal["IMPACT", "ADJUST", "QA"]
    whatif: list[WhatifOp] = Field(default_factory=list)
    receipt_delays: list[DelayMention] = Field(default_factory=list, description="입고 지연 언급. 원래 날짜/상대 기간은 여기에 추출하고 같은 변경을 whatif에 중복 기재하지 않는다")
    unclear: str | None = Field(
        None, description="계획 ID·날짜·수량을 하나로 정할 수 없을 때만 부족한 정보. "
                          "의도 선택 이유를 쓰지 않는다. 질문에 원래 생산일과 새 날짜가 있어 계획을 특정할 수 있으면 null.")
    follow_up: bool = Field(
        False, description="앞 질문에 이어서 묻는 말이면 true (예: '그럼 해결 방법은?', '거기에 스위치도 900대로 늘리면?'). "
                           "이전 대화가 없거나 새 주제면 false.")


SCENARIO_DAYS = (7, 28, 84)   # 장기화 대비 기간: 1주·4주·12주 고정 (decisions 6.50)


def resolve_delays(ops: list[dict], due_dates: dict[str, str]) -> tuple[list[dict], list[dict], list[str]]:
    """기간으로 말한 입고 지연을 날짜로 바꾸고, 장기화를 볼 발주를 고른다 (decisions 6.50).

    날짜 = 원래 입고 예정일 + delay_days (달력일). 모델은 일수만 뽑고 날짜 계산은 코드가 한다 (6.27·6.49).
    due_dates는 지금 열린 발주의 입고 예정일. 돌려주는 값: (계약 모양 ops, 장기화 대상 [{po_id, base_due}], 오류)
    """
    out, extend, errors = [], [], []
    for raw in ops:
        op = dict(raw)
        if op.get("op") == "request_due_date_change" and (op.get("delay_days") or op.get("may_extend")):
            base = due_dates.get(op.get("po_id"))
            if base is None:
                errors.append(f"{op.get('po_id')}: 아직 입고되지 않은 발주에서 찾지 못했습니다")
                continue
            if op.get("delay_days") and not op.get("new_due_date"):
                op["new_due_date"] = (date.fromisoformat(base) + timedelta(days=op["delay_days"])).isoformat()
            if op.get("may_extend"):
                extend.append({"po_id": op["po_id"], "base_due": base})
        out.append(normalize(op))
    return out, extend, errors


def po_errors(question: str, ops: list[dict], pos: list[dict], allowed: set[str] = frozenset()) -> list[str]:
    """발주 번호 없이 품목·협력사로 말한 질문에서 고른 발주가 맞는지 본다 (decisions 6.51).

    실제 모델이 "세미온반도체 MCU 입고가 1주 늦어지면"에 릴레이 발주를 골랐다 (변형 V5). 그래서:
    질문에 발주 번호가 없으면 그 발주의 품목이 질문에 있어야 하고, 같은 품목의 열린 발주가 여러 건이면
    원래 입고일이 질문에 있어야 한다. 아니면 모델에게 알려 되묻게 한다. pos가 비면 검사하지 않는다.
    allowed는 이어 묻기에서 앞 질문이 이미 정한 발주다 ("그럼 12주까지 가면?").
    """
    by_id = {p["po_id"]: p for p in pos}
    errors = []
    for op in ops:
        po = op.get("po_id")
        if not pos or not po or po in question or po in allowed:
            continue
        p = by_id.get(po)
        if p is None:
            errors.append(f"{po}: 열린 발주 표에 없는 발주임")
            continue
        if not any(name_key(value) in name_key(question) for value in (p["item_id"], p.get("item_name") or p["item_id"])):
            errors.append(f"{po}: 질문에 이 발주의 품목({p.get('item_name') or p['item_id']})이 없음. "
                          "지연된 품목과 발주 번호 또는 원래 입고일을 확인해 주세요.")
            continue
        same = [q for q in pos if q["item_id"] == p["item_id"]]
        m, d = int(p["due_date"][5:7]), int(p["due_date"][8:10])
        if len(same) > 1 and p["due_date"] not in question and f"{m}/{d}" not in question and not re.search(rf"{m}\s*월\s*{d}\s*일", question):
            errors.append(f"{p.get('item_name') or p['item_id']} 열린 발주가 {len(same)}건("
                          + ", ".join(f"{q['po_id']} 입고 {q['due_date']}" for q in same)
                          + ")이라 하나로 정할 수 없습니다. 발주 번호나 원래 입고일을 알려 주세요.")
    return errors


CURRENT_STATE = re.compile(r"현재|지금|확정된")
MORE_DELAY = re.compile(r"더\s*(?:늦|지연|밀)|추가로\s*(?:늦|지연|밀)")


def recorded_match(d: dict, pos: list[dict], question: str = "") -> str | None:
    """질문의 입고 지연 언급이 이미 저장된 입고 지연과 같으면 그 발주 번호 (decisions 6.56).

    팀 시나리오 ③: PO-2609-008 입고 지연(11/27 → 12/4)이 저장된 뒤 "1주 MCU 지연으로 남는 부족"을 물으면, 그 지연을
    다시 더하거나(12/11) MCU 발주가 둘이라 되묻지 않게 한다. "더 늦어지면"처럼 추가 지연을 말하면 쓰지 않는다."""
    if MORE_DELAY.search(question):
        return None
    rec = [p for p in pos if p.get("recorded_delay")]
    key = lambda v: re.sub(r"[^a-z0-9가-힣]", "", str(v or "").lower())
    by_id = {p["po_id"].upper(): p for p in pos}
    if d.get("po_id") and str(d["po_id"]).upper() in question.upper():
        cands = [p for p in rec if p["po_id"].upper() == str(d["po_id"]).upper()]
    elif d.get("po_id") and str(d["po_id"]).upper() in by_id:   # 질문에 없는 번호를 모델이 고른 경우: 그 품목으로 본다
        item = by_id[str(d["po_id"]).upper()]["item_id"]
        cands = [p for p in rec if p["item_id"] == item]
    elif d.get("item_name"):
        cands = [p for p in rec if key(d["item_name"]) in {key(p.get("item_name")), key(p["item_id"])}]
    else:
        return None
    for p in cands:
        r = p["recorded_delay"]
        if d.get("delay_days") == r["days"] or d.get("new_due_date") == r["to"] or d.get("original_due_date") == r["from"]:
            return p["po_id"]
        # 모델이 일수·날짜를 비워 두거나 0으로 넣었어도 질문의 기간("1주")이 저장된 지연과 같으면 그 지연이다
        if not d.get("delay_days") and not d.get("new_due_date") and r["days"] in stated_days(question):
            return p["po_id"]
    return None


def stated_days(question: str) -> set[int]:
    """질문에 적힌 기간(일). 날짜(11/27, 11월 27일)는 빼고 읽는다."""
    text = re.sub(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s*/\s*\d{1,2}|\d{1,2}\s*월\s*\d{1,2}\s*일", "", question)
    days = {int(n) * (7 if u.startswith("주") else 1) for n, u in re.findall(r"(?<![\d.])(\d+)\s*(주|일)", text)}
    return days | ({7} if re.search(r"일주일|한\s*주", text) else set())


def unsupported_delay(d: dict, pos: list[dict], question: str) -> bool:
    """질문에 근거가 없는 입고 지연 추출인가 (6.56). 해결 방법만 물었는데 모델이 다른 부품 발주에 날짜를 만들거나
    빈 지연(일수 0·날짜 없음)을 넣은 적이 있다(실측 ③ 변형). 질문에 그 발주 번호도 품목 이름도 없거나, 품목만 있고
    날짜·기간이 하나도 없으면 근거 없음으로 보고 뺀다."""
    po = str(d.get("po_id") or "").upper()
    if po and po in question.upper():
        return False
    row = next((p for p in pos if p["po_id"].upper() == po), None)
    key = lambda v: re.sub(r"[^a-z0-9가-힣]", "", str(v or "").lower().replace("와이파이", "wifi"))   # receipt_delay.name_key와 같은 규칙
    names = {row.get("item_name"), row["item_id"]} if row else {d.get("item_name")}
    if not any(n and key(n) in key(question) for n in names):
        return True
    return not d.get("new_due_date") and not d.get("delay_days") and not d.get("original_due_date")


ARRIVAL = re.compile(r"들어오|들어온|입고|도착|늦어|지연|밀리")


def supply_premise_errors(question: str, ops: list[dict], pos: list[dict], inherited: set[str] = frozenset()) -> list[str]:
    """질문의 입고 이야기가 가정에서 빠지거나, 바뀌는 게 없는 입고 가정이면 되묻게 한다 (decisions 6.55).

    실측: 앞 질문 없이 "릴레이가 11/27에 들어온다면 PP-03을 11/26으로 옮기면?" → 모델이 릴레이 입고를 빼고
    계산해 "문제가 없습니다"라고 답했다. 열린 릴레이 발주가 둘(11/20·11/27 예정)이라 어느 것인지 정할 수 없다.
    ① 질문에 열린 발주의 품목 이름과 입고 표현이 함께 있는데 이번 가정·이어받은 가정에 그 품목 발주가 없으면,
    ② 입고 지연의 새 날짜가 원래 입고 예정일과 같으면(바뀌는 게 없음) 오류로 알린다. pos가 비면 검사하지 않는다.
    """
    if not pos or not ARRIVAL.search(question):
        return []
    named = {o.get("po_id") for o in ops} | set(inherited)
    covered = {p["item_id"] for p in pos if p["po_id"] in named}
    groups: dict[str, list[dict]] = {}
    for p in pos:
        groups.setdefault(p["item_id"], []).append(p)
    errors = []
    for item, group in groups.items():
        name = group[0].get("item_name") or item
        if item in covered or (name not in question and item not in question):
            continue
        errors.append(f"질문에 {name} 입고 이야기가 있는데 whatif에 {name} 발주가 없음. 열린 {name} 발주: "
                      + ", ".join(f"{p['po_id']} 입고 예정 {p['due_date']}" for p in group)
                      + ". 어느 발주인지 정할 수 없으면 whatif를 비우고 unclear에 발주 번호를 물어라")
    due = {p["po_id"]: p["due_date"] for p in pos}
    for o in ops:
        if o.get("op") == "request_due_date_change" and o.get("po_id") not in inherited \
                and o.get("new_due_date") and due.get(o.get("po_id")) == o.get("new_due_date"):
            errors.append(f"{o['po_id']}는 원래 {o['new_due_date']} 입고 예정이라 바뀌는 것이 없음. "
                          "질문이 말한 입고 변경이 다른 발주라면 그 발주로, 정할 수 없으면 whatif를 비우고 unclear에 물어라")
    return errors


EXTEND_REQUEST = re.compile(r"더\s*길어|장기화|길어질\s*경우|길어지면|더\s*늦어지면|더\s*늘어나면")


def mark_extension(question: str, ops: list[dict], pos: list[dict] | None = None) -> list[dict]:
    """"더 길어질 경우"를 물으면 장기화 비교 대상(may_extend)을 코드로 정한다 (6.50·6.55).

    ① 장기화 표현 바로 앞에 품목 이름이 있으면("MCU 지연이 더 길어질 경우") 그 품목 발주만 — 모델이 확정 지연(릴레이)에도
    표시를 붙인 적이 있다. ② 아니면 모델 표시를 따르고, ③ 모델이 빠뜨렸으면 기간으로 말한 지연(delay_days), 그것도 없으면
    입고 지연이 하나뿐일 때 그 발주. 실제 모델이 may_extend를 3번 중 2번 빠뜨렸다."""
    delays = [o for o in ops if o.get("op") == "request_due_date_change"]
    if not delays or not EXTEND_REQUEST.search(question):
        return ops
    names = {p["po_id"]: p.get("item_name") or p["item_id"] for p in pos or []}
    near = {o.get("po_id") for m in EXTEND_REQUEST.finditer(question) for o in delays
            if names.get(o.get("po_id")) and names[o["po_id"]] in question[max(0, m.start() - 25):m.start()]}
    if near:
        pick = near
    elif any(o.get("may_extend") for o in delays):
        return ops
    else:
        pick = {o.get("po_id") for o in delays if o.get("delay_days")} or ({delays[0].get("po_id")} if len(delays) == 1 else set())
    return [{**o, "may_extend": o.get("po_id") in pick} if o.get("op") == "request_due_date_change" else o for o in ops]

PERIOD = re.compile(r"\d+\s*주")


def restates_previous(ops: list[dict], previous_ops: list[dict]) -> bool:
    """이번 질문이 앞 질문의 가정을 그대로 다시 말하는가 ("릴레이가 11/27에 들어온다면 PP-03을 …").
    그러면 앞 질문에 이어서 묻는 말이다 — 모델이 follow_up을 빠뜨려도 앞 가정(예: MCU 1주 지연)을 잃지 않게 (6.55)."""
    prev = [normalize(o) for o in previous_ops]
    return any(normalize(o) in prev for o in ops)


def plan_effect(before: dict, after: dict) -> dict:
    """생산계획을 바꾸기 전·후 결품 비교 (decisions 6.55). (부품, 생산계획) 단위로 맞춘다 — 일정을 옮기면 날짜가 바뀐다.

    resolved: 전에 있었는데 사라진 결품 / new: 새로 생긴 결품 / remaining: 그대로 남은 결품(전 수량 before_qty 포함)"""
    key = lambda s: (s.get("item_id"), s.get("plan_id"))
    b = {key(s): s for s in before.get("shortages", [])}
    a = {key(s): s for s in after.get("shortages", [])}
    return {"resolved": [s for k, s in b.items() if k not in a],
            "new": [s for k, s in a.items() if k not in b],
            "remaining": [{**s, "before_qty": b[k].get("qty")} for k, s in a.items() if k in b]}


def scenario_ops(ops: list[dict], extend: list[dict], days: int) -> list[dict]:
    """장기화 대상 발주만 '원래 입고 예정일 + days'로 바꾼 가정. 나머지 가정(확정 지연 등)은 그대로 둔다."""
    dates = {e["po_id"]: (date.fromisoformat(e["base_due"]) + timedelta(days=days)).isoformat() for e in extend}
    return [{**o, "new_due_date": dates[o["po_id"]]} if o.get("op") == "request_due_date_change" and o.get("po_id") in dates
            else o for o in ops]


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
            if arg == "new_due_date" and name == "request_due_date_change" and v in (None, "") and op.get("delay_days"):
                if not isinstance(op["delay_days"], int) or op["delay_days"] <= 0:
                    errors.append(f"{i}번째 {name}: delay_days는 1 이상의 정수여야 함")
                continue   # 날짜는 resolve_delays가 원래 입고 예정일에 더해 채운다
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
    try:
        mentioned = {infer_date(*map(int, m)) for m in MD.findall(question)}
    except ValueError:
        return ["질문에 실제로 존재하지 않는 월/일이 있습니다."]
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


MD_KO = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def unstated_date_errors(question: str, ops: list[dict], allowed: list[str] = ()) -> list[str]:
    """whatif의 날짜가 질문(이어 묻기면 앞 가정 포함)에 실제로 적혀 있는지 본다.

    "허브 생산 좀 당겨 보면 어때?"처럼 날짜가 없는 질문에 실제 모델이 11/25를 채워 계산한 적이 있다
    (평가셋 v2 Q17, 개선 전·후 모두). 날짜는 질문에서만 온다 — 없으면 되묻는다 (6.27과 같은 원칙).
    """
    try:
        stated = {infer_date(*map(int, m)) for m in MD.findall(question) + MD_KO.findall(question)}
    except ValueError:
        return []   # 없는 월/일은 consistency_errors가 이미 알린다
    stated |= set(ISO_DATE.findall(question)) | set(allowed)
    return [f"{op['op']}: {arg} {op[arg]}는 질문에 없는 날짜임. 날짜를 지어내지 말고 unclear에 무엇이 빠졌는지 적어라"
            for op in ops for arg in sorted(DATE_ARGS) if op.get(arg) and op[arg] not in stated]


def op_target(op: dict) -> tuple:
    """같은 대상에 같은 종류의 변경인가를 가르는 열쇠. 이어 묻기에서 새 값이 앞 값을 덮어쓴다."""
    return (op.get("op"), op.get("plan_id") or op.get("po_id"))


def merge_ops(previous: list[dict], new: list[dict]) -> list[dict]:
    """앞 질문의 가정에 이번 질문의 변경을 얹는다 (decisions 6.44). 같은 대상·종류는 이번 값으로 바꾼다.

    합치는 건 코드가 한다 — 모델은 이번 질문에서 새로 바뀐 것만 뽑는다. 숫자를 모델이 옮겨 적다 틀리지 않게.
    """
    new = [normalize(o) for o in new]
    replaced = {op_target(o) for o in new}
    return [normalize(o) for o in previous if op_target(normalize(o)) not in replaced] + new


def same_ops(a: list[dict], b: list[dict]) -> bool:
    """두 whatif가 같은가. 순서는 보지 않는다 (같은 계획을 두 번 바꾸는 질문은 없다고 본다)."""
    key = lambda o: tuple(sorted((k, str(v)) for k, v in normalize(o).items()))
    return sorted(map(key, a)) == sorted(map(key, b))


# ---------------------------------------------------------------------------
# 가짜 모델용 규칙 분류 (OPENAI_API_KEY가 없을 때)
# ---------------------------------------------------------------------------

PRODUCT_WORDS = {"허브": "FG-HUB", "스위치": "FG-SWITCH", "플러그": "FG-PLUG", "도어": "FG-DOOR"}
MD = re.compile(r"(\d{1,2})/(\d{1,2})")


# 해결 요청 표현 (decisions 6.48): "해결방안·해결책·해결안 줘", "어떻게 해결해?", "대안 있어?"도 놓치지 않는다.
# "해결안 검수 규정"처럼 해결안을 묻는 문서 질문은 요청 동사가 없어 잡히지 않는다
ADJUST_REQUEST = re.compile(
    r"해결\s*(?:방법|방안|책)|해결안\s*(?:을|좀)?\s*(?:만들|줘|주|알려|찾|제안|내)|해결해\s*줘|어떻게\s*해결"
    r"|대안(?:을|이|은)?\s*(?:좀\s*)?(?:찾|만들|제안|있|줘|주|알려)|발주\s*조정"
    r"|해결할\s*수\s*있|(?:메울|채울|풀|막을)\s*방법")   # "해결할 수 있나요?", "부족을 메울 방법 있어?" (팀 시나리오 ③, 6.56)


def explicit_adjust_request(question: str) -> bool:
    """긴 가정 뒤의 명시적 해결 요청을 모델이 IMPACT로 놓치지 않게 보완한다.

    '해결안 검수 규정' 같은 문서 질문에는 적용하지 않는다. 실행은 여전히 별도 승인을 요구한다.
    """
    return bool(ADJUST_REQUEST.search(question))


def infer_date(month: int, day: int) -> str:
    """연도 없는 날짜를 기준일 이후 가장 가까운 날짜로 바꾼다. 11/25 → 2026-11-25, 1/4 → 2027-01-04."""
    d = date(BASE_DATE.year, month, day)
    return (d if d > BASE_DATE else date(BASE_DATE.year + 1, month, day)).isoformat()


# "그럼 해결 방법은?", "거기에 …", "그 상태에서 …"처럼 앞 질문을 받는 말
FOLLOW_UP = re.compile(r"^\s*(그럼|그러면|거기에|여기에|거기서|그 상태에서|추가로|이어서)|그거|그걸|그것|앞(의|에서)?\s*(질문|가정|결과)")


def rule_classify(question: str, plans: list[dict]) -> Classification:
    """키 없이도 평가셋 질문이 돌도록 하는 단순 규칙. 실제 서비스에서는 모델이 한다."""
    return _rule_classify(question, plans).model_copy(update={"follow_up": bool(FOLLOW_UP.search(question))})


def _rule_classify(question: str, plans: list[dict]) -> Classification:
    q = question
    if re.search(r"해결|대안|방법|조정", q):
        intent = "ADJUST"
    elif re.search(r"규정|조항|계약|통지서|ECN", q):
        intent = "QA"
    else:
        intent = "IMPACT"
    ops: list[WhatifOp] = []
    if intent == "QA":
        return Classification(intent=intent)
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
        qty = re.search(r"(\d[\d,]*)\s*대", clause)
        target = re.search(r"(\d{1,2})/(\d{1,2})\s*로", clause)
        if not target and not (qty and re.search(r"늘|줄|대로", clause)):
            continue  # "플러그 케이스 해결 방법"처럼 변경 없는 질문에서 생산 계획을 추측하지 않는다.
        candidates = [p for p in plans if p["product_id"] == product]
        mentioned = {infer_date(*map(int, m)) for m in MD.findall(clause)}
        new_date = infer_date(*map(int, target.groups())) if target else None
        by_date = [p for p in candidates if p["plan_date"] in mentioned - {new_date}]
        plan = by_date[0] if len(by_date) == 1 else candidates[0] if len(candidates) == 1 else None
        if plan is None:
            return Classification(intent=intent, unclear=f"{product} 계획이 여러 개라 어느 생산인지 모릅니다.")
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
    """긴급 결품과 과잉. 품목·규칙별로 처리하며 orderable 결품은 신규 발주 안내로 남긴다."""
    return ([{**s, "rule_id": "INV-001"} for s in impact.get("shortages", []) if not s.get("orderable")]
            + [{**s, "rule_id": "INV-003"} for s in impact.get("excesses", [])])


def target_key(target: dict) -> str:
    return f"{target.get('target_rule') or target.get('rule_id') or 'INV-001'}|{target['item_id']}"


def option_key(option: dict) -> str:
    """OPT-1은 재계산마다 달라지므로 거절·실패 중복 판정에는 실행 내용을 쓴다."""
    return json.dumps(option_op(option), sort_keys=True, ensure_ascii=False)


def remaining_ops(ops: list[dict], executed: dict) -> list[dict]:
    """이미 DB에 실행한 변경을 다시 가정하지 않는다. 생산계획 가정은 유지한다.

    같은 필드를 덮어쓴 기존 가정도 제거한다. SC4 입고 지연 가정을 실행 후 다시 얹으면
    승인된 납기 당김이 가려진다. 분할 후에는 원 발주의 입고일 가정은 여전히 유효하다.
    """
    tool, po_id = executed["tool"], executed["args"].get("po_id")
    overwritten = {
        "request_due_date_change": {"request_due_date_change"},
        "change_po_qty": {"change_po_qty"},
        "split_po": {"split_po", "change_po_qty"},
        "cancel_po": {"request_due_date_change", "change_po_qty", "split_po", "cancel_po"},
    }.get(tool, set())
    return [o for o in ops if not (o.get("po_id") == po_id and o.get("op") in overwritten)]


def option_op(option: dict) -> dict:
    """대안 → whatif 한 건. tool·args를 그대로 쓴다. approval_id는 계산에 필요 없어 뺀다."""
    return {"op": option["tool"], **{k: v for k, v in option["args"].items() if k != "approval_id"}}


def judge(before: dict, after: dict, targets: list[dict]) -> dict:
    """대안을 적용하기 전(before)과 후(after)를 비교해 통과 여부를 정한다.

    통과 = 대상 품목·규칙의 긴급 결품/과잉이 사라졌고 새로 생기거나 더 커진 결품이 없다.
    경고(안전재고 미달)는 통과를 막지 않는다 — 결품이 아니라 여유가 줄어든 상태다 (decisions 1.2).
    대신 결과에 남겨 승인 카드와 답변에서 보이게 한다.
    """
    before_by_key = {shortage_key(s): s for s in before.get("shortages", [])}
    after_by_key = {shortage_key(s): s for s in after.get("shortages", [])}
    # 같은 품목의 긴급 결품 전체를 확인한다. 다른 대상 품목의 기존 결품은 다음 승인에서 처리한다.
    groups = {target_key(t) for t in targets}
    unresolved = [
        s for s in after.get("shortages", [])
        if f"INV-001|{s['item_id']}" in groups and not s.get("orderable")
    ] + [s for s in after.get("excesses", []) if f"INV-003|{s['item_id']}" in groups]
    new_or_worse = [
        s for k, s in after_by_key.items()
        if k not in before_by_key or s.get("projected_stock", 0) < before_by_key[k].get("projected_stock", 0)
    ]
    return {
        "passed": not unresolved and not new_or_worse,
        "unresolved": unresolved,
        "new_shortages": new_or_worse,
        "warnings_after": after.get("warnings", []),
    }
