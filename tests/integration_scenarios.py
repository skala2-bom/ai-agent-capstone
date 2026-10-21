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
from concurrent.futures import ThreadPoolExecutor

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


def submit_drafts(task_id, role="buyer"):
    """구매 요청 카드는 초안(DRAFT)으로 온다 (decisions 6.33). 요청자가 문제 정의를 확인해 보내야 팀장이 결정할 수 있다."""
    for c in request("GET", "/api/approvals?status=DRAFT", role):
        if c["task_id"] == task_id:
            assert c["problem"], "AI가 문제 정의를 미리 채워야 합니다."
            request("POST", f"/api/approvals/{c['approval_id']}/submit", role, json={"problem": c["problem"], "memo": "테스트 메모"})


def finish_task(task_id, *, approve=False, reject_first=False, initial_cards=0, timeout=240):
    decided = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = request("GET", f"/api/ai/tasks/{task_id}")
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
        cards.reverse()  # Independent approvals must not depend on preparation order.
        for card in cards:
            if card["task_id"] != task_id or card["approval_id"] in decided:
                continue
            assert approve, "분석/QA가 승인 요청을 만들었습니다."
            assert card["evidence"], "근거 없는 승인 카드"
            assert all(e["uri"].startswith("doc://") and e["version"] == "v1.2" for e in card["evidence"])
            decision = "REJECTED" if reject_first and not decided else "APPROVED"
            request("POST", f"/api/ai/tasks/{task_id}/approval", "approver", json={
                "approvalId": card["approval_id"], "decision": decision,
                "reason": "테스트: 분할 대신 다른 대안 요청" if decision == "REJECTED" else None})
            decided.add(card["approval_id"])
            print(f"  {decision} {card['tool']} {card['args']}", flush=True)
        time.sleep(0.2)
    raise AssertionError(f"작업 시간 초과: {task_id}")


def start(question, role="buyer"):
    return request("POST", "/api/ai/tasks", role, json={"question": question})["taskId"]


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
    print("PASS", case_id, "분석 → RAG → 개별 승인 → 실행 → 재분석 → SSE", flush=True)


def rejection():
    reset_seed()
    with psycopg.connect(DSN) as conn:
        original_count = conn.execute("select count(*) from purchase_order").fetchone()[0]
    task, decisions = finish_task(start("허브를 11/25로 당기면 부족한 MCU 해결 방법 만들어 줘"),
                                  approve=True, reject_first=True)
    assert len(decisions) == 2
    assert [e["tool"] for e in task["result"]["executions"]] == ["request_due_date_change"], task["answer"]
    assert "거절 사유" in task["answer"]
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select count(*) from purchase_order").fetchone()[0] == original_count
    print("PASS 거절 후 다른 대안 재승인 (거절한 분할 실행 없음)", flush=True)


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
    return request("POST", f"/api/ai/tasks/{task_id}/approval", "approver", json={
        "approvalId": card["approval_id"], "decision": decision,
        "reason": "테스트: 분할 대신 다른 대안 요청" if decision == "REJECTED" else None})


def mixed_approvals():
    reset_seed()
    case = next(c for c in CASES if c["id"] == "SC2")
    task_id = start(case["question"] + " 결품과 과잉 해결 방법 만들어 줘.")
    cards = wait_cards(task_id, 3, "PENDING")
    first, reject, last = sorted(cards, key=lambda c: c["args"]["po_id"])
    decide_card(task_id, first, "APPROVED")
    wait_cards(task_id, 1, "EXECUTED")
    assert len(wait_cards(task_id, 2, "PENDING")) == 2, "미결정 카드가 독립적으로 대기해야 합니다."
    assert request("GET", f"/api/ai/tasks/{task_id}")["status"] != "DONE"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide_card, task_id, reject, "REJECTED"), pool.submit(decide_card, task_id, last, "APPROVED")]
        for f in futures:
            f.result()
    wait_cards(task_id, 2, "EXECUTED")
    revised = wait_cards(task_id, 1, "PENDING")
    assert len(revised) == 1 and revised[0]["tool"] == "request_due_date_change"
    task, _ = finish_task(task_id, approve=True)
    assert task["result"]["approval_summary"] == {"EXECUTED": 3, "REJECTED": 1, "BLOCKED": 0, "WITHDRAWN": 0}
    assert len(task["result"]["executions"]) == 3
    assert any(p.get("replaces") for p in task["result"]["proposals"])
    # Backend delivery retries an identical decision after it has already executed.
    with psycopg.connect(DSN) as conn:
        payload = conn.execute("select payload from agent.workflow_command where command_id=%s",
                               ("decision:" + first["approval_id"],)).fetchone()[0]
        count = conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0]
    for _ in range(2):
        reply = HTTP.post(f"http://agent:8000/agent/tasks/{task_id}/resume", json=payload)
        assert reply.status_code == 202 and reply.json()["duplicate"]
    with psycopg.connect(DSN) as conn:
        assert conn.execute("select count(*) from change_history where approval_id is not null").fetchone()[0] == count == 3
    print("PASS 3건 선등록 → 1건만 승인·실행 → 동시 승인/거절 → 해당 품목만 재제안 → 중복 재개 무시", flush=True)


def draft_review():
    """구매가 초안을 검토: 1건은 보내지 않고 2건은 메모를 달아 보낸다 → 팀장은 보낸 2건만 보고 승인 (decisions 6.33)"""
    reset_seed()
    case = next(c for c in CASES if c["id"] == "SC2")
    task_id = start(case["question"] + " 결품과 과잉 해결 방법 만들어 줘.")
    for _ in range(600):
        drafts = [c for c in request("GET", "/api/approvals?status=DRAFT", "buyer") if c["task_id"] == task_id]
        if len(drafts) >= 3:
            break
        time.sleep(0.2)
    assert len(drafts) == 3, drafts
    assert not [c for c in request("GET", "/api/approvals", "approver") if c["task_id"] == task_id], "팀장에게 초안이 보이면 안 됩니다."
    assert all(c["problem"] for c in drafts), "문제 정의 초안이 비었습니다."
    keep, *send = sorted(drafts, key=lambda c: c["args"]["po_id"])
    request("POST", f"/api/approvals/{keep['approval_id']}/withdraw", "buyer", json={"memo": "협력사 협의 불가"})
    for c in send:
        request("POST", f"/api/approvals/{c['approval_id']}/submit", "buyer",
                json={"problem": c["problem"] + " (구매 확인)", "memo": "협력사 구두 확인"})
    pending = [c for c in request("GET", "/api/approvals?status=PENDING", "approver") if c["task_id"] == task_id]
    assert len(pending) == 2 and all(c["memo"] == "협력사 구두 확인" and c["problem"].endswith("(구매 확인)") for c in pending)
    for c in pending:
        decide_card(task_id, c, "APPROVED")
    task, _ = finish_task(task_id)
    assert task["result"]["approval_summary"] == {"EXECUTED": 2, "REJECTED": 0, "BLOCKED": 0, "WITHDRAWN": 1}, task["result"]["approval_summary"]
    assert "보내지 않았습니다" in task["answer"], task["answer"]
    print("PASS 초안 3건 → 1건 보내지 않음·2건 메모 달아 요청 → 팀장은 2건만 보고 승인·실행", flush=True)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", choices=["golden", "SC1", "SC2", "SC3", "SC4", "reject", "mixed", "draft", "system", "qa", "recovery-prepare", "recovery-finish"])
    args = parser.parse_args()
    for _ in range(60):
        try:
            if HTTP.get(BASE + "/api/plans").status_code == 401:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    checks = {"golden": golden_read_only, **{s: lambda s=s: scenario(s) for s in ("SC1", "SC2", "SC3", "SC4")},
              "reject": rejection, "mixed": mixed_approvals, "draft": draft_review, "system": system_delay, "qa": qa,
              "recovery-prepare": recovery_prepare, "recovery-finish": recovery_finish}
    for name in args.only or [k for k in checks if not k.startswith("recovery-")]:
        checks[name]()
    print("ALL REQUESTED CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
