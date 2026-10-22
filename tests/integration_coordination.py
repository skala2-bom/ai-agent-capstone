"""Bidirectional requests through real HTTP/queue/agent/MCP; isolated capstone_ai_test DB only."""
import uuid
import psycopg
import integration_scenarios as S


def req(method, path, role="buyer", body=None):
    return S.request(method, path, role, **({"json": body} if body is not None else {}))


def send(task, role, parent=None):
    preview = req("GET", f"/api/coordination/preview?taskId={task}", role)
    body = {"sourceTaskId": task, "expectedFingerprint": preview["fingerprint"], "commandId": str(uuid.uuid4()), "message": "부서 간 협의: 해당 생산에 필요한 변경을 요청합니다."}
    if parent:
        body["parentRequestId"] = parent["id"]
    r = req("POST", "/api/coordination/requests", role, body)
    assert req("POST", "/api/coordination/requests", role, body)["id"] == r["id"]
    return r


def claim(r, role):
    return req("POST", f"/api/coordination/requests/{r['id']}/claim", role, {"expectedRevision": r["revision"]})


def forward_source(case="SC1"):
    task = S.start(next(c["question"] for c in S.CASES if c["id"] == case), "planner")
    S.finish_task(task)
    return send(task, "planner")


def solve(r):
    claim(r, "buyer")
    body = {"coordinationId": r["id"], "question": "이 요청 해결방법 알려줘"}
    task = req("POST", "/api/ai/tasks", body=body)["taskId"]
    assert req("POST", "/api/ai/tasks", body=body)["taskId"] == task
    return task


def forward_completion():
    S.reset_seed()
    r = forward_source("SC2")
    denied = S.HTTP.get(S.BASE + f"/api/ai/tasks/{r['id']}", headers={"Authorization": "Bearer " + S.TOKENS["buyer"]})
    assert denied.status_code == 403
    task = solve(r)
    cards = S.wait_cards(task, 3, "PENDING")
    with psycopg.connect(S.DSN) as conn:
        assert conn.execute("select qty from production_plan where plan_id='PP-03'").fetchone()[0] == 600
        assert conn.execute("select count(*) from change_history").fetchone()[0] == 0
    S.batch_command(task, "CONFIRM", cards[0])
    with psycopg.connect(S.DSN) as conn:
        assert conn.execute("select count(*) from change_history").fetchone()[0] == 0
    for card in cards[1:]: S.batch_command(task, "CONFIRM", card)
    result, command = S.batch_command(task, "EXECUTE")
    assert result["status"] == "COMPLETED", result
    finished, _ = S.finish_task(task)
    assert len(finished["result"]["plan_changes"]) == 2
    assert finished["result"]["remaining_whatif"] == []
    assert "생산계획도 함께 변경" in finished["answer"]
    req("POST", f"/api/ai/tasks/{task}/batch", "approver", command)
    with psycopg.connect(S.DSN) as conn:
        assert conn.execute("select qty from production_plan where plan_id='PP-03'").fetchone()[0] == 900
        assert conn.execute("select qty from production_plan where plan_id='PP-04'").fetchone()[0] == 500
        assert conn.execute("select count(*) from change_history where target_type='PRODUCTION_PLAN'").fetchone()[0] == 2
    final = req("GET", f"/api/coordination/requests/{r['id']}", "planner")
    assert final["status"] == "COMPLETED" and final["unread"]
    S.check_money_hidden(final)
    print("PASS 생산→구매: private history, one solver, three proposals, atomic PO+two plans, notification", flush=True)


def final_rejection():
    S.reset_seed()
    r = forward_source(); task = solve(r)
    S.wait_cards(task, 1, "PENDING")
    r = req("GET", f"/api/coordination/requests/{r['id']}", "approver")
    body = {"commandId": str(uuid.uuid4()), "expectedRevision": r["revision"], "reason": "협력사 발주 변경 불가"}
    req("POST", f"/api/coordination/requests/{r['id']}/reject", "approver", body)
    req("POST", f"/api/coordination/requests/{r['id']}/reject", "approver", body)
    assert S.batch_state(task)["status"] == "REJECTED"
    done, _ = S.finish_task(task)
    assert "최종 거절" in done["answer"]
    with psycopg.connect(S.DSN) as conn:
        assert conn.execute("select count(*) from change_history").fetchone()[0] == 0
    final = req("GET", f"/api/coordination/requests/{r['id']}", "planner")
    assert final["decisionReason"] == body["reason"] and final["unread"]
    req("POST", f"/api/coordination/requests/{r['id']}/read", "planner", {"revision": final["revision"]})
    assert not req("GET", f"/api/coordination/requests/{r['id']}", "planner")["unread"]
    print("PASS 최종 거절: no mutation, persisted reason/read status, closed task and SSE", flush=True)


def reverse_source():
    delayed = req("PUT", "/api/purchase-orders/PO-2610-106/due-date", body={"due_date": "2026-11-27", "reason": "협력사 확정 지연"})
    S.finish_task(delayed["analysis_task_id"])
    return claim(send(delayed["analysis_task_id"], "buyer"), "planner")


def reverse_flow(reject=False):
    S.reset_seed(); r = reverse_source(); base = f"/api/coordination/requests/{r['id']}"
    if reject:
        req("POST", base + "/reject", "planner", {"commandId": str(uuid.uuid4()), "expectedRevision": r["revision"], "reason": "생산 납기 변경 불가"})
    else:
        task = req("POST", "/api/ai/tasks", "planner", {"coordinationId": r["id"], "question": "스위치 11/23 생산을 11/28로 미루면 문제 있어?"})["taskId"]
        S.TASK_OWNERS[task] = "planner"
        result, _ = S.finish_task(task)
        assert result["result"]["intent"] == "IMPACT"
        assert not req("GET", "/api/approvals", "approver")
        preview = req("POST", base + "/plan-preview", "planner", {"expectedRevision": r["revision"], "whatif": result["result"]["whatif"]})
        assert preview["canExecute"], preview
        command = {"commandId": str(uuid.uuid4()), "expectedRevision": r["revision"], "previewId": preview["previewId"], "reason": "생산 일정 협의 완료"}
        assert req("POST", base + "/plan-execute", "planner", command)["status"] == "COMPLETED"
        req("POST", base + "/plan-execute", "planner", command)
    with psycopg.connect(S.DSN) as conn:
        assert str(conn.execute("select due_date from purchase_order where po_id='PO-2610-106'").fetchone()[0]) == "2026-11-27"
        assert str(conn.execute("select plan_date from production_plan where plan_id='PP-03'").fetchone()[0]) == ("2026-11-23" if reject else "2026-11-28")
    assert req("GET", base)["unread"]
    print("PASS 구매→생산: " + ("최종 거절 · 확정 지연 유지" if reject else "AI plan analysis · planner confirmation · atomic plan update"), flush=True)


def linked_request(reject=False):
    S.reset_seed(); parent = reverse_source()
    task = req("POST", "/api/ai/tasks", "planner", {"coordinationId": parent["id"], "question": "허브 11/25로 당기면 문제 있어?"})["taskId"]
    S.TASK_OWNERS[task] = "planner"
    S.finish_task(task)
    child = send(task, "planner", parent)
    assert req("GET", f"/api/coordination/requests/{parent['id']}")["status"] == "WAITING_LINKED"
    if reject:
        child = claim(child, "buyer")
        child = req("POST", f"/api/coordination/requests/{child['id']}/escalate", body={"expectedRevision": child["revision"]})
        req("POST", f"/api/coordination/requests/{child['id']}/reject", "approver", {"commandId": str(uuid.uuid4()), "expectedRevision": child["revision"], "reason": "협력사 추가 변경 불가"})
        original = req("GET", f"/api/coordination/requests/{parent['id']}", "planner")
        assert original["status"] == "IN_PROGRESS" and original["needsAttention"]
        assert original["linkedOutcome"]["reason"] == "협력사 추가 변경 불가"
    else:
        task = solve(child)
        cards = S.wait_cards(task, 2, "PENDING")
        for card in cards:
            S.batch_command(task, "CONFIRM", card)
        result, _ = S.batch_command(task, "EXECUTE")
        assert result["status"] == "COMPLETED", result
        S.finish_task(task)
        for r in (parent, child):
            assert req("GET", f"/api/coordination/requests/{r['id']}", "planner")["status"] == "COMPLETED"
    print("PASS 연결 요청: " + ("구매 최종 거절 → 생산 재검토로 복귀" if reject else "구매→생산→구매 연결 및 두 요청 함께 완료"), flush=True)


if __name__ == "__main__":
    forward_completion()
    final_rejection()
    reverse_flow()
    reverse_flow(reject=True)
    linked_request()
    linked_request(reject=True)
