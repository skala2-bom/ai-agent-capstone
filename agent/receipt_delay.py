"""Resolve chat receipt delays against actual open POs; never write business data."""
import re
from datetime import date, timedelta
from pydantic import BaseModel, Field


class DelayMention(BaseModel):
    po_id: str | None = None
    item_name: str | None = Field(None, description="발주 번호가 없으면 질문의 부품명")
    original_due_date: str | None = Field(None, description="질문에 명시된 원래 입고일 YYYY-MM-DD. 없으면 null")
    delay_days: int | None = Field(None, description="원래 입고일에서 늦어지는 일수. 1주=7일. 직접 날짜를 계산하지 않는다")
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


def resolve(mentions, receipts, base):
    ops, errors, seen = [], [], set()
    for mention in mentions:
        d = mention.model_dump() if isinstance(mention, BaseModel) else mention
        candidates = [r for r in receipts if r.get("status") == "OPEN"]
        if d.get("po_id"):
            candidates = [r for r in candidates if r["po_id"].upper() == d["po_id"].upper()]
        elif d.get("item_name"):
            key = name_key(d["item_name"])
            candidates = [r for r in candidates if key in {name_key(r.get("item_name")), name_key(r["item_id"])}]
        else:
            errors.append("지연된 발주 번호 또는 부품명과 원래 입고일을 알려 주세요."); continue
        if d.get("original_due_date"):
            candidates = [r for r in candidates if r["due_date"] == d["original_due_date"]]
        if len(candidates) != 1:
            hint = ", ".join(f"{r['po_id']} ({r['due_date']}, {r['qty']}개)" for r in candidates[:8])
            errors.append("지연된 발주를 하나로 정할 수 없습니다. 발주 번호를 알려 주세요." + (" 후보: " + hint if hint else " 원래 입고일과 현재 발주 상태를 확인하세요.")); continue
        row = candidates[0]
        if row["po_id"] in seen:
            errors.append("같은 발주에 여러 지연 조건이 있습니다. 적용할 조건 하나를 정해 주세요."); continue
        seen.add(row["po_id"])
        try:
            old = date.fromisoformat(row["due_date"])
            days = d.get("delay_days")
            if days is not None and (type(days) is not int or not 1 <= days <= 730):
                raise ValueError()
            relative = old + timedelta(days=days) if days is not None else None
            explicit = date.fromisoformat(d["new_due_date"]) if d.get("new_due_date") else None
            if relative and explicit and relative != explicit:
                raise ValueError()
            new = explicit or relative
            if new is None or new <= old or new <= base:
                raise ValueError()
            ops.append({"op": "request_due_date_change", "po_id": row["po_id"], "new_due_date": new.isoformat()})
        except (ValueError, TypeError, OverflowError):
            errors.append(f"{row['po_id']}: 현재 입고일 {row['due_date']} 이후의 새 날짜 또는 정확한 지연 기간을 알려 주세요.")
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
