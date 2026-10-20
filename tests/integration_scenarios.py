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


def finish_task(task_id, *, approve=False, reject_first=False, timeout=180):
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
        cards = request("GET", "/api/approvals?status=PENDING", "approver")
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
    task, decisions = finish_task(start(case["question"] + " 결품과 과잉 해결 방법 만들어 줘."), approve=True)
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
    parser.add_argument("--only", nargs="+", choices=["golden", "SC1", "SC2", "SC3", "SC4", "reject", "system", "qa"])
    args = parser.parse_args()
    for _ in range(60):
        try:
            if HTTP.get(BASE + "/api/plans").status_code == 401:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    checks = {"golden": golden_read_only, **{s: lambda s=s: scenario(s) for s in ("SC1", "SC2", "SC3", "SC4")},
              "reject": rejection, "system": system_delay, "qa": qa}
    for name in args.only or checks:
        checks[name]()
    print("ALL REQUESTED CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
