# AI Agent Capstone

> 📌 **먼저 읽을 것**
>
> - [docs/walking-skeleton.md](docs/walking-skeleton.md) — 이 뼈대를 무엇을 기반으로, 왜 이렇게 만들었는지
> - [docs/contracts.md](docs/contracts.md) — **파트 경계를 넘는 약속.** 내 파트 것만 보면 된다
> - [docs/decisions.md](docs/decisions.md) — 무엇이 정해졌고, 왜 그렇게 정했는지

```
ai-agent-capstone/
├─ docker-compose.yml          상시 서비스 7개 + 수동 rag-index (공개 포트: frontend 3000, backend 8080)
├─ .env.example                OPENAI_API_KEY, OPENAI_MODEL (복사해서 .env로)
├─ .gitignore
├─ README.md
├─ docs/
│  ├─ contracts.md             파트 간 계약 (바꾸려면 상대 파트와 합의)
│  ├─ decisions.md             결정 기록 (무엇을, 왜)
│  ├─ bom_agent_policy_v0.1.html  판단 기준 설계안 (규칙 ID·수식, 브라우저로 열기)
│  ├─ walking-skeleton.md      이 뼈대의 근거·의도·기준과 다른 점
│  ├─ BOM_Agent_아키텍처.md     아키텍처 문서 v0.2
│  └─ AI_Agent_Capstone_MCP_정리.md   교재 정리 노트
├─ frontend/                   Vue 3 + Vite + TypeScript (자세한 건 frontend/README.md)
│  ├─ src/                     화면, services/api.ts(서버 응답 변환), mocks/(목데이터)
│  ├─ nginx.conf               /api/** → backend 프록시 (SSE 버퍼링 끔)
│  └─ Dockerfile               compose는 API 모드로 빌드한다 (VITE_DATA_MODE, 기본 api)
├─ backend/                    Spring Boot 3 (Java 21, Maven)
│  ├─ pom.xml
│  ├─ Dockerfile
│  └─ src/main/java/capstone/
│     ├─ BackendApplication.java   시작점, 모듈 목록
│     ├─ Tasks.java                AI 작업 접수(202), 이벤트 저장·SSE 중계, 승인 결정 후 재개
│     ├─ TaskQueue.java            AI 작업 대기열(RabbitMQ) 설정 — 30초·2분 재시도, DLQ
│     ├─ InternalApi.java          /internal/** 도구 8개
│     ├─ ImpactAnalysis.java · OptionAnalysis.java   영향 분석·대안 비교 계산
│     ├─ Approvals.java            승인 카드·대기함·실행 전 확인·변경 이력
│     ├─ ScreenApi.java            화면 조회·변경 API
│     └─ Auth.java · Masking.java  로그인(JWT)·역할별 금액 가리기
├─ agent/                      FastAPI + LangGraph
│  ├─ main.py                  작업 API, PostgreSQL checkpointer, 이벤트 발행
│  ├─ graph.py                 의도 분류, 영향 분석, 문서 QA, 결품·과잉별 승인·실행
│  ├─ whatif.py                가정 변경 검증, 부작용 판정
│  └─ Dockerfile
├─ business-mcp/               FastMCP, :9101/mcp
│  ├─ server.py                조회·계산·변경 도구 13개 → backend /internal/**
│  └─ Dockerfile
└─ rag-mcp/                    FastMCP, :9102/mcp
   ├─ server.py                search_docs Tool + 실제 문서 Resource
   ├─ indexer.py               data/docs 6개를 조항별 pgvector 색인
   ├─ docstore.py · sql/       rag 스키마 저장·벡터 검색·원문 조회
   └─ Dockerfile
```

SKALA 2기 AI Agent Capstone 팀 저장소. 주제: BOM Agent ([아키텍처](docs/BOM_Agent_아키텍처.md))

> ⚠️ **여기 구조는 임시이며 토의로 바뀔 수 있음.**
> 로그인(JWT)·역할별 금액 가리기·승인 흐름과 실제 문서 검색을 연결했다. 작업·이벤트도 DB에 남아 재시작해도 이어진다. postgres에 시연 데이터(seed)가 들어가고, backend `/internal`이 그걸 읽어 영향 분석(결품·경고·과잉)과 해결 대안 비교를 실제로 계산한다. 환경별 최초 문서 색인과 프론트 승인 카드의 후속 작업은 아래 안내를 참고한다.

## 요청 흐름

```
브라우저 (localhost:3000)
  │ ① POST /api/ai/tasks → 202 {taskId}
  │ ⑦ GET  /api/ai/tasks/{id}/stream (SSE)
  ▼
frontend (nginx, /api/** 를 backend로 넘김)
  ▼
backend :8080 ── ② RabbitMQ → backend 소비자 → POST /agent/tasks ──▶ agent :8000
  ▲  ▲                                     │ ④ MCP Streamable HTTP /mcp
  │  │                                     ├─▶ business-mcp :9101  업무 도구 13개
  │  └── ⑤ /internal/** ──────────────────┘     (backend의 실제 계산·승인된 변경)
  │                                        └─▶ rag-mcp :9102  search_docs + doc:// Resource
  └── ⑦ POST /internal/tasks/{id}/events (TOOL_START, TOOL_END, DONE) → SSE로 중계
```

1. 화면이 질문을 보내면 backend가 작업을 만들고 바로 `202 {taskId}`를 돌려준다.
2. backend가 RabbitMQ에 작업을 넣고, backend 소비자가 agent에 HTTP로 실행을 요청한다. 승인 후 재개도 같은 대기열을 거치며, 전달 실패 시 30초·2분 뒤 재시도한다.
3. 영향 분석은 `analyze_impact`, 문서 근거는 `search_docs`로 찾고 해당 URI의 원문을 읽는다. 해결 요청은 모든 대상의 비교 → 검증 → 근거 확인을 먼저 마치고 개별 승인 카드를 함께 올린다. 승인된 건부터 최신 조건을 재검증해 실행하며, 미결정 건은 따로 기다린다.
4. 도구 실행 전후와 끝날 때 agent가 backend로 이벤트를 보내고, backend가 SSE로 화면에 넘긴다. 구독 전에 쌓인 이벤트는 구독할 때 다시 보낸다.

`OPENAI_API_KEY`가 없으면 agent의 분류·선택은 **가짜 모델**로 동작한다. 운영 RAG의 색인·검색에는 임베딩 키가 필요하며, 문서 근거를 못 읽으면 발주 변경 승인 요청을 보류한다. 외부 API 없는 테스트는 [tests/README.md](tests/README.md)를 따른다.

## 실행

```bash
cp .env.example .env   # 최초 설정 때만. 이미 있는 .env는 덮어쓰지 않는다.
```

```bash
env -u OPENAI_API_KEY docker compose --env-file .env up -d --build
env -u OPENAI_API_KEY docker compose --env-file .env --profile indexing run --rm --build rag-index
```

`.env`에 키를 입력한 뒤 실행한다. `env -u OPENAI_API_KEY`는 터미널에 남은 옛 키가 `.env`보다 우선하는 것을 막는다. 색인은 `rag` 스키마에만 테이블을 만들므로 업무 DB 초기화가 필요 없다. 자세한 재색인 방법은 [data/README.md](data/README.md).

http://localhost:3000 이 화면이다. 시연 계정 `planner`·`buyer`·`approver`(비밀번호 `1234`)로 로그인하면 실제 backend 데이터가 보인다. 구매 계정으로 "허브를 12/14에서 11/25로 당기면 부족한 MCU 해결 방법 만들어 줘"라고 물으면 승인 요청까지 가고, 팀장 계정의 승인 대기함에서 승인하면 발주가 실제로 나뉜다. 여러 부품 요청은 작업별로 묶어 각각 승인·거절하며, 상태가 자동 갱신된다([변경·검증 기록](docs/ai-rag-integration-changes.md)). 목데이터 화면만 보려면 `.env`에 `VITE_DATA_MODE=mock`.

AI는 `AGENT_DB_URL`로 접수·승인 연결·실행 기록을 영구 저장한 뒤 202를 반환한다. 기본 compose가 이 값을 제공한다. 10/21 이전 버전에서 승인 대기 중인 작업은 기존 버전에서 마무리한 뒤 AI·프론트를 업데이트한다. 업무 DB를 지울 필요는 없다.

backend 흐름은 직접 확인할 수 있다 (시연 계정 `planner`·`buyer`·`approver`, 비밀번호 `1234`):

```bash
TOKEN=$(curl -s -XPOST localhost:8080/api/auth/login -H 'Content-Type: application/json' -d '{"user_id":"planner","password":"1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
```

```bash
curl -s -XPOST localhost:8080/api/ai/tasks -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"question":"허브를 11/25로 당기면?"}'
```

받은 `taskId`로 `curl -N -H "Authorization: Bearer $TOKEN" localhost:8080/api/ai/tasks/{taskId}/stream`을 부르면 `TOOL_START → TOOL_END → TOOL_START → TOOL_END → DONE`이 차례로 온다.

`db/*.sql`을 고쳤으면 DB를 지우고 다시 띄워야 반영된다 (seed는 빈 DB에서만 들어간다).

```bash
docker compose down -v
```

골든셋 테스트 ([eval/golden.json](eval/golden.json)의 문항마다 계산해서 정답과 비교). Docker가 켜져 있어야 한다.

```bash
cd backend && mvn test
```

공개 포트는 frontend(3000)와 backend(8080)뿐이다. agent·MCP 서버는 compose 내부망에서 서비스 이름으로만 부른다.

## 폴더와 담당

| 폴더              | 지금 들어 있는 것                                                                                                                                                                                                           | 채울 사람                         | 채울 내용 (아키텍처 문서 기준)                                      |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- | ------------------------------------------------------------------- |
| `frontend/`     | Vue 3 + Vite 업무 화면 (compose는 실제 API 모드)                                                                                                                                                                            | 프론트 담당                       | (API 모드 화면 전부 연결됨) 과잉 해결안의 승인 요청은 agent 처리 후 |
| `backend/`      | 작업 접수·대기열·이벤트 저장·SSE 중계(`Tasks`·`TaskQueue`), 승인(`Approvals`), `/internal` 1차 8개(`InternalApi`), 영향 분석·대안 비교 계산(`ImpactAnalysis`·`OptionAnalysis`), 로그인·마스킹(`Auth`·`Masking`) + 테스트 | 백엔드 담당                       | (1~9단계, 대기열 완료)                             |
| `agent/`        | 의도 분류·영향 분석·문서 QA·품목별 결품/과잉 승인·실행, checkpointer                                                                                                                                                    | Agent 담당                        | 실제 모델 회귀 평가·프롬프트 개선                                  |
| `business-mcp/` | 조회·계산·변경 도구 13개, 서비스 토큰 분리                                                                                                                                                                                | MCP 담당                          | backend 계약 동기화                                                 |
| `rag-mcp/`      | 실제 원문 Resource·`search_docs`·pgvector·증분 indexer                                                                                                                                                                 | RAG 담당                          | 문서 갱신·재색인·검색 품질 평가                                   |
| `db/`           | `init.sql` 계정·스키마 / `schema.sql` 업무 테이블 / `seed.sql` 시연 데이터 ([decisions 7](docs/decisions.md))                                                                                                         | 백엔드 담당                       | 작업·승인·이력 테이블                                             |
| `data/`         | RAG 근거 문서 6개(`docs/*.md`)와 목록(`documents.json`) — 시스템과 같은 ID·규칙 ([data/README](data/README.md))                                                                                                        | RAG 담당(색인)·백엔드(내용 대조) | 색인, `search_docs`                                               |
| `eval/`         | `golden.json` — SC1~SC4 평가셋. **정답의 유일한 기준** ([eval/README](eval/README.md))                                                                                                                              | 백엔드(정답)·AI(질문)            | 문항 10~20개로 늘리기                                               |
| `docs/`         | 아키텍처, 결정 기록, 교재 정리                                                                                                                                                                                              | 모두                              | 결정이 바뀌면 `decisions.md`에 근거와 함께 남기기                 |

## 일부러 뺀 것

- `TOKEN` 스트리밍, 서비스별 healthcheck(postgres·rabbitmq만 있음)
- 작업 대기열(RabbitMQ)은 backend가 넣고 backend가 꺼내 agent에 보낸다. agent가 꺼져 있으면 30초·2분 뒤 다시 보낸다 ([decisions 6.24](docs/decisions.md)).
