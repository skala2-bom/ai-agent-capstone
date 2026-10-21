# AI·MCP·RAG 회귀 검사

실제 서비스·pgvector DB를 쓰되 운영 개발 환경과 별도인 `capstone-ai-test` Compose 프로젝트에서 실행한다.
호스트 포트를 열지 않는다. 테스트 코드의 DB 초기화·자동 승인은 **이름이 `capstone_ai_test`인 테스트 DB에서만** 가능하다.
기존 `ai-agent-capstone` 프로젝트의 볼륨/컨테이너를 지울 필요가 없다.
테스트 환경에도 RabbitMQ를 띄워 작업 접수와 승인 후 재개가 실제 대기열을 거치게 한다.
메시지는 테스트 전용 `testmq` 볼륨에 저장한다.

## 외부 모델 호출 없는 검사

```bash
docker compose -p capstone-ai-test -f tests/compose.yml up -d --build
docker compose -p capstone-ai-test -f tests/compose.yml run --rm tests python -m unittest -v test_ai_rag
docker compose -p capstone-ai-test -f tests/compose.yml run --rm tests python -m unittest -v test_approval_runner
docker compose -p capstone-ai-test -f tests/compose.yml run --rm tests
```

- `test_ai_rag.py`: 순수 판정·실행 보호·출처 검증, 실제 pgvector의 색인/갱신/검색/트랜잭션/권한.
- `integration_scenarios.py`: 매 시나리오 시드를 초기화하고 로그인 → 질문 → MCP → RAG → 개별 승인 → 실행 → DB 이력 → SSE를 검사한다.
- `draft`: 작업 전체 전송/철회, 전체 문제 설명·개인 메모 보존, 고정 품목 문제, 부분 요청 거부·재시도, 팀장 비노출 및 실행 여부를 검사한다.
- `reject`: 최초 구매 전송 후 팀장만 API를 조회하여 재제안을 직접 받는지 확인한다. 전체 문제·이전 구매 메모 계승과 팀장 승인 전 실행 없음도 검사한다.
- `manual-private`: 수동 입고 수량·예정일 변경 → 이력·자동 분석, 본인 작업만 목록·상세·SSE 조회, 팀장과 동일 역할의 다른 사용자 접근 차단을 확인한다. `reject`는 별도 승인 진행 API도 확인한다.
- SC1~4 분석·실행, 거절 후 재제안, SC4 지연 입력의 SYSTEM 자동 분석, 문서 QA를 포함한다.
- SC2는 승인 전 카드 3개, SC3은 2개가 먼저 만들어지는지 검사하고 등록 역순으로 승인한다. `mixed`는 1건만 승인해 먼저 실행 → 나머지 동시 승인/거절 → 거절된 품목만 재승인 → 중복 resume 전달을 검사한다.
- `test_approval_runner.py`: 별도의 `agent-test-db` 컨테이너에서 실제 agent PostgreSQL 체크포인트/접수함을 사용한다. 응답 저장 전후 중단, 카드 등록 응답 유실, 승인 중 데이터 변경, 역순 결정, thread 잠금, 중복 접수를 검사한다. MCP와 이벤트 HTTP만 대역을 쓴다.
- RAG 단위 검사는 색인을 바꾸므로 통합 검사·실제 모델 검사와 동시에 실행하지 않는다.
- `rag_fixture.py`의 결정적 벡터는 **연결·저장 계약용 테스트 대역**이다. 검색 품질이나 실제 모델 성공을 증명하지 않는다. 운영 이미지의 서버는 이 모듈을 사용하지 않는다.

통합 검사 일부만 실행하려면 `run --rm tests python -u integration_scenarios.py --only SC2 SC3`처럼 지정한다.

실제 컨테이너 재시작 복구 검사는 아래 순서로 실행한다. `recovery-finish`는 시드를 초기화하지 않는다.

```bash
docker compose -p capstone-ai-test -f tests/compose.yml run --rm tests python -u integration_scenarios.py --only recovery-prepare
docker compose -p capstone-ai-test -f tests/compose.yml restart agent
docker compose -p capstone-ai-test -f tests/compose.yml run --rm tests python -u integration_scenarios.py --only recovery-finish
```

프론트 검사는 `frontend/`에서 `npm ci`, `npm test`, `npm run build`로 실행한다. 카드별 동시 결정, 오래된 조회의 상태 덮어쓰기, 로그아웃 뒤 응답, 진행 이벤트 중복/역순 수신도 포함한다.

## 실제 모델 검사

`.env`에 키가 필요하다. 다음 명령은 데모 문서 6개와 시나리오 질문/데이터를 **OpenAI API**로 전송하며 비용이 발생한다.
외부 전송을 허용한 환경에서만 실행한다. 터미널의 옛 키가 우선되지 않도록 `env -u`를 붙였다.
같은 테스트 프로젝트의 RAG를 실제 임베딩으로 바꾸므로 위 가짜 벡터 테스트와 동시에 돌리지 않는다.

```bash
# 문서 색인
env -u OPENAI_API_KEY -u OPENAI_MODEL docker compose --env-file .env -p capstone-ai-test -f tests/compose.yml -f tests/compose.real.yml run --rm rag-index
# agent + RAG를 실제 모델로 전환
env -u OPENAI_API_KEY -u OPENAI_MODEL docker compose --env-file .env -p capstone-ai-test -f tests/compose.yml -f tests/compose.real.yml up -d agent rag-mcp
# 시드 초기화 + 읽기 전용 골든셋 (승인·실행 전 상태를 보장)
env -u OPENAI_API_KEY -u OPENAI_MODEL docker compose --env-file .env -p capstone-ai-test -f tests/compose.yml -f tests/compose.real.yml run --rm tests python -u integration_scenarios.py --only golden
# 실제 모델 whatif 추출 5문항 + verify 3문항
env -u OPENAI_API_KEY -u OPENAI_MODEL docker compose --env-file .env -p capstone-ai-test -f tests/compose.yml -f tests/compose.real.yml run --rm tests python -u /repo/agent/eval_golden.py /repo/eval/golden.json
# 각 시나리오마다 다시 시드 초기화 후 실제 모델로 끝까지
env -u OPENAI_API_KEY -u OPENAI_MODEL docker compose --env-file .env -p capstone-ai-test -f tests/compose.yml -f tests/compose.real.yml run --rm tests
```

실제 모델은 답변 문장이 달라도 된다. 추출한 가정·실행 도구·승인 건수·DB 변경·남은 문제는 고정 기대값으로 검사한다.
실제 모델 실행 뒤 단위 테스트를 다시 실행하면 fixture 벡터가 색인된다. 실제 모델을 계속 검증하려면 `rag-index`를 다시 실행해야 한다.

## 정리

테스트 기록을 지워도 될 때 **테스트 프로젝트만** 정리한다.

```bash
docker compose -p capstone-ai-test -f tests/compose.yml down -v
```

현재 검증 결과와 남은 프론트/백엔드 이슈는 [변경 기록](../docs/ai-rag-integration-changes.md)에 있다.
