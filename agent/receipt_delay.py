"""Resolve chat receipt delays against actual open POs; never write business data."""
import re
from datetime import date, timedelta
from pydantic import BaseModel, Field
from classification_feedback import issue


class DelayMention(BaseModel):
    po_id: str | None = None
    item_name: str | None = Field(None, description="발주 번호가 없으면 질문의 부품명")
    original_due_date: str | None = Field(None, description="질문에 명시된 원래 입고일 YYYY-MM-DD. 없으면 null")
    delay_days: int | None = Field(None, description="사용자가 기간을 직접 말한 경우만 지연 일수. 1주=7일. 새 입고일이 명시되면 null. 날짜 차이를 계산해서 채우지 않는다")
    new_due_date: str | None = Field(None, description="새 입고일이 명시된 경우만 YYYY-MM-DD. 상대 기간이면 null")


def relevant(question):
    return bool(re.search(r"입고|납품|납기|발주|PO-\d", question, re.I) and re.search(r"늦|지연|미루|미뤄|연기|일주일|한\s*주|\d+\s*(?:주|일).*뒤", question))


def reported(question):
    if re.search(r"아니|않|취소|미확정|불확실", question):
        return False
    # A question about a reported fact may end in '문제 있나?'. Uncertainty stays hypothetical.
    if re.search(r"만약|가정|가능성|예상|추정|약\s*(?:\d|한|일주일)|수\s*(?:있|도)|늦어지면|늦어진다면|늦어질|지연되면|지연될|미루면|바꾸면|확정되면", question):
        return False
    return bool(re.search(r"늦어졌|늦어진대|늦어진다고|늦어진다|늦어집|늦습니다|지연됐|지연되었|지연된대|지연됩니다|확정|통보|연기됐|미뤄졌", question))


def name_key(value):
    value = str(value or "").lower().replace("와이파이", "wifi")
    return re.sub(r"[^a-z0-9가-힣]", "", value)


def calendar_date(value, base):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    month, day = map(int, value.split("/"))
    d = date(base.year, month, day)
    return d if d >= base else date(base.year + 1, month, day)


def stated_delay_days(question):
    """Only user-stated days/weeks may authorize a code-calculated receipt date.

    Remove calendar dates so '11월 13일' cannot be misread as a 13-day duration.
    This does not authorize any production date or another PO's date.
    """
    text = re.sub(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s*/\s*\d{1,2}|(?:\d{4}\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일", "", question)
    days = {int(n) * (7 if unit.startswith("주") else 1)
            for n, unit in re.findall(r"(?<![\d.])(\d+)\s*(주(?:일)?|일)", text)}
    if re.search(r"일주일|한\s*주(?:일)?", text):
        days.add(7)
    return days


def resolve(mentions, receipts, base, issues=None):
    ops, errors, seen = [], [], set()
    for mention in mentions:
        d = mention.model_dump() if isinstance(mention, BaseModel) else mention
        target = " · ".join(str(d[k]) for k in ("item_name", "po_id") if d.get(k)) or "입고 지연 대상"

        def fail(code, message, field, action, *, kind="NEEDS_INPUT"):
            errors.append(f"{target}: {message}")
            if issues is not None:
                issues.append(issue(code, message, target=target, field=field, value=d.get(field), action=action, kind=kind))

        candidates = [r for r in receipts if r.get("status") == "OPEN"]
        if d.get("po_id"):
            candidates = [r for r in candidates if r["po_id"].upper() == d["po_id"].upper()]
        elif d.get("item_name"):
            key = name_key(d["item_name"])
            candidates = [r for r in candidates if key in {name_key(r.get("item_name")), name_key(r["item_id"])}]
        else:
            fail("MISSING_RECEIPT", "입고 지연 대상을 찾을 정보가 없습니다.", "po_id", "지연된 발주 번호 또는 부품명과 원래 입고일을 알려 주세요.")
            continue
        if d.get("original_due_date"):
            candidates = [r for r in candidates if r["due_date"] == d["original_due_date"]]
        if len(candidates) != 1:
            hint = ", ".join(f"{r['po_id']} ({r['due_date']}, {r['qty']}개)" for r in candidates[:8])
            fail("AMBIGUOUS_RECEIPT" if candidates else "RECEIPT_NOT_FOUND",
                 "지연된 발주를 하나로 정할 수 없습니다." + (" 후보: " + hint if hint else f" 원래 입고일 {d.get('original_due_date') or '미지정'}에 해당하는 미입고 발주가 없습니다."),
                 "po_id", "발주 번호를 지정해 주세요." if candidates else "부품명·발주 번호·원래 입고일과 현재 발주 상태를 확인해 주세요.")
            continue
        row = candidates[0]
        target = f"{row.get('item_name', row['item_id'])} · {row['po_id']}"
        if row["po_id"] in seen:
            fail("DUPLICATE_RECEIPT", "같은 발주에 여러 지연 조건이 있습니다.", "po_id", "해당 발주에 적용할 새 입고일 하나를 확인해 주세요.")
            continue
        seen.add(row["po_id"])
        try:
            old = date.fromisoformat(row["due_date"])
            days = d.get("delay_days")
            if days is not None and (type(days) is not int or not 1 <= days <= 730):
                raise ValueError()
            relative = old + timedelta(days=days) if days is not None else None
            explicit = date.fromisoformat(d["new_due_date"]) if d.get("new_due_date") else None
            if relative and explicit and relative != explicit:
                fail("CONFLICTING_RECEIPT_DATES",
                     f"현재 입고일 {old}에서 {days}일 지연으로 해석하면 {relative}이지만, 추출한 새 입고일은 {explicit}입니다.",
                     "new_due_date", "입력한 새 입고일과 지연 기간이 일치하는지 확인해 주세요. 입력이 맞다면 같은 질문을 다시 시도해 주세요.",
                     kind="INTERPRETATION_ERROR")
                continue
            new = explicit or relative
            if new is None:
                fail("MISSING_RECEIPT_DATE", f"현재 입고일은 {old}이며, 새 입고일 또는 지연 기간이 필요합니다.",
                     "new_due_date", f"예: {row['po_id']} 입고가 며칠 늦어지는지 또는 새 입고일을 알려 주세요.")
                continue
            if new <= old or new <= base:
                raise ValueError()
            ops.append({"op": "request_due_date_change", "po_id": row["po_id"], "new_due_date": new.isoformat()})
        except (ValueError, TypeError, OverflowError):
            fail("INVALID_RECEIPT_DATE",
                 f"현재 입고일은 {row['due_date']}입니다. 추출한 새 입고일: {d.get('new_due_date') or '없음'}, 지연 일수: {d.get('delay_days') if d.get('delay_days') is not None else '없음'}.",
                 "new_due_date", "현재 입고일 이후의 실제 날짜 또는 1~730일 사이의 지연 기간을 확인해 주세요.")
    return ops, errors


def fake_mentions(question, receipts, base):
    """Deterministic fixture parser; live mode uses structured model extraction."""
    mentions = []
    for clause in re.split(r"[.!?\n]|,\s*|그리고\s+", question):
        if not re.search(r"늦|지연|미루|미뤄|연기", clause):
            continue
        po = re.search(r"PO-\d{4}-\d{3}", clause, re.I)
        names = {r.get("item_name", r["item_id"]) for r in receipts}
        item = next((n for n in sorted(names, key=len, reverse=True) if name_key(n) in name_key(clause)), None)
        dated = re.sub(r"(\d{1,2})월\s*(\d{1,2})일", r"\1/\2", clause)
        dates = re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}", dated)
        duration = re.search(r"(\d+)\s*(주|일)(?!\s*(?:로|에))|일주일|한\s*주", clause)
        days = (int(duration[1]) * (7 if duration[2] == "주" else 1) if duration[1] else 7) if duration else None
        mentions.append(DelayMention(po_id=po.group().upper() if po else None, item_name=item,
            original_due_date=calendar_date(dates[0], base).isoformat() if dates and (days or len(dates) > 1) else None,
            delay_days=days, new_due_date=calendar_date(dates[-1], base).isoformat() if dates and not days else None))
    return mentions


def snapshot(question, ops, receipts):
    if not ops or any(o.get("op") != "request_due_date_change" for o in ops):
        return None
    changes = []
    for op in ops:
        row = next((r for r in receipts if r["po_id"] == op["po_id"]), None)
        if not row or op["new_due_date"] <= row["due_date"]:
            return None
        before = {k: row[k] for k in ("po_id", "item_id", "qty", "due_date", "status")}
        changes.append({"poId": row["po_id"], "itemName": row.get("item_name", row["item_id"]),
                        "before": before, "after": {**before, "due_date": op["new_due_date"]}})
    return {"reported": reported(question), "changes": changes}
