"""Read-after-write check for history traces on the isolated, fake-model stack."""
import json
import sys
import time

import integration_scenarios as S


def main(fail=False):
    S.reset_seed()
    task_id = S.start("지금 생산 계획대로 가면 부품 문제 있어?", "planner")
    for _ in range(100):
        task = S.request("GET", f"/api/ai/tasks/{task_id}", "planner")
        traces = task.get("traces") or []
        if task["status"] in {"DONE", "FAILED"} and traces and traces[-1].get("outcome") != "RUNNING":
            break
        time.sleep(0.2)
    else:
        raise AssertionError("terminal trace missing")
    trace = traces[-1]
    assert task["status"] == ("FAILED" if fail else "DONE"), task["status"]
    assert trace["llmCalls"] == 0 and trace["costUsd"] == 0
    assert trace["traceUrl"].startswith("https://smith.langchain.com/"), "LangSmith link not attached"
    assert trace["toolCalls"] > 0
    if fail:
        assert trace["toolErrors"] > 0 and trace["outcome"] == "ERROR"
    print(json.dumps({"ok": True, "taskId": task_id, "status": task["status"], "trace": trace}, ensure_ascii=False))


if __name__ == "__main__":
    main("--failure" in sys.argv)
