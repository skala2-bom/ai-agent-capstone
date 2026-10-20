# AI Agent Capstone

> 📌 **먼저 읽을 것**
> - [docs/walking-skeleton.md](docs/walking-skeleton.md) — 이 뼈대를 무엇을 기반으로, 왜 이렇게 만들었는지
> - [docs/contracts.md](docs/contracts.md) — **파트 경계를 넘는 약속.** 내 파트 것만 보면 된다
> - [docs/decisions.md](docs/decisions.md) — 무엇이 정해졌고, 왜 그렇게 정했는지

```
ai-agent-capstone/
├─ docker-compose.yml          서비스 5개 정의 (공개 포트: frontend 3000, backend 8080)
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
├─ frontend/                   Vue 3 (CDN) + nginx
│  ├─ index.html               질문 입력, SSE 이벤트 목록 표시
│  ├─ nginx.conf               /api/** → backend 프록시 (SSE 버퍼링 끔)
│  └─ Dockerfile
├─ backend/                    Spring Boot 3 (Java 21, Maven)
│  ├─ pom.xml
│  ├─ Dockerfile
│  └─ src/main/java/capstone/
│     └─ BackendApplication.java   작업 API(202), SSE 중계, /internal/** (고정 JSON)
├─ agent/                      FastAPI + LangGraph
│  ├─ main.py                  StateGraph(agent ↔ tools), MCP 연결, 가짜 모델, 이벤트 발행
│  └─ Dockerfile
├─ business-mcp/               FastMCP, :9101/mcp
│  ├─ server.py                analyze_impact 도구 → backend /internal/analysis/impact
│  └─ Dockerfile
└─ rag-mcp/                    FastMCP, :9102/mcp
   ├─ server.py                doc://{doc_id}/{clause_no} Resource (원문 하드코딩)
   └─ Dockerfile
```

SKALA 2기 AI Agent Capstone 팀 저장소. 주제: BOM Agent ([아키텍처](docs/BOM_Agent_아키텍처.md))

> ⚠️ **여기 구조는 임시이며 토의로 바뀔 수 있음.**
> 지금 코드는 서비스 5개가 끝까지 연결되는지만 확인하는 walking skeleton이다. 승인 흐름은 아직 없다. 로그인(JWT)과 역할별 금액 가리기는 있다. postgres에 시연 데이터(seed)가 들어가고, backend `/internal`이 그걸 읽어 영향 분석(결품·경고·과잉)과 해결 대안 비교를 실제로 계산한다.

## 요청 흐름

```
브라우저 (localhost:3000)
  │ ① POST /api/ai/tasks → 202 {taskId}
  │ ⑦ GET  /api/ai/tasks/{id}/stream (SSE)
  ▼
frontend (nginx, /api/** 를 backend로 넘김)
  ▼
backend :8080 ── ② POST /agent/tasks ──▶ agent :8000 (FastAPI + LangGraph)
  ▲  ▲                                     │ ④ MCP Streamable HTTP /mcp
  │  │                                     ├─▶ business-mcp :9101  tool analyze_impact
  │  └── ⑤ POST /internal/analysis/impact ─┘     (고정 JSON 응답)
  │                                        └─▶ rag-mcp :9102  resource doc://{doc_id}/{clause_no}
  └── ⑦ POST /internal/tasks/{id}/events (TOOL_START, TOOL_END, DONE) → SSE로 중계
```

1. 화면이 질문을 보내면 backend가 작업을 만들고 바로 `202 {taskId}`를 돌려준다.
2. backend가 agent에 작업 실행을 요청한다.
3. agent 그래프(`agent ↔ tools` 반복)가 `analyze_impact` 도구를 부르고, 결과의 `evidence_uri`로 rag-mcp Resource를 한 번 읽는다.
4. 도구 실행 전후와 끝날 때 agent가 backend로 이벤트를 보내고, backend가 SSE로 화면에 넘긴다. 구독 전에 쌓인 이벤트는 구독할 때 다시 보낸다.

`OPENAI_API_KEY`가 없으면 agent는 **가짜 모델**로 동작한다: `analyze_impact` → `read_doc` → 답변 순서로 고정.

## 실행

```bash
cp .env.example .env   # OPENAI_API_KEY는 비워 두면 가짜 모델
```

```bash
docker compose up --build
```

http://localhost:3000 에서 계정 버튼(`planner` 등)을 눌러 로그인한 뒤 "질문"을 누르면 `TOOL_START → TOOL_END → TOOL_START → TOOL_END → DONE`이 차례로 보인다.

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

| 폴더 | 지금 들어 있는 것 | 채울 사람 | 채울 내용 (아키텍처 문서 기준) |
|---|---|---|---|
| `frontend/` | CDN Vue 한 장 + nginx | 프론트 담당 | Vite 전환, 진행 상태·결과·승인 카드·근거 패널, 로그인 |
| `backend/` | 작업 접수·SSE 중계(`BackendApplication`), `/internal` 1차 8개(`InternalApi`), 영향 분석·대안 비교 계산(`ImpactAnalysis`·`OptionAnalysis`), 로그인·마스킹(`Auth`·`Masking`) + 테스트 | 백엔드 담당 | 승인 API·변경 저장, 작업 DB(ai_task·task_event), 2차 도구 5개, 자동 분석, `/internal/**` 서비스 토큰 |
| `agent/` | ReAct 그래프 + 가짜 모델 | Agent 담당 | 5장 그래프 노드(classify_intent 등), Harness 8요소, checkpointer, 승인 재개 |
| `business-mcp/` | `analyze_impact` 도구 하나 | MCP 담당 | 8.1 조회·계산·변경 도구, 오류 코드별 ToolError |
| `rag-mcp/` | 조항 Resource 하나 (원문 하드코딩) | RAG 담당 | `search_docs` 도구, pgvector 색인, indexer |
| `db/` | `init.sql` 계정·스키마 / `schema.sql` 업무 테이블 / `seed.sql` 시연 데이터 ([decisions 7](docs/decisions.md)) | 백엔드 담당 | 작업·승인·이력 테이블 |
| `eval/` | `golden.json` — SC1~SC4 평가셋. **정답의 유일한 기준** ([eval/README](eval/README.md)) | 백엔드(정답)·AI(질문) | 문항 10~20개로 늘리기 |
| `docs/` | 아키텍처, 결정 기록, 교재 정리 | 모두 | 결정이 바뀌면 `decisions.md`에 근거와 함께 남기기 |

## 일부러 뺀 것

- 작업·승인 테이블, 승인(`APPROVAL_REQUIRED`), `TOKEN` 스트리밍, healthcheck(postgres만 있음)
- `/internal/**`가 지금은 backend 공개 포트로도 열려 있다. 서비스 토큰 검사는 백엔드 담당이 추가한다.
- 작업과 이벤트는 backend 메모리에만 있어서 재시작하면 사라진다.
