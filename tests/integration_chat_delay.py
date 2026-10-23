"""Buyer chat -> confirmed receipt fact + department request -> planner execution, isolated DB only."""
import uuid
import psycopg
import integration_scenarios as S
from integration_coordination import req, claim

QUESTION = "11/13 와이파이칩 입고일이 일주일 늦어졌어. 문제있나?"


def state():
    with psycopg.connect(S.DSN) as conn:
        return (str(conn.execute("select due_date from purchase_order where po_id='PO-2609-002'").fetchone()[0]),
                conn.execute("select count(*) from change_history").fetchone()[0])


def chat_handoff():
    S.reset_seed()
    task = S.start(QUESTION, "buyer")
    done, _ = S.finish_task(task)
    result = done["result"]
    assert result["intent"] == "IMPACT", done
    assert result["whatif"] == [{"op":"request_due_date_change", "po_id":"PO-2609-002", "new_due_date":"2026-11-20"}], result
    assert result["receipt_delay"]["reported"]
    assert any(s["item_id"] == "WIFI-CHIP" and s["plan_id"] == "PP-01" for s in result["impact"]["shortages"])
    assert state() == ("2026-11-13", 0)
    p = req("GET", f"/api/coordination/preview?taskId={task}")
    assert p["requiresDelayConfirmation"] and not p["planOps"]
    assert state() == ("2026-11-13", 0)
    body = {"sourceTaskId":task, "expectedFingerprint":p["fingerprint"], "commandId":str(uuid.uuid4()),
            "message":"Wi-Fi 칩 입고가 11/20으로 늦어져 플러그 생산 일정 조정이 필요합니다.", "delayReason":"협력사 지연 확정"}
    denied = S.HTTP.post(S.BASE + "/api/coordination/requests", headers={"Authorization":"Bearer " + S.TOKENS["buyer"]}, json=body)
    assert denied.status_code == 400 and denied.json()["error"] == "TASK_REVIEW_CONFLICT", denied.text
    assert state() == ("2026-11-13", 0)
    body["confirmDelay"] = True
    r = req("POST", "/api/coordination/requests", body=body)
    assert req("POST", "/api/coordination/requests", body=body)["id"] == r["id"]
    assert state() == ("2026-11-20", 1)
    assert req("GET", f"/api/ai/tasks/{task}")["supplierDelay"]
    r = claim(r, "planner")
    ptask = req("POST", "/api/ai/tasks", "planner", {"coordinationId":r["id"], "question":"플러그 11/16 생산을 11/21로 미루면 문제 있어?"})["taskId"]
    S.TASK_OWNERS[ptask] = "planner"
    planned, _ = S.finish_task(ptask)
    assert planned["result"]["intent"] == "IMPACT", planned
    preview = req("POST", f"/api/coordination/requests/{r['id']}/plan-preview", "planner",
                  {"expectedRevision":r["revision"], "whatif":planned["result"]["whatif"]})
    assert preview["canExecute"], preview
    command = {"expectedRevision":r["revision"], "previewId":preview["previewId"], "commandId":str(uuid.uuid4()), "reason":"생산 일정 협의 완료"}
    assert req("POST", f"/api/coordination/requests/{r['id']}/plan-execute", "planner", command)["status"] == "COMPLETED"
    assert req("GET", f"/api/coordination/requests/{r['id']}")["unread"]
    assert state() == ("2026-11-20", 2)
    print("PASS chat delay: resolve actual PO/+7 days → read-only analysis/preview → confirmed atomic handoff → planner AI/execute → buyer notification", flush=True)


def hypothetical_and_ambiguous():
    S.reset_seed()
    task = S.start("11/13 와이파이칩 입고가 일주일 늦어지면 문제 있나?", "buyer")
    done, _ = S.finish_task(task)
    assert done["result"]["intent"] == "IMPACT", done
    assert not done["result"]["receipt_delay"]["reported"]
    denied = S.HTTP.get(S.BASE+f"/api/coordination/preview?taskId={task}", headers={"Authorization":"Bearer "+S.TOKENS["buyer"]})
    assert denied.status_code == 400 and denied.json()["error"] == "TASK_REVIEW_CONFLICT", denied.text
    task = S.start("11/20 와이파이칩 입고가 일주일 늦어졌어. 문제있나?", "buyer")
    done, _ = S.finish_task(task)
    assert done["result"]["intent"] == "CLARIFY", done
    assert "PO-2609-005" in done["answer"] and "PO-2609-006" in done["answer"]
    assert state() == ("2026-11-13", 0)
    assert not req("GET", "/api/coordination/requests", "planner")
    print("PASS hypothetical/ambiguous chat: no fact writes, no handoff, exact PO clarification", flush=True)


if __name__ == "__main__":
    chat_handoff()
    hypothetical_and_ambiguous()
