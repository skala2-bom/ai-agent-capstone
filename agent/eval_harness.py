"""15 paraphrases referencing golden truth + original verify cases, zero LLM calls.

    python eval_harness.py /repo/eval/golden.json

Explicitly refuses real model mode; does not claim model answer quality.
Output is JSON so before/after reports can be compared without editing truth.
"""
import asyncio
import json
from pathlib import Path
import sys
import time

import graph as G
import whatif as W
import observability as O


async def main(path):
    if G.llm is not None:
        print(json.dumps({"error": "FAKE_MODE_REQUIRED", "hint": "AGENT_MODEL_MODE=fake"}))
        return 2
    golden = json.loads(Path(path).read_text())["cases"]
    plans = await G.load_plans({"task_id": "harness-eval", "role": "BUYER"})
    results = []
    for case in golden:
        if case["kind"] != "question":
            continue
        for variant, suffix in enumerate(["", " 영향 분석해 줘.", " 부품 영향을 확인해 줘."]):
            start = time.monotonic()
            got, errors, _ = await G.classify_question(case["question"] + suffix, plans)
            ok = not errors and got.intent == "IMPACT" and W.same_ops([W.normalize(o) for o in got.whatif], case["whatif"])
            results.append({"id": f"{case['id']}-V{variant}", "truth": case["id"], "kind": "classification",
                            "passed": bool(ok), "durationMs": round((time.monotonic() - start) * 1000, 2)})
    for case in golden:
        if case["kind"] != "verify":
            continue
        ok1, before = await G.call_tool({"role": "BUYER"}, "analyze_impact", {"whatif": case["whatif"][:-1]}, quiet=True)
        ok2, after = await G.call_tool({"role": "BUYER"}, "analyze_impact", {"whatif": case["whatif"]}, quiet=True)
        passed = ok1 and ok2 and W.judge(before, after, W.actionable(before))["passed"] == (not case["id"].endswith("FAIL"))
        results.append({"id": case["id"], "truth": case["id"], "kind": "verification", "passed": bool(passed)})
    report = {"mode": "fake", "modelCalls": 0, "modelCostUsd": 0, "promptVersion": O.prompt_version(),
              "total": len(results), "passed": sum(r["passed"] for r in results), "cases": results,
              "unverified": ["real_model_accuracy", "real_model_token_usage", "real_model_cost", "real_embedding_quality"]}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "/repo/eval/golden.json")))
