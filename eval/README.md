# 평가셋 (`golden.json`)

> **이 파일이 SC1~SC4 정답의 유일한 기준이다.** 기획안 PDF의 숫자가 아니다.
> backend 골든셋 테스트와 AI 파트의 Harness 평가가 **같은 파일**을 읽는다.

## 왜 한 파일인가

정답이 여러 곳에 있으면, seed나 판정 규칙이 바뀔 때 한 곳만 고쳐지고 나머지는 낡은 채 남는다. 오류가 나지 않으니 아무도 모른다.
실제로 기획안 PDF의 SC3 정답(BLE 칩·홀 센서 과잉 800)은 과잉 기간 규칙 때문에 계산에서 나오지 않는다 ([decisions.md 1.3·7.3](../docs/decisions.md)). PDF를 보고 평가셋을 만들면, AI가 맞게 답해도 오답 처리된다.

이 파일은 backend 테스트(`cd backend && mvn test`)가 매번 검증한다. **seed를 바꿨는데 여기를 안 고치면 테스트가 깨진다.** 그래서 이 파일의 숫자는 항상 지금 계산과 같다.

## 문항 하나의 모양

```jsonc
{
  "id": "SC1",
  "kind": "question",            // question = 사람이 묻는 문항 / verify = 해결안 검증 문항 (질문 없음)
  "title": "허브만 당겼는데 플러그가 멈춘다",
  "question": "허브를 11/25로 당기면 문제 있어?",            // ── C 파트
  "role": "PLANNER",                                          // ── C 파트 (요청자 역할)
  "expected_tools": ["analyze_impact"],                       // ── C 파트 (모델이 불러야 할 도구)
  "whatif": [{ "op": "reschedule_plan", "plan_id": "PP-05", "new_date": "2026-11-25" }],   // ── B 파트
  "expected": {                                               // ── B 파트 (계산 결과, 마스킹 전)
    "shortages": [{ "item_id": "MCU", "plan_id": "PP-05", "plan_date": "2026-11-25", "projected_stock": -100, "orderable": false }, ...],
    "warnings": [],
    "excesses": [],
    "options": [{ "item_id": "MCU", "type": "SPLIT_PO", "tool": "split_po", "args": {...}, "added_cost": 390000, "risk": "MID", "resolves": true, "recommended": true }, ...],
    "recommended_option_id": "OPT-1"
  }
}
```

| 칸 | 주인 | 뜻 |
|---|---|---|
| `question` · `role` · `expected_tools` | **C** | 사용자가 할 말, 누가 묻나, 모델이 어떤 도구를 불러야 하나 |
| `whatif` | **B** | 질문을 계산 입력으로 바꾸면 이렇게 된다. **C에게는 `classify_intent`가 뽑아야 할 정답**이기도 하다 |
| `expected` | **B** | `analyze_impact`에 `whatif`를 넣었을 때 나와야 하는 결과. `options`·`recommended_option_id`는 `compare_options`에 같은 `whatif`를 넣었을 때의 결과 (question 문항만). 적힌 칸만 비교하고, 순서까지 같아야 한다 |
| `id` · `kind` · `title` | 둘 다 | 새 문항을 넣는 쪽이 채운다 |

- `expected`의 금액(`amount`)은 **마스킹 전** 값이다. PLANNER가 받으면 `null`이 된다 (contracts 1.2).
- `verify` 문항은 사용자 질문이 아니다. agent의 verify 노드가 맞게 판정하는지 보는 용도다 (contracts 2.4). `SC1-FIX-FAIL`은 **verify가 실패로 걸러야 하는** 오답 해결안이다.

## 문항 늘리기 (기획안 D4: 10~20개)

1. **C 파트**가 질문을 쓴다. 기존 시나리오의 날짜·수량을 바꾼 변형이면 된다 (예: "허브를 11/28로 당기면?").
2. `whatif`까지 적고 `expected`는 비워 둔 채(`{}`) B 파트에 넘긴다.
3. **B 파트**가 계산으로 `expected`를 채우고 `mvn test`로 확인한 뒤 커밋한다.
4. 숫자를 손으로 계산해 넣지 않는다. 손계산은 기획안 PDF가 이미 한 번 틀린 길이다.

## 평가는 새 DB에서

승인·실행을 해 보면 DB의 발주가 실제로 바뀐다. 그 상태로 평가를 돌리면 "이미 풀린 결품" 때문에 해결안 검증 문항이 실패한다. 평가 전에 DB를 처음 상태로 돌린다.

```bash
docker compose down -v && docker compose up -d --build
```

## 쓰는 곳

| 누가 | 어떻게 |
|---|---|
| backend | [`ImpactAnalysisTest`](../backend/src/test/java/capstone/ImpactAnalysisTest.java)가 문항마다 `whatif` → 계산 → `expected` 비교 |
| AI 파트 Harness 평가 | `question`을 agent에 넣고 ① 부른 도구가 `expected_tools`인가 ② 뽑은 whatif가 `whatif`와 같은가 ③ 답변에 `expected`의 숫자·품목·날짜가 들어 있고 없는 숫자를 지어내지 않았나 ④ 해결 방법을 물으면 `recommended=true`인 대안을 골랐나 |
| 프론트 | 시연 질문 예시 버튼에 `question`을 그대로 쓸 수 있다 |
| 발표 | "계산 엔진 테스트와 AI 평가가 같은 정답 파일을 쓴다" — 기능 적합성과 Harness 평가의 근거가 하나다 |
