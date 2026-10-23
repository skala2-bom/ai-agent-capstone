"""평가셋(eval/golden.json)으로 agent의 두 부분을 시험한다 (contracts 2.8 · eval/README.md).

  1. classify_intent: question 문항마다 뽑은 whatif가 정답 whatif와 같은가
  2. verify: verify 문항마다 판정(통과/실패)이 맞는가. 마지막 op = 대안, 그 앞 = 사용자 변경

실행 (compose가 떠 있을 때):
  docker compose cp eval/golden.json agent:/tmp/golden.json
  docker compose exec agent python eval_golden.py /tmp/golden.json

OPENAI_API_KEY가 있으면 실제 모델로, 없으면 규칙 분류로 돈다. 모델 호출 비용이 든다 (문항당 1~2회).
"""

import asyncio
import json
import sys
from pathlib import Path

import graph as G
import whatif as W
import receipt_delay as D


async def main(path: str) -> int:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))["cases"]
    state = {"task_id": "eval", "role": "BUYER"}
    plans = await G.load_plans(state)
    failed = 0

    print(f"모델: {'OpenAI ' + str(G.llm.model_name) if G.llm is not None and hasattr(G.llm, 'model_name') else ('OpenAI' if G.llm else '가짜(규칙)')}")
    print("\n[1] classify_intent — whatif 추출")
    for c in [c for c in cases if c["kind"] == "question"]:
        try:
            context = {}
            if D.relevant(c["question"]):
                ok, receipts = await G.call_tool(state, "list_receipts", {"status": "OPEN"}, quiet=True)
                if not ok:
                    raise G.ToolUnavailable(receipts)
                context["receipts"] = receipts["purchase_orders"]
            got, errors, _ = await G.classify_question(c["question"], plans, **context)
        except Exception as e:  # 모델 시간 초과 등. 한 문항 때문에 평가 전체가 멈추지 않게 한다
            failed += 1
            print(f"  ERROR {c['id']:5} {type(e).__name__}: {e}")
            continue
        ops = [W.normalize(o) for o in got.whatif]
        ok = not errors and W.same_ops(ops, c["whatif"]) and got.intent == "IMPACT"
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'} {c['id']:5} intent={got.intent} whatif={json.dumps(ops, ensure_ascii=False)}"
              + ("" if ok else f"\n        기대={json.dumps(c['whatif'], ensure_ascii=False)} 오류={errors}"))

    print("\n[2] verify — 해결안 판정")
    for c in [c for c in cases if c["kind"] == "verify"]:
        base, option = c["whatif"][:-1], c["whatif"][-1]
        ok1, before = await G.call_tool(state, "analyze_impact", {"whatif": base}, quiet=True)
        ok2, after = await G.call_tool(state, "analyze_impact", {"whatif": c["whatif"]}, quiet=True)
        if not (ok1 and ok2):
            failed += 1
            print(f"  FAIL {c['id']} 계산 오류: {before if not ok1 else after}")
            continue
        verdict = W.judge(before, after, W.actionable(before))
        expect_pass = not c["id"].endswith("FAIL")
        ok = verdict["passed"] == expect_pass and W.option_op(
            {"tool": option["op"], "args": {k: v for k, v in option.items() if k != "op"}}) == option
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'} {c['id']:12} 판정={'통과' if verdict['passed'] else '실패'} "
              f"(기대 {'통과' if expect_pass else '실패'}) 남은 결품={len(verdict['unresolved'])} "
              f"새 결품={len(verdict['new_shortages'])} 경고={len(verdict['warnings_after'])}")

    print(f"\n결과: {'전부 통과' if not failed else f'{failed}건 실패'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/golden.json")))
