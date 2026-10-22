"""New task-wide review safeguards through real HTTP/queue/agent/MCP, isolated DB only."""
import psycopg
import integration_scenarios as S


def mutations():
    with psycopg.connect(S.DSN) as conn:
        return conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0]


def prepare(case="SC1"):
    question = next(c["question"] for c in S.CASES if c["id"] == case)
    task = S.start(question + " 결품과 과잉 해결 방법 만들어 줘.")
    cards = S.wait_cards(task, {"SC1": 1, "SC2": 3}[case], "PENDING")
    return task, cards


def excluded_problem_blocks_all():
    S.reset_seed()
    task, cards = prepare("SC2")
    for i, card in enumerate(cards):
        S.batch_command(task, "NEEDS_EDIT" if i == 0 else "CONFIRM", card)
    assert mutations() == 0
    state, _ = S.batch_command(task, "EXECUTE")
    assert state["status"] == "BLOCKED" and "문제가 남아" in state["error"], state
    assert mutations() == 0
    state, _ = S.batch_command(task, "CANCEL")
    assert state["status"] == "CANCELLED"
    S.finish_task(task)
    cards = [c for c in S.request("GET", "/api/approvals", "approver") if c["task_id"] == task]
    assert len(cards) == 3 and all(c["status"] == "WITHDRAWN" for c in cards)
    print("PASS 제외한 품목의 문제 잔존 → 전체 실행 차단 → 전체 취소·기록 유지", flush=True)


def no_alternative_then_manual():
    S.reset_seed()
    task, cards = prepare()
    parent = cards[0]
    for _ in range(4):
        state, _ = S.batch_command(task, "REPLAN", parent, reason="이 대안은 불가합니다. 다른 방법을 검토해 주세요.")
        entry = state["entries"][0]
        # 6.47: 여유안이 늘어 대안 없음보다 재제안 한도(LIMIT)에 먼저 닿을 수 있다. 둘 다 AI 대안 소진이다
        if (entry.get("outcome") or {}).get("code") in ("NO_ALTERNATIVE", "LIMIT"):
            break
        cards = [c for c in S.wait_cards(task, 1, "PENDING") if c["approval_id"] == entry["approvalId"]]
        assert len(cards) == 1, state
        parent = cards[0]
    else:
        raise AssertionError("대안 없음 상태에 도달하지 못함")
    assert S.request("GET", f"/api/ai/tasks/{task}")["status"] != "DONE"
    assert mutations() == 0
    state, _ = S.batch_command(task, "MANUAL", parent, reason="협력사 확인 후 전체 입고를 당깁니다.",
        args={"po_id": "PO-2609-001", "new_qty": 500, "new_due_date": "2026-11-25"})
    assert state["entries"][0]["source"] == "MANUAL"
    assert state["entries"][0]["reviewStatus"] == "UNREVIEWED"
    assert mutations() == 0
    manual = S.wait_cards(task, 1, "PENDING")[0]
    assert manual["tool"] == "update_purchase" and manual["previousApprovalId"] == parent["approval_id"]
    S.batch_command(task, "CONFIRM", manual)
    state, _ = S.batch_command(task, "EXECUTE")
    assert state["status"] == "COMPLETED", state
    S.finish_task(task)
    with psycopg.connect(S.DSN) as conn:
        qty, due = conn.execute("select qty,due_date from purchase_order where po_id='PO-2609-001'").fetchone()
    assert qty == 500 and str(due) == "2026-11-25" and mutations() == 1
    print("PASS AI 대안 소진 알림 → 직접 수정안 등록(업무 미변경) → 재검토 → 전체 실행", flush=True)


def conflicting_tasks_and_stale_review():
    S.reset_seed()
    first, a = prepare()
    second, b = prepare()
    S.batch_command(first, "CONFIRM", a[0])
    S.batch_command(second, "CONFIRM", b[0])
    state = S.batch_state(first)
    # Stale manager tab cannot overwrite a newer review.
    response = S.HTTP.post(S.BASE + f"/api/ai/tasks/{first}/batch",
        headers={"Authorization": "Bearer " + S.TOKENS["approver"]},
        json={"requestId": "stale-tab", "expectedRevision": state["revision"] - 1,
              "action": "UNCONFIRM", "approvalId": a[0]["approval_id"]})
    assert response.status_code == 400, response.text
    state, body = S.batch_command(first, "EXECUTE")
    assert state["status"] == "COMPLETED", state
    S.finish_task(first)
    # Same HTTP request is safe even after completion.
    S.request("POST", f"/api/ai/tasks/{first}/batch", "approver", json=body)
    state, _ = S.batch_command(second, "EXECUTE")
    assert state["status"] == "BLOCKED" and "업무 데이터가 변경" in state["error"], state
    assert mutations() == 1
    S.batch_command(second, "CANCEL")
    S.finish_task(second)
    print("PASS 오래된 검토 차단 / 같은 발주의 다른 task 실행 후 재검증 차단 / 중복 실행 방지", flush=True)


def follow_up_multi_item_review():
    """Main's conversation inheritance must create a fresh v3 review, not resume the parent approval."""
    S.reset_seed()
    case = next(c for c in S.CASES if c["id"] == "SC2")
    parent, _ = S.finish_task(S.start(case["question"]))
    child = S.request("POST", "/api/ai/tasks", json={
        "question": "그럼 결품과 과잉 해결 방법 만들어 줘.", "parentTaskId": parent["taskId"]})["taskId"]
    assert child != parent["taskId"]
    cards = S.wait_cards(child, 3, "PENDING")
    state, _ = S.batch_command(child, "CONFIRM", cards[0])
    assert state["mode"] == "BATCH" and len(state["entries"]) == 3
    assert sum(e["reviewStatus"] == "CONFIRMED" for e in state["entries"]) == 1
    assert not state["canExecute"] and mutations() == 0
    assert S.request("GET", f"/api/ai/tasks/{parent['taskId']}")["status"] == "DONE"
    task, _ = S.finish_task(child, approve=True)
    assert S.W.same_ops(task["result"]["whatif"], case["whatif"])
    assert "앞 질문의 가정을 이어서 계산했습니다" in task["answer"]
    assert len(task["result"]["executions"]) == mutations() == 3
    print("PASS 여러 품목 영향 질문 → 이어서 해결 요청 → 새 task의 독립 검토 → 3건 전체 실행", flush=True)


def replan_with_manager_constraint():
    """팀장 조건 재요청 (decisions 6.47): 날짜·수량을 지킨 새 제안 / 불가능한 조건은 이유와 가장 빠른 가능일."""
    S.reset_seed()
    task, cards = prepare()
    state, _ = S.batch_command(task, "REPLAN", cards[0], reason="협력사가 11/22에 150개까지 가능하다고 함",
        args={"new_due_date": "2026-11-22", "split_qty": 150})
    entry = state["entries"][0]
    new = [c for c in S.wait_cards(task, 1, "PENDING") if c["approval_id"] == entry["approvalId"]]
    assert len(new) == 1 and new[0]["tool"] == "split_po", state
    assert new[0]["args"] == {"po_id": "PO-2609-001", "split_qty": 150, "new_due_date": "2026-11-22"}, new[0]
    assert mutations() == 0
    state, _ = S.batch_command(task, "REPLAN", new[0], reason="1월 발주만 쓰고 싶습니다",
        args={"po_id": "PO-2610-108", "new_due_date": "2026-11-25"})
    outcome = state["entries"][0]["outcome"]
    assert outcome["code"] == "NO_ALTERNATIVE" and "2026-12-15 이후" in outcome["detail"], state
    assert mutations() == 0
    S.batch_command(task, "CANCEL")
    S.finish_task(task)
    print("PASS 팀장 조건 재요청 → 그 날짜·수량의 새 제안 / 불가능한 조건은 대안 없음 + 가장 빠른 가능일", flush=True)


if __name__ == "__main__":
    for check in (excluded_problem_blocks_all, no_alternative_then_manual, conflicting_tasks_and_stale_review, follow_up_multi_item_review,
                  replan_with_manager_constraint):
        check()
    print("ALL TASK BATCH CHECKS PASSED", flush=True)
