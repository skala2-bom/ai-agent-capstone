"""격리 DB + 실제 backend/MCP/agent HTTP를 거치는 시나리오 회귀 검사.

이 스크립트는 테스트 DB를 매 시나리오 초기화하고 테스트 승인 카드를 자동 승인한다.
개발 DB에는 실행할 수 없다. 실제 LLM은 tests/README.md의 별도 절차로 검증한다.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

import httpx
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import whatif as W

BASE = os.getenv("BACKEND_URL", "http://backend:8080")
DSN = os.environ["TEST_DB_URL"]
if not DSN.endswith("/capstone_ai_test"):
    raise RuntimeError("초기화·자동 승인은 capstone_ai_test DB에서만 허용합니다.")
CASES = json.loads((ROOT / "eval/golden.json").read_text())["cases"]
TOKENS = {}
TASK_OWNERS = {}
HTTP = httpx.Client(timeout=10)


def request(method, path, role="buyer", **kwargs):
    r = HTTP.request(method, BASE + path, headers={"Authorization": "Bearer " + TOKENS[role]}, **kwargs)
    if r.is_error:
        raise AssertionError(f"{method} {path}: HTTP {r.status_code} {r.text[:400]}")
    return r.json()


def reset_seed():
    # Wait for the command that emitted DONE to finish its durable bookkeeping.
    for _ in range(100):
        with psycopg.connect(DSN) as conn:
            if not conn.execute("select count(*) from agent.workflow_command where status='PENDING'").fetchone()[0]:
                break
        time.sleep(0.1)
    else:
        raise AssertionError("진행 중인 테스트 작업이 남아 있어 초기화하지 않습니다.")
    with psycopg.connect(DSN) as conn:
        if conn.execute("select current_database()").fetchone()[0] != "capstone_ai_test":
            raise RuntimeError("테스트 DB 확인 실패")
        tables = [r[0] for r in conn.execute("select tablename from pg_tables where schemaname='public'")]
        conn.execute(sql.SQL("truncate {} restart identity cascade").format(
            sql.SQL(",").join(sql.Identifier("public", name) for name in tables)))
        conn.execute((ROOT / "db/seed.sql").read_text())
    for role in ("planner", "buyer", "approver"):
        r = HTTP.post(BASE + "/api/auth/login", json={"user_id": role, "password": "1234"})
        r.raise_for_status()
        TOKENS[role] = r.json()["token"]


def assert_subset(expected, actual, path=""):
    if isinstance(expected, dict):
        for key, value in expected.items():
            assert key in actual, f"{path}.{key} missing"
            assert_subset(value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(expected) == len(actual), f"{path}: length {len(actual)} != {len(expected)}"
        for i, (a, b) in enumerate(zip(expected, actual)):
            assert_subset(a, b, f"{path}[{i}]")
    else:
        assert expected == actual, f"{path}: {actual!r} != {expected!r}"


def check_money_hidden(value):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("amount", "unit_price", "added_cost", "saved_amount"):
                assert v is None, f"생산관리 금액 노출: {k}"
            check_money_hidden(v)
    elif isinstance(value, list):
        for v in value:
            check_money_hidden(v)


def planner_expected(value):
    if isinstance(value, dict):
        return {k: None if k in ("amount", "added_cost", "saved_amount", "unit_price")
                else planner_expected(v) for k, v in value.items()}
    if isinstance(value, list):
        return [planner_expected(v) for v in value]
    return value


def review_body(task_id, drafts):
    ready = request("GET", f"/api/ai/tasks/{task_id}").get("reviewReady")
    if not ready or not set(c["approval_id"] for c in drafts).issubset(ready["approvalIds"]):
        return None
    return {"requestId": str(uuid.uuid4()), "reviewId": ready["reviewId"],
            "taskProblem": "생산 일정 변경으로 생긴 결품과 과잉을 함께 해결해야 합니다.",
            "memos": {c["approval_id"]: "협력사 구두 확인 " + c["approval_id"] for c in drafts}}


def submit_drafts(task_id, role="buyer"):
    drafts = [c for c in request("GET", "/api/approvals?status=DRAFT", role) if c["task_id"] == task_id]
    if not drafts:
        return
    assert not any(c.get("previousApprovalId") or c.get("summary", "").startswith("[재제안 ") for c in drafts), "재제안은 구매 검토로 돌아오면 안 됩니다."
    body = review_body(task_id, drafts)
    if body:
        assert all(c["problem"] for c in drafts)
        request("POST", f"/api/ai/tasks/{task_id}/review/submit", role, json=body)


def batch_state(task_id):
    return request("GET", f"/api/ai/tasks/{task_id}/batch", "approver")


def batch_command(task_id, action, card=None, **extra):
    for _ in range(600):
        state = batch_state(task_id)
        if state.get("mode") == "BATCH" and "revision" in state:
            break
        time.sleep(0.1)
    body = {"requestId": str(uuid.uuid4()), "expectedRevision": state["revision"], "action": action,
            "approvalId": card["approval_id"] if card else None, "reason": "", "args": None, **extra}
    request("POST", f"/api/ai/tasks/{task_id}/batch", "approver", json=body)
    for _ in range(600):
        state = batch_state(task_id)
        if state.get("lastRequestId") == body["requestId"] and state.get("status") != "EXECUTING":
            return state, body
        time.sleep(0.1)
    raise AssertionError(f"검토 명령 시간 초과: {action}, {state}")


def finish_task(task_id, *, approve=False, reject_first=False, initial_cards=0, timeout=240):
    decided = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = request("GET", f"/api/ai/tasks/{task_id}", TASK_OWNERS.get(task_id, "buyer"))
        if task["status"] == "FAILED":
            raise AssertionError(f"agent FAILED: {task.get('answer')}")
        if task["status"] == "DONE":
            # 실제 SSE 재접속도 같은 DONE.result 계약을 주는지 확인한다.
            response = HTTP.get(BASE + f"/api/ai/tasks/{task_id}/stream",
                                headers={"Authorization": "Bearer " + TOKENS[task["userId"]]})
            response.raise_for_status()
            events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
            assert events[-1]["type"] == "DONE", events[-1]
            assert events[-1]["result"] == task["result"]
            return task, decided
        if approve:
            submit_drafts(task_id)
        cards = [c for c in request("GET", "/api/approvals?status=PENDING", "approver") if c["task_id"] == task_id]
        if not decided and len(cards) < initial_cards:
            time.sleep(0.2)
            continue
        if not decided and initial_cards:
            with psycopg.connect(DSN) as conn:
                assert conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0] == 0
        cards.reverse()  # Review order must not affect task-wide execution.
        for card in cards:
            if card["task_id"] != task_id or card["approval_id"] in decided:
                continue
            assert approve, "분석/QA가 승인 요청을 만들었습니다."
            assert card["evidence"], "근거 없는 승인 카드"
            assert all(e["uri"].startswith("doc://") and e["version"] == "v1.2" for e in card["evidence"])
            decision = "REJECTED" if reject_first and not decided else "APPROVED"
            state = batch_state(task_id)
            entry = next((e for e in state.get("entries", []) if e.get("approvalId") == card["approval_id"]), None)
            if not entry or entry["reviewStatus"] != "UNREVIEWED":
                continue
            decide_card(task_id, card, decision)
            decided.add(card["approval_id"])
            print(f"  {decision} {card['tool']} {card['args']}", flush=True)
        if approve and cards and batch_state(task_id).get("canExecute"):
            state, _ = batch_command(task_id, "EXECUTE")
            assert state["status"] == "COMPLETED", state
        time.sleep(0.2)
    raise AssertionError(f"작업 시간 초과: {task_id}")


def start(question, role="buyer"):
    task_id = request("POST", "/api/ai/tasks", role, json={"question": question})["taskId"]
    TASK_OWNERS[task_id] = role
    return task_id


def golden_read_only():
    """실행 전에 golden 숫자와 대안까지 현재 DB로 검사한다."""
    reset_seed()
    for case in CASES:
        got = request("POST", "/api/analysis/impact", json={"whatif": case["whatif"]})
        for field in ("shortages", "warnings", "excesses"):
            assert_subset(case["expected"].get(field, []), got.get(field, []), case["id"] + "." + field)
        if case["kind"] == "question":
            opts = request("POST", "/api/analysis/options", json={"whatif": case["whatif"]})
            assert_subset(case["expected"]["options"], opts["options"], case["id"] + ".options")
        print("PASS golden", case["id"], flush=True)


def scenario(case_id):
    reset_seed()
    case = next(c for c in CASES if c["id"] == case_id)
    task, _ = finish_task(start(case["question"], "planner"))
    assert task["result"]["intent"] == "IMPACT"
    assert W.same_ops(task["result"]["whatif"], case["whatif"])
    for field in ("shortages", "warnings", "excesses"):
        assert_subset(planner_expected(case["expected"][field]), task["result"]["impact"].get(field, []), case_id + "." + field)
    check_money_hidden(task["result"])
    assert task["sources"], "영향 분석의 실제 문서 근거가 비었습니다."
    original_impact = request("POST", "/api/analysis/impact", json={"whatif": case["whatif"]})
    task, decisions = finish_task(start(case["question"] + " 결품과 과잉 해결 방법 만들어 줘."), approve=True,
                                  initial_cards={"SC1": 1, "SC2": 3, "SC3": 2, "SC4": 1}[case_id])
    result = task["result"]
    assert result["intent"] == "ADJUST", result
    assert result["impact"] == original_impact, "DONE.result.impact는 최초 분석이어야 합니다."
    expected_tools = {
        "SC1": ["split_po"],
        "SC2": ["split_po", "split_po", "change_po_qty"],
        "SC3": ["cancel_po", "request_due_date_change"],
        "SC4": ["split_po"],
    }[case_id]
    assert sorted(e["tool"] for e in result["executions"]) == sorted(expected_tools), task["answer"]
    assert len(decisions) == len(expected_tools)
    assert all(p["status"] == "EXECUTED" for p in result["proposals"]), result["proposals"]
    assert not result["blocked_targets"], result["blocked_targets"]
    assert not W.actionable(result["post_execution_impact"]), result["post_execution_impact"]
    with psycopg.connect(DSN) as conn:
        executed = conn.execute("select count(*) from approval where status='EXECUTED'").fetchone()[0]
        history = conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0]
        assert executed == history == len(expected_tools)
        if case_id == "SC2":
            assert conn.execute("select qty from purchase_order where po_id='PO-2610-115'").fetchone()[0] == 500
        if case_id == "SC3":
            assert conn.execute("select status from purchase_order where po_id='PO-2610-110'").fetchone()[0] == "CANCELLED"
            assert str(conn.execute("select due_date from purchase_order where po_id='PO-2610-103'").fetchone()[0]) == "2027-01-04"
    print("PASS", case_id, "분석 → RAG → 개별 검토 → task 전체 실행 → 재분석 → SSE", flush=True)


def rejection():
    reset_seed()
    with psycopg.connect(DSN) as conn:
        original_count = conn.execute("select count(*) from purchase_order").fetchone()[0]
    task_id = start("허브를 11/25로 당기면 부족한 MCU 해결 방법 만들어 줘")
    initial = wait_cards(task_id, 1, "PENDING")[0] # 최초 한 번만 구매가 전송한다.
    decide_card(task_id, initial, "REJECTED")
    revised = None
    for _ in range(600):
        # 구매 화면·토큰·전송 API를 사용하지 않고 팀장 목록만 확인한다.
        cards = [c for c in request("GET", "/api/approvals?status=PENDING", "approver") if c["task_id"] == task_id]
        if cards:
            assert len(cards) == 1
            revised = cards[0]
            break
        time.sleep(0.2)
    assert revised, "구매 재검토 없이 재제안이 팀장에게 전달되어야 합니다."
    assert revised["previousApprovalId"] == initial["approval_id"]
    assert revised["taskProblem"] == initial["taskProblem"] and revised["memo"] == initial["memo"]
    assert revised["tool"] == "request_due_date_change"
    progress = request("GET", f"/api/ai/tasks/{task_id}/approval-progress", "approver")
    assert set(progress) == {"progress"} and any(p["approvalId"] == initial["approval_id"] for p in progress["progress"])
    hidden = HTTP.get(BASE + f"/api/ai/tasks/{task_id}", headers={"Authorization": "Bearer " + TOKENS["approver"]})
    assert hidden.status_code == 403, hidden.text
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0] == 0
    decide_card(task_id, revised, "APPROVED")
    state, _ = batch_command(task_id, "EXECUTE")
    assert state["status"] == "COMPLETED", state
    task, _ = finish_task(task_id)
    assert [e["tool"] for e in task["result"]["executions"]] == ["request_due_date_change"], task["answer"]
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select count(*) from purchase_order").fetchone()[0] == original_count
    print("PASS 구매 최초 전송 → 팀장 거절 → 구매 없이 재제안 직접 수신 → 팀장 승인 후 실행", flush=True)


def wait_cards(task_id, count, status=None):
    for _ in range(600):
        submit_drafts(task_id)
        cards = [c for c in request("GET", "/api/approvals", "approver") if c["task_id"] == task_id]
        selected = [c for c in cards if status is None or c["status"] == status]
        if len(selected) >= count:
            return selected
        time.sleep(0.2)
    raise AssertionError(f"승인 카드 {count}건 대기 시간 초과: {cards}")


def decide_card(task_id, card, decision):
    return batch_command(task_id, "REPLAN" if decision == "REJECTED" else "CONFIRM", card,
        reason="테스트: 분할 대신 다른 대안 요청" if decision == "REJECTED" else "")


def mixed_approvals():
    reset_seed()
    case = next(c for c in CASES if c["id"] == "SC2")
    task_id = start(case["question"] + " 결품과 과잉 해결 방법 만들어 줘.")
    cards = wait_cards(task_id, 3, "PENDING")
    first, reject, last = sorted(cards, key=lambda c: c["args"]["po_id"])
    decide_card(task_id, first, "APPROVED")
    assert len(wait_cards(task_id, 3, "PENDING")) == 3, "검토만 완료하면 모든 발주는 변경 전이어야 합니다."
    assert request("GET", f"/api/ai/tasks/{task_id}")["status"] != "DONE"
    decide_card(task_id, reject, "REJECTED")
    decide_card(task_id, last, "APPROVED")
    pending = wait_cards(task_id, 3, "PENDING")
    revised = [c for c in pending if c.get("previousApprovalId") == reject["approval_id"]]
    assert len(revised) == 1 and revised[0]["tool"] == "request_due_date_change"
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0] == 0
    task, _ = finish_task(task_id, approve=True)
    assert task["result"]["approval_summary"] == {"EXECUTED": 3, "REJECTED": 1, "BLOCKED": 0, "WITHDRAWN": 0}
    assert len(task["result"]["executions"]) == 3
    assert any(p.get("replaces") for p in task["result"]["proposals"])
    # Backend delivery retries the identical task execution command.
    with psycopg.connect(DSN) as conn:
        payload = conn.execute("select payload from agent.workflow_command where task_id=%s and kind='BATCH' and payload->>'action'='EXECUTE'",
                               (task_id,)).fetchone()[0]
        count = conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0]
    for _ in range(2):
        reply = HTTP.post(f"http://agent:8000/agent/tasks/{task_id}/batch", json=payload)
        assert reply.status_code == 202 and reply.json()["duplicate"]
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0] == count == 3
    print("PASS 3건 선등록 → 검토·수정 필요 → 해당 품목만 재제안 → task 전체 실행 → 중복 실행 방지", flush=True)


def draft_review():
    """작업 전체 전송/철회, 고정 문제·메모 유지, 팀장 비노출 및 중복 요청을 검증한다."""
    for send in (False, True):
        reset_seed()
        case = next(c for c in CASES if c["id"] == "SC2")
        task_id = start(case["question"] + " 결품과 과잉 해결 방법 만들어 줘.")
        body = None
        for _ in range(600):
            drafts = [c for c in request("GET", "/api/approvals?status=DRAFT") if c["task_id"] == task_id]
            if len(drafts) == 3:
                body = review_body(task_id, drafts)
                if body:
                    break
            time.sleep(0.2)
        assert body and len(drafts) == 3
        assert not [c for c in request("GET", "/api/approvals", "approver") if c["task_id"] == task_id]
        url = f"/api/ai/tasks/{task_id}/review/" + ("submit" if send else "withdraw")
        # 일부 카드만 보내는 요청은 거부되며 아무 카드도 바뀌지 않는다.
        partial = {**body, "memos": {drafts[0]["approval_id"]: "부분 요청"}}
        response = HTTP.post(BASE + url, headers={"Authorization": "Bearer " + TOKENS["buyer"]}, json=partial)
        assert response.status_code == 409, response.text
        saved = request("POST", url, json=body)["cards"]
        replay = request("POST", url, json=body)["cards"]
        assert {c["approval_id"] for c in saved} == {c["approval_id"] for c in replay}
        assert all(c["taskProblem"] == body["taskProblem"] and c["memo"] == body["memos"][c["approval_id"]] for c in saved)
        original = {c["approval_id"]: c["problem"] for c in drafts}
        assert all(c["problem"] == original[c["approval_id"]] for c in saved)
        manager = [c for c in request("GET", "/api/approvals", "approver") if c["task_id"] == task_id]
        assert len(manager) == (3 if send else 0)
        if send:
            for c in manager:
                decide_card(task_id, c, "APPROVED")
            state, _ = batch_command(task_id, "EXECUTE")
            assert state["status"] == "COMPLETED", state
        else:
            for c in saved:
                response = HTTP.get(BASE + f"/api/approvals/{c['approval_id']}", headers={"Authorization": "Bearer " + TOKENS["approver"]})
                assert response.status_code == 403
        task, _ = finish_task(task_id)
        assert task["result"]["approval_summary"] == {"EXECUTED": 3 if send else 0, "REJECTED": 0, "BLOCKED": 0, "WITHDRAWN": 0 if send else 3}
        with psycopg.connect(DSN) as conn:
            assert conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0] == (3 if send else 0)
        print("PASS 작업 전체 " + ("전송 → 팀장 검토 → 전체 실행" if send else "보내지 않음 → 팀장 비노출·실행 없음"), flush=True)


def recovery_prepare():
    reset_seed()
    case = next(c for c in CASES if c["id"] == "SC2")
    task_id = start(case["question"] + " 결품과 과잉 해결 방법 만들어 줘.")
    wait_cards(task_id, 3, "PENDING")
    print("READY restart agent, then run recovery-finish:", task_id, flush=True)


def recovery_finish():
    # Intentionally no reset. Called after an actual test-agent container restart.
    for role in ("planner", "buyer", "approver"):
        r = HTTP.post(BASE + "/api/auth/login", json={"user_id": role, "password": "1234"})
        r.raise_for_status()
        TOKENS[role] = r.json()["token"]
    with psycopg.connect(DSN) as conn:
        task_id = conn.execute("select task_id from ai_task where status='WAITING_APPROVAL' order by created_at desc limit 1").fetchone()[0]
    assert len(wait_cards(task_id, 3, "PENDING")) == 3
    task, _ = finish_task(task_id, approve=True)
    assert len(task["result"]["executions"]) == 3
    assert len(wait_cards(task_id, 3, "EXECUTED")) == 3
    print("PASS 실제 컨테이너 재시작 뒤 기존 3건 재개, 중복 카드·중복 실행 없음", flush=True)


def system_delay():
    reset_seed()
    updated = request("PUT", "/api/purchase-orders/PO-2610-106/due-date",
                      json={"due_date": "2026-11-27", "reason": "테스트 협력사 지연"})
    task, _ = finish_task(updated["analysis_task_id"])
    assert task["trigger"] == "SYSTEM"
    assert task["result"]["intent"] == "IMPACT"
    check_money_hidden(task["result"])
    assert task["result"]["impact"]["shortages"][0]["projected_stock"] == -1000
    print("PASS SC4 협력사 지연 입력 → SYSTEM/PLANNER 자동 분석", flush=True)


def qa():
    reset_seed()
    task, _ = finish_task(start("ECN-2026-014는 MCU 교체야 아니면 도어센서 케이스 교체야?", "planner"))
    assert task["result"]["intent"] == "QA", task["answer"]
    assert any(s["doc_id"] == "ECN-2026-014" for s in task["sources"]), task["sources"]
    assert "케이스" in task["answer"]
    print("PASS QA 검색 → 원문 → 출처", flush=True)


def manual_and_private():
    reset_seed()
    with psycopg.connect(DSN) as conn:
        conn.execute("insert into app_user select 'buyer2', '다른 구매', role, password_hash from app_user where user_id='buyer'")
    TOKENS["buyer2"] = HTTP.post(BASE + "/api/auth/login", json={"user_id": "buyer2", "password": "1234"}).json()["token"]
    updated = request("PUT", "/api/purchase-orders/PO-2610-106", json={
        "qty": 1000, "due_date": "2026-11-19", "reason": "협력사 확정 수량과 입고일 수동 반영"})
    assert updated["qty"] == 1000 and updated["due_date"] == "2026-11-19"
    analysis, _ = finish_task(updated["analysis_task_id"])
    assert analysis["trigger"] == "SYSTEM" and analysis["userId"] == "buyer"
    assert W.same_ops(analysis["result"]["whatif"], [
        {"op": "change_po_qty", "po_id": "PO-2610-106", "new_qty": 1000},
        {"op": "request_due_date_change", "po_id": "PO-2610-106", "new_due_date": "2026-11-19"}])
    with psycopg.connect(DSN) as conn:
        h = conn.execute("select before, after, changed_by, reason from change_history where action='update_purchase'").fetchone()
        assert h[0]["qty"] == 1200 and h[1]["qty"] == 1000 and h[2] == "buyer" and h[3]
    tasks = {analysis["taskId"]: "buyer"}
    roles = ("planner", "buyer", "buyer2", "approver")
    for role in roles:
        task_id = start("ECN-2026-014는 MCU 교체야 아니면 도어센서 케이스 교체야?", role)
        finish_task(task_id)
        tasks[task_id] = role
    for role in roles:
        for suffix in ("", "?mine=false", "?mine=true"):
            listed = request("GET", "/api/ai/tasks" + suffix, role)
            assert listed and all(t["userId"] == role for t in listed), (role, listed)
        for task_id, owner in tasks.items():
            if owner == role:
                assert request("GET", f"/api/ai/tasks/{task_id}", role)["userId"] == role
                continue
            for suffix in ("", "/stream", "/approval-progress"):
                res = HTTP.get(BASE + f"/api/ai/tasks/{task_id}" + suffix,
                               headers={"Authorization": "Bearer " + TOKENS[role]})
                assert res.status_code == 403, (role, owner, suffix, res.status_code)
    print("PASS 입고 수량·당김 일정 수동 저장 → 변경 이력·자동 분석 / 전 역할·동일 역할 간 본인 이력·상세·SSE만 조회", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", choices=["golden", "SC1", "SC2", "SC3", "SC4", "reject", "mixed", "draft", "system", "qa", "manual-private", "recovery-prepare", "recovery-finish"])
    args = parser.parse_args()
    for _ in range(60):
        try:
            if HTTP.get(BASE + "/api/plans").status_code == 401:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    checks = {"golden": golden_read_only, **{s: lambda s=s: scenario(s) for s in ("SC1", "SC2", "SC3", "SC4")},
              "reject": rejection, "mixed": mixed_approvals, "draft": draft_review, "system": system_delay, "qa": qa, "manual-private": manual_and_private,
              "recovery-prepare": recovery_prepare, "recovery-finish": recovery_finish}
    for name in args.only or [k for k in checks if not k.startswith("recovery-")]:
        checks[name]()
    print("ALL REQUESTED CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
