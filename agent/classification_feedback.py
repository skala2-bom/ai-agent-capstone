"""User-facing classification feedback, separate from model retry instructions."""


def issue(code, message, *, target="질문 전체", field=None, value=None,
          action="같은 질문을 다시 시도해 주세요. 반복되면 작업 ID와 함께 담당자에게 알려 주세요.",
          kind="INTERPRETATION_ERROR"):
    return {"code": code, "kind": kind, "target": target, "field": field,
            "value": value, "message": message, "action": action}


def validation_issue(message, op=None):
    op = op or {}
    target = op.get("po_id") or op.get("plan_id") or "질문 전체"
    field = next((f for f in ("new_due_date", "new_date", "new_qty", "split_qty", "po_id", "plan_id")
                  if f in message), None)
    # Never show instructions intended for the model in the answer.
    friendly = message.split(". 날짜를 지어내지 말고")[0]
    for key, label in {"request_due_date_change": "입고일 변경", "reschedule_plan": "생산일 변경",
                       "change_plan_qty": "생산 수량 변경", "new_due_date": "새 입고일",
                       "new_date": "새 생산일", "new_qty": "새 수량", "split_qty": "분할 수량",
                       "plan_id": "생산계획 번호", "po_id": "발주 번호"}.items():
        friendly = friendly.replace(key, label)
    return issue("INVALID_CONDITION", friendly, target=target, field=field, value=op.get(field))


def feedback(issues, extracted=None):
    return {"kind": "INTERPRETATION_ERROR" if any(i["kind"] == "INTERPRETATION_ERROR" for i in issues) else "NEEDS_INPUT",
            "stage": "CLASSIFY", "issues": issues, "extracted": extracted or {}}


def answer(detail):
    failed = detail["kind"] == "INTERPRETATION_ERROR"
    parts = ["질문 해석 결과를 검증하지 못해 영향 분석을 진행하지 않았습니다." if failed
             else "분석에 필요한 정보를 더 확인해야 합니다.", "중단 단계: 질문의 대상·날짜 확인"]
    for item in detail["issues"]:
        parts += [f"\n확인이 필요한 대상: {item['target']}", item["message"], f"다음 단계: {item['action']}"]
    parts.append("\n이 작업에서는 실제 발주·생산계획을 변경하지 않았습니다.")
    return "\n".join(parts)
