# Harness 구현과 검증

기준일: 2026-10-21. 사용자 지정 추가 API 예산: **$0**.
실제 유료 LLM·임베딩 호출을 하지 않았다. 모델 복구·토큰 집계는 모의 응답으로 검증했고,
LangSmith 연결과 실패 추적의 도착은 실제 서비스로 확인했다.

## 적용 구조

| 요소 | 실제 코드 | 검증 |
|---|---|---|
| Instructions | `agent/prompts/*.md`, `observability.prompt_version()` | 고정 분류 지시도 Markdown으로 이동. 실행마다 프롬프트 SHA-256 앞 12자리 기록 |
| Tools | `graph.call_tool()`, `context_budget.tool_content()` | 실제 MCP 호출별 span, 반환 오류도 실패 표시. 모델용 ToolMessage 최대 4,000자. 원본 상태는 그대로 유지 |
| Context | `context_budget.prepare_messages()` | 입력 기본 12,000 tokens, 출력 기본 최대 1,500 tokens. 오래된 완결 대화를 발췌 요약하고 현재 질문·도구 호출 쌍 유지. 필수 입력도 초과하면 모델 호출 전에 중단 |
| State·Memory | PostgreSQL checkpointer, `ApprovalStore`, `ApprovalRunner` | 다건 카드 선등록, 역순/부분 승인, 재시작 재개, 중복 명령/실행 차단 회귀 검사 |
| Agent Loop | `graph.next_turn()`, `approval_runner.WORK_TIMEOUT` | 일반 8턴, ADJUST 기본 64턴, 실행 command 구간 600초, 그래프 600단계. 사람 승인 대기는 제외. 모델이 선택하는 같은 도구는 3회, 대상 10개·총 제안 30개 |
| Guardrails | approval gate, backend 승인 검증, `graph.mask()` | 승인 전 변경 차단, 비용 변경 후 실행 보류, 금액 역할별 숨김. 응답 불확실한 쓰기는 자동 재실행 금지 |
| Recovery | `model_runtime.invoke()` | SDK 재시도는 0으로 두고 429/503을 애플리케이션에서 최대 2회 재시도. 설정된 대체 모델에도 도구/출력 스키마를 각각 바인딩. 401 등 설정 오류는 재시도하지 않음 |
| Observability | `observability.py`, TRACE outbox, `Tasks.java`, History 화면 | 노드·도구·사용량·시간·복구 횟수. 실행 회차별 실제 SDK 추적 링크. 개인정보·금액·자유 텍스트는 외부 추적에서 제외 |

도구 스키마는 설치된 `ChatOpenAI.get_num_tokens_from_messages()`에서 계산되지 않으므로 별도 크기 예산에 포함한다.
모델 메시지는 로컬 토큰 계산을 사용하고, 지원되지 않으면 UTF-8 바이트 수로 보수적으로 계산한다.
대화 요약은 유료 요약 모델을 쓰지 않는 발췌 요약이다. 숫자를 새로 만들지 않고 업무의 정답·승인 상태는 구조화 State에서 보존한다.

## ID와 이벤트 계약

- `taskId`: 업무 요청 한 건. `threadId`: checkpoint·재개 단위.
- `traceId`: 한 durable command 실행의 LangSmith root ID. 재시작 재시도·다른 승인 결정에는 새 ID를 발급한다.
- `previousTraceId`: 같은 작업의 직전 실행 ID. LangSmith metadata의 `thread_id`로 같은 작업을 묶는다.
- `proposalId`/`approvalId`는 기존 승인 계약을 유지한다. 추적 정보로 승인·실행 중복 판단을 바꾸지 않는다.
- `TRACE` 이벤트는 업무 상태를 변경하지 않는다. 처음에는 RUNNING, 끝에는 실행 통계를 담아 저장한다.
- 이벤트 fingerprint는 기존 업무 payload로 계산한 다음 trace 정보를 첨부한다. 재실행 시 trace ID가 달라졌다는 이유로 기존 업무 이벤트를 다시 전달하지 않는다.
- 최종 TRACE → DONE/FAILED 저장 → command 완료 순서다. command 완료 전에 결과 이벤트를 영구 저장해 유실 구간을 만들지 않는다.
- `WAITING_APPROVAL`은 정상 중단, `HELD`는 확인이 필요한 불확실한 실행, `PARTIAL`은 일부 제안이 보류된 결과다.
- `GET /api/ai/tasks`에는 최신 `trace`, 상세 조회에는 실행별 최신 통계 `traces[]`를 추가한다. 기존 접근 권한을 유지하고 링크는 HTTPS LangSmith 도메인만 허용한다.
- 조회하지 못한 토큰·비용은 0으로 꾸미지 않는다. 비용은 실제 LLM 호출이 없을 때만 0이며, 나머지는 LangSmith에서 확인한다.

## 실제 결과

| 항목 | 보완 전 | 보완 후 |
|---|---:|---:|
| 동일한 무료 분류·검증 평가 | 18/18 | 18/18 |
| 골든셋 + 실제 서비스 업무 시나리오 | 16/16 | 16/16 |
| 모델용 도구 결과 제한 | 전체 경로 미적용 | 실제 ToolMessage 경로 적용 |
| Context 입력 예산·요약 | 없음 | 초과 시 요약 또는 호출 차단 |
| LLM 복구 검증 | SDK 설정 중심 | 재시도/대체/최종 실패 모의 검증 |
| 실제 실패 작업의 LangSmith 링크 | 없음 | 기록·조회 및 외부 오류 span 확인 |

18문항은 정답 숫자를 복제하지 않고 `eval/golden.json`의 5개 질문에 대한 3개 표현과 기존 verify 3개를 참조한다.
가짜 모델의 규칙 처리 성능이며 **실제 LLM 정답률 개선을 의미하지 않는다**.

- [보완 전 평가 JSON](../eval/reports/harness-before.json)
- [보완 후 평가 JSON](../eval/reports/harness-after.json)
- [검증 요약](../eval/reports/validation-summary.json) — Python 54 · backend 29 · frontend 38 테스트 통과, 프론트 빌드 성공
- [정상 작업 추적](../eval/reports/tracing-success.json)
- [실패 작업 추적](../eval/reports/tracing-failure.json)
- [실제 컨테이너 재시작 검증](../eval/reports/restart-recovery.log)
- [재시작 전후 연결된 추적 목록](../eval/reports/restart-traces.json)
- [Python 테스트 기록](../eval/reports/python-tests.log)

## 재현: 비용 없는 테스트

기존 `tests/compose.yml`은 LLM 키를 비우고 `AGENT_MODEL_MODE=fake`, `LANGSMITH_TRACING=false`를 강제한다.
RAG도 테스트 전용 결정적 임베딩을 쓴다. 개발 DB와 프로젝트 이름·볼륨을 공유하지 않는다.

```bash
docker compose -p capstone-harness-check -f tests/compose.yml up -d --build --wait
docker compose -p capstone-harness-check -f tests/compose.yml run --rm tests python -m unittest -v test_harness test_ai_rag test_approval_runner
docker compose -p capstone-harness-check -f tests/compose.yml run --rm tests python -u integration_scenarios.py
docker compose -p capstone-harness-check -f tests/compose.yml run --rm tests python /repo/agent/eval_harness.py /repo/eval/golden.json
```

실제 컨테이너 재시작은 기존 테스트 환경에서 다음 순서로 재현한다.

```bash
docker compose -p capstone-harness-check -f tests/compose.yml run --rm tests python -u integration_scenarios.py --only recovery-prepare
docker compose -p capstone-harness-check -f tests/compose.yml restart agent
docker compose -p capstone-harness-check -f tests/compose.yml run --rm tests python -u integration_scenarios.py --only recovery-finish
```

개발 서비스에 적용하려면 `.env`의 LangSmith 설정과 `docker-compose.yml`의 전달 설정을 사용한다.
모델 비용 없이 실행하려면 **재시작 전에** `AGENT_MODEL_MODE=fake`를 설정하고, RAG 임베딩 호출도 별도로 통제해야 한다.
`AGENT_MODEL_MODE=fake`는 Agent만 통제하며 운영 rag-mcp의 외부 임베딩 API까지 끄지는 않는다.
`FALLBACK_MODEL`이 비어 있으면 대체 호출 없이 주 모델 재시도 소진 후 오류 처리한다.
실제 모델을 사용할 때에는 이 값을 지원되는 다른 모델로 설정하고 도구 호출·구조화 출력 호환성을 별도 검증한다.
현재 실행 중인 개발 컨테이너와 DB는 이번 작업에서 교체하거나 초기화하지 않았다.

무료 테스트 화면은 `http://localhost:3100`이다. 시연 계정 `buyer` / `1234`로 로그인해 작업 이력의 개발 상세를 연다.
재현 설정은 `tests/compose.harness.yml`에 있으며 Agent는 fake 모드, RAG는 fixture를 유지한다.

```bash
docker compose --env-file .env -p capstone-harness-current -f tests/compose.yml -f tests/compose.harness.yml up -d --build --wait
```

이 명령은 별도 테스트 프로젝트를 사용한다. 기존 개발용 `docker compose up`과 구분한다.

## 발표 시연 순서

1. 정상 분석의 작업 이력 → 개발 상세 → 실행 시간·호출 수와 LangSmith 링크.
2. LangSmith의 노드/도구 계층. 자유 텍스트·민감값이 제거된 것을 함께 설명.
3. 테스트 모델의 429/503 오류 → 최초+재시도 2회 → 대체 모델 전환과 실패 경로. 모의 검증임을 표시.
4. 큰 결과·긴 대화 테스트: 원본 보존, ToolMessage 제한, 요약 후 입력 예산 준수.
5. 여러 카드 중 일부만 결정 → Agent 재시작 → 나머지 카드 재개. 변경 이력이 중복되지 않음.
6. 실제 도구 연결 실패의 FAILED 이력 → 추적 링크 → 오류 span.
7. 동일 문항 전후표. 업무 결과 유지와 관측·통제 기능 추가를 구분해서 설명.

## 남은 실제 검증

예산 $0 때문에 실제 모델의 프롬프트 준수·정답률, 실제 제공자 장애 복구, 실제 토큰/과금 값,
실제 임베딩 검색 품질은 확인하지 않았다. 테스트 대역 결과를 실제 모델 성능으로 제시하지 않는다.
연결 가능한 브라우저가 없어 화면 시각 검수는 수행하지 못했다. 프론트 타입 검사·빌드·테스트는 통과했다.
LangSmith 기본 trace 보관 기간은 제한되므로 발표에 쓸 실행 기록·캡처는 보관 기간 안에 확보한다.

공식 근거: [LangGraph tracing](https://docs.langchain.com/langsmith/trace-with-langgraph),
[trace masking](https://docs.langchain.com/langsmith/mask-inputs-outputs),
[memory and summarization](https://docs.langchain.com/oss/python/langchain/short-term-memory).
