# AI Agent Capstone

> 📌 **먼저 읽을 것**
>
> - [docs/walking-skeleton.md](docs/walking-skeleton.md) — 이 뼈대를 무엇을 기반으로, 왜 이렇게 만들었는지
> - [docs/contracts.md](docs/contracts.md) — **파트 경계를 넘는 약속.** 내 파트 것만 보면 된다
> - [docs/decisions.md](docs/decisions.md) — 무엇이 정해졌고, 왜 그렇게 정했는지
> - [docs/harness-validation.md](docs/harness-validation.md) — Harness 8요소 구현·비용 0 검증 결과·LangSmith 연결·시연 절차
> - [docs/coordination-requests.md](docs/coordination-requests.md) — 생산·구매 양방향 요청, 최종 거절 알림, 발주·생산계획 일괄 실행 및 검증
> - [docs/BOM_Agent_아키텍처.md](docs/BOM_Agent_아키텍처.md) — 전체 구조와 각 파트가 하는 일

```
ai-agent-capstone/
├─ docker-compose.yml          상시 서비스 7개 + 수동 rag-index (공개 포트: frontend 3000, backend 8080)
├─ .env.example                모델·LangSmith·예산·임베딩·JWT·서비스 토큰 설정 (복사해서 .env로)
├─ .gitignore
├─ README.md
├─ docs/
│  ├─ contracts.md             파트 간 계약 (바꾸려면 상대 파트와 합의)
│  ├─ decisions.md             결정 기록 (무엇을, 왜)
│  ├─ harness-validation.md    Harness 구현·비용 0 검증 결과·재현 명령
│  ├─ bom_agent_policy_v0.1.html  판단 기준 설계안 (규칙 ID·수식, 브라우저로 열기)
│  ├─ walking-skeleton.md      이 뼈대의 근거·의도·기준과 다른 점
│  ├─ BOM_Agent_아키텍처.md     아키텍처 문서 (v0.4)
│  ├─ AI_Agent_Capstone_MCP_정리.md   교재 정리 노트
│  ├─ ai-rag-integration-changes.md   (기록) AI·RAG 연결 변경·검증
│  ├─ task-review-changes.md   (기록) 작업 단위 구매 검토 변경
│  └─ agent-draft-changes.md   (기록) agent-draft 브랜치 변경
├─ db/                         init.sql 계정·스키마 / schema.sql 업무 테이블 / seed.sql 시연 데이터
├─ data/                       RAG 근거 문서 6개(docs/*.md)와 목록(documents.json)
├─ eval/
│  ├─ golden.json              SC1~SC4 평가셋 (정답의 유일한 기준)
│  └─ reports/                 Harness 검증 결과 (전후 평가·추적·재시작 기록)
├─ tests/                      AI·MCP·RAG 회귀 검사 (별도 compose 프로젝트, 자세한 건 tests/README.md)
├─ frontend/                   Vue 3 + Vite + TypeScript (자세한 건 frontend/README.md)
│  ├─ src/                     화면, services/api.ts(서버 응답 변환), mocks/(목데이터)
│  ├─ nginx.conf               /api/** → backend 프록시 (SSE 버퍼링 끔)
│  └─ Dockerfile               기본은 mock. compose가 VITE_DATA_MODE=api로 빌드한다
├─ backend/                    Spring Boot 3 (Java 21, Maven)
│  ├─ pom.xml
│  ├─ Dockerfile
│  └─ src/main/java/capstone/
│     ├─ BackendApplication.java   시작점, 모듈 목록
│     ├─ Tasks.java                AI 작업 접수(202), 이벤트 저장·SSE 중계, 구매 검토 전송·철회, 승인 결정 후 재개
│     ├─ TaskQueue.java            AI 작업 대기열(RabbitMQ) 설정 — 30초·2분 재시도, DLQ
│     ├─ InternalApi.java          /internal/** 업무 API 13개 (business-mcp 도구 13개와 1:1)
│     ├─ ServiceTokens.java        /internal 서비스 토큰 검사 — agent·business-mcp 토큰을 나눠 각자 경로만 연다
│     ├─ ScreenApi.java            화면 조회·변경 API (/internal과 같은 계산을 부른다)
│     ├─ ApiError.java             업무 오류 형식 {error, message, retry_hint}
│     ├─ ImpactAnalysis.java · OptionAnalysis.java   영향 분석·대안 비교 계산
│     ├─ Approvals.java            승인 카드·대기함·작업 단위 검토·실행 전 확인·변경 이력
│     └─ Auth.java · Masking.java  로그인(JWT)·역할별 금액 가리기
├─ agent/                      FastAPI + LangGraph
│  ├─ main.py                  작업 접수 API — 명령을 DB에 저장한 뒤 202
│  ├─ graph.py                 의도 분류 → 영향 분석 / 문서 QA / 품목별 해결안·승인·실행
│  ├─ whatif.py                가정 변경 검증, 부작용 판정 (순수 함수)
│  ├─ approval_runner.py       DB 접수함의 명령을 작업마다 하나씩 실행·재개 (재시작해도 이어감)
│  ├─ approval_store.py        접수함·승인 연결·실행 기록·이벤트 보낼 함 저장
│  ├─ adjust_tools.py          해결안 경로의 읽기 전용 도구 실행 규칙
│  ├─ model_runtime.py         모델 재시도(429/503 최대 2회)·대체 모델·가짜 모델 모드
│  ├─ context_budget.py        모델 입력 예산·도구 결과 길이 제한
│  ├─ observability.py         실행별 추적(LangSmith)·로컬 기록
│  ├─ check_tracing.py         LangSmith 연결 확인용 추적 1건 전송
│  ├─ eval_golden.py           golden.json으로 분류·verify 평가
│  ├─ eval_harness.py          모델 호출 없는 Harness 평가 (변형 질문 + verify)
│  ├─ prompts/                 분류·영향·QA·해결안 지시문 (Markdown)
│  ├─ sql/                     agent 스키마 (접수함·승인 연결 테이블)
│  ├─ tests/                   adjust_tools 단위 테스트
│  └─ Dockerfile
├─ business-mcp/               FastMCP, :9101/mcp
│  ├─ server.py                조회·계산·변경 도구 13개 → backend /internal/**
│  └─ Dockerfile
└─ rag-mcp/                    FastMCP, :9102/mcp
   ├─ server.py                search_docs Tool + doc:// 원문 Resource
   ├─ documents.py             문서 목록 검증·조항 단위 자르기
   ├─ embeddings.py            문서·질의 공용 임베딩 호출
   ├─ docstore.py · sql/       rag 스키마 저장·벡터 검색·원문 조회
   ├─ indexer.py               data/docs 6개를 조항별 pgvector 색인 (직접 실행)
   └─ Dockerfile
```

SKALA 2기 AI Agent Capstone 팀 저장소. 주제: BOM Agent ([아키텍처](docs/BOM_Agent_아키텍처.md))

> ⚠️ **여기 구조는 임시이며 토의로 바뀔 수 있음.**
> 로그인(JWT)·역할별 금액 가리기·승인 흐름과 실제 문서 검색을 연결했다. 작업·이벤트도 DB에 남아 재시작해도 이어진다. postgres에 시연 데이터(seed)가 들어가고, backend `/internal`이 그걸 읽어 영향 분석(결품·경고·과잉)과 해결 대안 비교를 실제로 계산한다. 환경별 최초 문서 색인은 아래 실행 안내를 따른다.

## 요청 흐름

```
브라우저 (localhost:3000)
  │ ① POST /api/ai/tasks → 202 {taskId}
  │ ⑥ GET  /api/ai/tasks/{id}/stream (SSE)
  ▼
frontend (nginx, /api/** 를 backend로 넘김)
  ▼
backend :8080 ── ② RabbitMQ ai.tasks → backend 소비자 → POST /agent/tasks ──▶ agent :8000
  ▲  ▲                                        ③ classify_intent → 영향 분석 / 문서 QA / 해결안
  │  │                                        │ ④ MCP Streamable HTTP /mcp
  │  │                                        ├─▶ business-mcp :9101  업무 도구 13개
  │  └── ⑤ /internal/** ─────────────────────┘     (backend의 실제 계산·승인된 변경)
  │                                           └─▶ rag-mcp :9102  search_docs + doc:// Resource
  └── ⑥ POST /internal/tasks/{id}/events → 저장 후 SSE로 중계
```

1. 화면이 질문을 보내면 backend가 작업을 만들고 바로 `202 {taskId}`를 돌려준다.
2. backend가 RabbitMQ `ai.tasks`에 작업을 넣고, backend 소비자가 꺼내 agent에 HTTP로 실행을 요청한다. 승인 후 재개도 같은 대기열을 거친다. agent가 꺼져 있으면 30초·2분 뒤 다시 보내고, 그래도 안 되면 DLQ에 남기고 FAILED ([decisions 6.24](docs/decisions.md)). agent는 받은 명령을 자기 DB에 먼저 저장하고 202를 돌려주므로, agent가 재시작해도 이어서 실행한다.
3. agent의 `classify_intent`가 질문을 IMPACT(영향 분석)·QA(문서 질문)·ADJUST(해결 방법)·CLARIFY(되묻기)로 나눈다.
   - IMPACT: `analyze_impact`로 결품·경고·과잉을 계산하고, `search_docs`로 관련 조항을 찾아 원문(`doc://`)을 읽는다.
   - QA: `search_docs` → 검색된 URI의 원문을 읽고 인용한다.
   - ADJUST: 대상 품목마다 `compare_options` → 대안 선택 → 검증 → 근거 확인을 마친 뒤 승인 카드를 한꺼번에 올린다. 근거를 못 읽은 품목은 카드를 만들지 않는다.
4. 업무 도구는 business-mcp가 backend `/internal/**`을 불러 실행한다. 변경 도구는 승인 기록과 도구·인자가 정확히 같아야만 backend가 실행한다.
5. 승인된 카드부터 최신 조건을 다시 확인해 실행하고, 결정을 기다리는 카드는 따로 기다린다.
6. agent가 진행 이벤트를 backend로 보내고, backend가 저장한 뒤 SSE로 화면에 넘긴다. 구독 전에 쌓인 이벤트는 구독할 때 처음부터 다시 보낸다.

화면에 오는 이벤트 종류:

| 이벤트 | 언제 |
|---|---|
| `TOOL_START` · `TOOL_END` | 도구 실행 전후 |
| `APPROVAL_REQUIRED` | 승인 카드 등록 (backend가 approvalId를 붙인다) |
| `TASK_REVIEW_READY` | 작업의 모든 카드 등록이 끝나 구매 담당자가 검토할 수 있다 |
| `PROPOSAL_PROGRESS` | 카드별 진행 상태 (승인·거절·재제안·실행) |
| `WORKFLOW_NOTICE` | 중단·연결 실패처럼 사람이 확인해야 하는 상황 |
| `TRACE` | 실행 통계·추적 링크. 작업 상태는 바꾸지 않는다 |
| `DONE` · `FAILED` | 끝. 최종 답변은 `DONE`의 `answer`에 온다 |

`OPENAI_API_KEY`가 없거나 `AGENT_MODEL_MODE=fake`이면 agent의 분류·선택은 **가짜 모델**(규칙 처리)로 동작한다. 운영 RAG의 색인·검색에는 임베딩 키가 필요하다. `AGENT_MODEL_MODE=fake`는 agent만 막고 rag-mcp의 임베딩 호출은 막지 않는다. 외부 API 없는 테스트는 [tests/README.md](tests/README.md)를 따른다.

## 승인 흐름

역할마다 할 수 있는 일이 다르다. 금액을 볼 수 있는 사람과 발주를 바꿀 수 있는 사람을 나누기 위해서다.

| 역할 (시연 계정) | 할 수 있는 일 |
|---|---|
| 생산관리 `PLANNER` (`planner`) | 영향 분석·문서 질문만. 금액은 가려진다. 해결을 요청하면 영향 분석으로 답하고 "구매팀에 해결 요청"을 안내한다 |
| 구매 `BUYER` (`buyer`) | AI에 해결 방법 요청 → 카드 검토 후 팀장에게 전송 또는 보내지 않음. 단가는 가려진다 |
| 팀장 `APPROVER` (`approver`) | 승인 대기함에서 품목별 승인·거절. 금액 전부 보인다 |

1. 구매 담당자가 "해결 방법 만들어 줘"라고 묻는다. AI가 품목마다 해결안 카드를 준비한다 (아직 초안, 팀장에게 안 보인다).
2. 카드가 다 준비되면 구매 담당자가 작업마다 **전체 문제 설명**과 품목별 **메모**를 적고, 작업 전체를 **팀장에게 승인 요청**하거나 **보내지 않음**으로 처리한다 (`POST /api/ai/tasks/{id}/review/submit` · `/review/withdraw`). 일부 카드만 따로 보낼 수는 없다.
3. 팀장이 승인 대기함에서 품목별로 승인·거절한다 (`POST /api/ai/tasks/{id}/approval`). 거절은 사유가 필요하다.
4. 승인된 카드는 AI가 최신 조건을 다시 확인하고 실행한다. 거절된 품목은 AI가 다시 비교해 재제안한다. **재제안은 구매 재검토 없이 바로 팀장 승인 대기로 간다** (이전 문제 설명·메모를 이어받는다, [decisions 6.40](docs/decisions.md)).

팀장이 직접 해결 방법을 요청하면 초안 검토 없이 바로 승인 대기로 들어간다.

시연 계정은 `planner`·`buyer`·`approver`, 비밀번호는 모두 `1234`.

## 실행

```bash
cp .env.example .env   # 최초 설정 때만. 이미 있는 .env는 덮어쓰지 않는다.
```

`.env`의 주요 값 (비워 두면 compose의 기본값을 쓴다):

| 키 | 뜻 |
|---|---|
| `OPENAI_API_KEY` | 모델·임베딩 호출 키. 없으면 agent는 가짜 모델, RAG 색인·검색은 실패 |
| `OPENAI_MODEL` | 주 모델 (기본 `gpt-4o-mini`) |
| `AGENT_MODEL_MODE` | `auto`(키가 있으면 실제 모델) / `fake`(키가 있어도 모델을 안 부른다, 비용 0 검증용) |
| `FALLBACK_MODEL` | 주 모델 재시도가 다 실패하면 쓸 대체 모델. 비우면 대체 없이 오류 |
| `LANGSMITH_TRACING` · `LANGSMITH_API_KEY` · `LANGSMITH_PROJECT` · `LANGSMITH_ENDPOINT` · `LANGSMITH_WORKSPACE_ID` | LangSmith 추적. 기본은 꺼짐 |
| `AGENT_INPUT_TOKEN_BUDGET` · `AGENT_OUTPUT_MAX_TOKENS` | 모델 입력 예산(12,000)·출력 최대(1,500) 토큰 |
| `TOOL_RESULT_MAX_CHARS` | 모델에 넘기는 도구 결과 최대 글자 수(4,000). 원본 상태는 자르지 않는다 |
| `MAX_ADJUST_TURNS` · `AGENT_WORK_TIMEOUT_S` · `AGENT_RECURSION_LIMIT` | 해결안 경로 최대 턴(64)·실행 구간 제한 시간(600초)·그래프 최대 단계(600) |
| `RAG_EMBEDDING_MODEL` · `RAG_EMBEDDING_DIMENSIONS` | 색인·검색 공용 임베딩 모델·차원. 바꾸면 다시 색인 |
| `JWT_SECRET` | 로그인 토큰 서명 키 (32바이트 이상) |
| `AGENT_SERVICE_TOKEN` · `BUSINESS_MCP_SERVICE_TOKEN` | `/internal` 서비스 토큰. 기본값은 시연용이라 밖에 띄우면 바꾼다 |
| `VITE_DATA_MODE` | 화면 데이터 모드. compose 기본 `api`, 목데이터만 보려면 `mock` |

```bash
env -u OPENAI_API_KEY docker compose --env-file .env up -d --build --wait
```

**처음 한 번은 문서 색인이 꼭 필요하다.** 색인이 없으면 문서 검색이 `RAG_NOT_INDEXED`로 실패하고, 근거를 못 읽은 해결안은 승인 카드가 만들어지지 않는다. 문서를 임베딩 API로 보내므로 비용이 든다.

```bash
env -u OPENAI_API_KEY docker compose --env-file .env --profile indexing run --rm --build rag-index
```

`--wait`는 모든 서비스가 healthy가 될 때까지 기다린다(1분 안팎, `docker compose ps`의 STATUS로도 보인다). `env -u OPENAI_API_KEY`는 터미널에 남은 옛 키가 `.env`보다 우선하는 것을 막는다. 색인은 `rag` 스키마에만 테이블을 만들므로 업무 DB 초기화가 필요 없다. 자세한 재색인 방법은 [data/README.md](data/README.md).

http://localhost:3000 이 화면이다. 시연 계정 `planner`·`buyer`·`approver`(비밀번호 `1234`)로 로그인하면 실제 backend 데이터가 보인다. 구매 계정으로 "허브를 12/14에서 11/25로 당기면 부족한 MCU 해결 방법 만들어 줘"라고 묻고 검토 후 팀장에게 보내면, 팀장 계정의 승인 대기함에서 승인했을 때 발주가 실제로 나뉜다 ([승인 흐름](#승인-흐름)). 상태는 자동 갱신된다 ([변경·검증 기록](docs/ai-rag-integration-changes.md)).

AI 작업 대기열(RabbitMQ)은 backend가 넣고 backend가 꺼내 agent에 보낸다. agent는 `AGENT_DB_URL`로 접수·승인 연결·실행 기록을 영구 저장한 뒤 202를 반환한다. 기본 compose가 이 값을 제공한다. 10/21 이전 버전에서 승인 대기 중인 작업은 기존 버전에서 마무리한 뒤 AI·프론트를 업데이트한다. 업무 DB를 지울 필요는 없다.

backend 흐름은 직접 확인할 수 있다:

```bash
TOKEN=$(curl -s -XPOST localhost:8080/api/auth/login -H 'Content-Type: application/json' -d '{"user_id":"planner","password":"1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
```

```bash
curl -s -XPOST localhost:8080/api/ai/tasks -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"question":"허브를 11/25로 당기면?"}'
```

받은 `taskId`로 `curl -N -H "Authorization: Bearer $TOKEN" localhost:8080/api/ai/tasks/{taskId}/stream`을 부르면 도구마다 `TOOL_START`·`TOOL_END`(생산 계획 조회 → 영향 분석 → 문서 검색 → 원문 읽기, 원문은 근거 수만큼 여러 번)가 오고, 그 사이에 실행 기록 `TRACE`(시작·끝 두 번)가 끼며, 마지막에 `DONE`이 온다. 예 (10/21 실측): `TOOL_START(list_plans) → TRACE → TOOL_END(list_plans) → … → TOOL_END(read_doc) → TRACE → DONE`.

`db/*.sql`을 고쳤으면 DB를 지우고 다시 띄워야 반영된다 (seed는 빈 DB에서만 들어간다).

```bash
docker compose down -v
```

> ⚠️ `down -v`는 RAG 색인도 같이 지운다 (같은 postgres 볼륨). 다시 띄운 뒤 위 `rag-index`를 다시 실행한다.

### 테스트

| 무엇 | 명령 | 비고 |
|---|---|---|
| backend (골든셋 포함) | `cd backend && mvn test` | [eval/golden.json](eval/golden.json) 문항마다 계산해서 정답과 비교. Docker가 켜져 있어야 한다 |
| frontend | `cd frontend && npm test` | `npm ci` 먼저. 빌드 확인은 `npm run build` |
| AI·MCP·RAG 회귀 | [tests/README.md](tests/README.md) | 별도 compose 프로젝트, 외부 모델 호출 없음 |
| Harness 점검 | [docs/harness-validation.md](docs/harness-validation.md) | 비용 0 재현 명령·전후 평가 |

공개 포트는 frontend(3000)와 backend(8080)뿐이다. agent·MCP 서버는 compose 내부망에서 서비스 이름으로만 부른다.

## 폴더와 담당

| 폴더 | 지금 들어 있는 것 | 채울 사람 | 남은 일 |
|---|---|---|---|
| `frontend/` | Vue 3 + Vite 업무 화면 6개 — 업무 현황·생산계획·자재 BOM·발주 입고·승인 대기함·작업 이력 (compose는 실제 API 모드) | 프론트 담당 | |
| `backend/` | 작업 접수·대기열·이벤트 저장·SSE 중계(`Tasks`·`TaskQueue`), 승인·작업 단위 검토(`Approvals`), `/internal` 13개(`InternalApi`), 화면 API(`ScreenApi`), 서비스 토큰(`ServiceTokens`), 영향 분석·대안 비교 계산(`ImpactAnalysis`·`OptionAnalysis`), 로그인·마스킹(`Auth`·`Masking`) + 테스트 | 백엔드 담당 | |
| `agent/` | 의도 분류·영향 분석·문서 QA·품목별 해결안 승인·실행, 영구 접수함·재개(`approval_runner`), 재시도·대체 모델·가짜 모드(`model_runtime`), 입력 예산(`context_budget`), LangSmith 추적(`observability`), 평가(`eval_golden`·`eval_harness`) | Agent 담당 | 실제 모델 회귀 평가·프롬프트 개선 |
| `business-mcp/` | 조회·계산·변경 도구 13개, 서비스 토큰 분리 | MCP 담당 | backend 계약 동기화 |
| `rag-mcp/` | 실제 원문 Resource·`search_docs`·pgvector·증분 indexer | RAG 담당 | 문서 갱신·재색인·검색 품질 평가 |
| `db/` | `init.sql` 계정·스키마 / `schema.sql` 업무·작업·승인·이력 테이블 / `seed.sql` 시연 데이터 ([decisions 7](docs/decisions.md)) | 백엔드 담당 | |
| `data/` | RAG 근거 문서 6개(`docs/*.md`)와 목록(`documents.json`) — 시스템과 같은 ID·규칙 ([data/README](data/README.md)) | RAG 담당(색인)·백엔드(내용 대조) | |
| `eval/` | `golden.json` — SC1~SC4 평가셋 8문항(질문 5·verify 3). **정답의 유일한 기준**. `reports/` Harness 검증 결과 ([eval/README](eval/README.md)) | 백엔드(정답)·AI(질문) | 문항 10~20개로 늘리기 |
| `tests/` | AI·MCP·RAG 통합·회귀 검사, 격리 compose 설정 ([tests/README](tests/README.md)) | AI 담당 | |
| `docs/` | 아키텍처, 계약, 결정 기록, Harness 검증, 변경 기록, 교재 정리 | 모두 | 결정이 바뀌면 `decisions.md`에 근거와 함께 남기기 |

## 일부러 뺀 것

- `TOKEN` 스트리밍. 답변을 글자 단위로 흘리지 않고, 최종 답변은 `DONE` 이벤트의 `answer`에 한 번에 담는다.
