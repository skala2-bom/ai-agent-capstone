# Walking Skeleton: 무엇을 기반으로, 왜 이렇게 만들었나

> 📜 **역사 문서** — 2026-10-20 첫 뼈대(`01c0ee7`) 시점의 설계 근거다. 여기서 "아직 없음", "일부러 뺀 것"으로 적힌 것 대부분(상태 저장·재개, RAG 검색, 서비스 토큰, 요청자 헤더, 대기열, healthcheck 등)은 그 뒤 구현됐다. **지금 상태는 [README](../README.md)·[contracts.md](contracts.md)·[decisions.md](decisions.md)를 본다.**

> **작성일**: 2026-10-20 · **대상 커밋**: `01c0ee7` 이후 `main`
>
> ⚠️ 여기 구조는 임시이며 토의로 바뀔 수 있음.

---

## 1. 무엇을 기반으로 만들었나

| 출처 | 가져온 것 |
|---|---|
| **요청 사항** | 구성 6개, 목표 흐름, 서비스당 파일 1~4개, 인증·DB·계산 제외, 가짜 모델, 공개 포트 2개, Streamable HTTP |
| **교재 정리 노트** ([AI_Agent_Capstone_MCP_정리.md](AI_Agent_Capstone_MCP_정리.md)) | MCP 구현 방법(FastMCP 4.1 문법), 5장 레퍼런스 아키텍처, 금지 사항, 평가표 |
| **아키텍처 문서 v0.2** ([BOM_Agent_아키텍처.md](BOM_Agent_아키텍처.md)) | 서비스 이름·포트, API 경로, 이벤트 이름과 payload, 시나리오 SC1의 데이터 |
| **노션 진행 가이드** | "얕게 끝까지 먼저 관통 → 그다음 두껍게" 원칙, 파트 분담 |

## 2. 왜 이 뼈대를 만들었나 (의도)

노션 진행 가이드 2부의 핵심은 **"각개전투하면 나중에 안 붙는다"**이다. 그래서 목표를 기능이 아니라 **서비스 경계를 한 번씩 다 건너 보는 것**으로 잡았다.

- 서비스 5개가 compose 안에서 서로 이름으로 찾아지는가
- 언어가 다른 서비스끼리(Java ↔ Python) 실제로 통신되는가
- MCP Streamable HTTP 연결, 도구 호출, Resource 읽기가 되는가
- 비동기 이벤트가 agent → backend → 브라우저까지 실시간으로 흐르는가

이게 되면 각자 자기 폴더의 가짜 부분만 진짜로 바꾸면 된다. 연결 문제가 마지막 주에 터지는 일을 막는 것이 목적이다.

## 3. 교재·평가표 항목별로 무엇을 반영했나

### 3.1 5장 레퍼런스 아키텍처: 구성도 번호 ①~⑦

| 번호 | 교재 요구 | 뼈대에서 | 파일 |
|:-:|---|---|---|
| ① | 작업으로 접수, **202 즉시 반환** | `POST /api/ai/tasks` → `202 {taskId}` | `backend/.../BackendApplication.java` |
| ② | backend → agent 내부 HTTP | 202를 먼저 돌려준 뒤 비동기로 `POST /agent/tasks` | 같은 파일 |
| ③ | agent → LLM | 키가 있으면 ChatOpenAI, 없으면 가짜 모델 | `agent/main.py` |
| ④ | MCP `tools/call`, Streamable HTTP | `http://business-mcp:9101/mcp`, `http://rag-mcp:9102/mcp` | 같은 파일 |
| ⑤ | business-mcp → `/internal/**` | `POST /internal/analysis/impact` | `business-mcp/server.py` |
| ⑥ | rag-mcp → 근거 반환 | Resource로 조항 원문 반환 (검색은 아직) | `rag-mcp/server.py` |
| ⑦ | 진행 이벤트를 **SSE로 중계** | agent → `/internal/tasks/{id}/events` → SSE | backend, agent |

### 3.2 5장 구성 요소별 금지 사항 (평가에서 위반 여부를 봄)

| 금지 | 지켰나 | 어떻게 |
|---|:-:|---|
| Vue가 AI 서버 직접 호출 | ✅ | 화면은 `/api/**`만 부름. agent는 포트가 공개되지 않음 |
| Spring Boot가 LLM 호출 | ✅ | backend에는 LLM 라이브러리 자체가 없음 |
| Agent가 DB 직접 쓰기 | ✅ | DB가 없고, 결과는 이벤트로 backend에 넘김 |
| business-mcp가 업무 규칙 복제 | ✅ | 계산 없이 backend로 전달만 함 (아키텍처 1.1 "계산은 백엔드에") |
| rag-mcp가 업무 데이터 변경 | ✅ | 읽기 전용 Resource만 있음 |

### 3.3 5장 배포: 공개 포트

- compose에서 `ports`를 준 서비스는 frontend(3000)와 backend(8080)뿐이다. 나머지는 내부망에서 서비스 이름으로만 부른다.
- 비밀값은 `.env`에만 두고 저장소에는 `.env.example`만 올렸다 (아키텍처 7장, 노션 ⑦ "API 키 커밋 금지").

### 3.4 1~4장 MCP (평가표 20점, 최대 배점)

| 교재 내용 | 반영 |
|---|---|
| `@mcp.tool`, docstring이 곧 도구 설명 | `analyze_impact` docstring에 인자 예시와 **단위**(개, YYYY-MM-DD)를 적음 |
| 실패는 `ToolError` + 재시도 정보 | backend 연결 실패 시 `[UPSTREAM_TIMEOUT] … 잠시 후 다시 시도하세요` (아키텍처 8.1 오류 코드 형식) |
| Resource 템플릿 | `doc://{doc_id}/{clause_no}` (아키텍처 v0.2 변경 1번, 교재 "원문 Resource 1개 이상" 요건) |
| 없는 자료면 있는 목록을 알려 줌 | 없는 조항을 요청하면 오류 메시지에 있는 조항 목록을 넣음 (2장 `find_product` 패턴) |
| 모델은 Tool만 부를 수 있음 → Resource는 **가상 도구**로 연결 | agent의 `read_doc(uri)`가 `client.read_resource(uri)`를 부름 (3장 "LLM에 연결하기") |
| compose에서는 Streamable HTTP, `/mcp`를 빼면 404 | `mcp.run(transport="http", path="/mcp")` |

### 3.5 AI Agent 구현 (평가표 15점)

| 평가 문구 | 반영 |
|---|---|
| Graph가 코드에 반영 | `StateGraph`로 `agent ↔ tools` 반복을 직접 구성 (노션 E 기술 선택 "구현 방식 A 추천"을 따름) |
| 모델이 도구를 골라 **근거와 함께** 답함 | DONE에 `sources: [{uri, excerpt}]`를 담음 (아키텍처 8.2 형식) |
| **진행 이벤트** | TOOL_START·TOOL_END(`tool`, `ok`, `ms`)·DONE·FAILED, 이벤트마다 `taskId`·`seq`·`ts` (아키텍처 9.3) |
| 상태 저장·재개 | ❌ 아직 없음 (6절 참고) |

### 3.6 Harness (평가표 20점): 맛보기 수준

| 요소 | 반영 |
|---|---|
| Tools | 도구 오류를 예외로 죽이지 않고 결과 메시지로 모델에 돌려줌 (교재 "오류는 ToolMessage") |
| Agent Loop | `recursion_limit=16` ≈ 최대 8턴 (교재 권장값) |
| Recovery | 이벤트 전달이 실패해도 작업은 계속함 (아키텍처 4.2 "실패해도 작업은 계속") |
| 나머지 5개 | 없음 |

### 3.7 아키텍처 문서에서 그대로 가져온 데이터

- 질문 "허브를 11/25로 당기면?"과 결품 결과 "MCU: PP-05 100개, PP-04 100개" → **시퀀스 4.3(SC1)** 그대로
- 근거 문서를 설계변경 통지서로 잡은 이유 → **v0.2 변경 9번** "SC3 정답에 설계변경 통지서 인용이 있음"

## 4. 작게 만들기 위해 내린 판단

| 판단 | 이유 |
|---|---|
| backend를 **파일 1개**로 | 파일 1~4개 제한. 모듈 분리(3.1의 task, analysis 등)는 담당자 몫 |
| 작업·이벤트를 **메모리에** 저장 | DB 제외 요청 때문. 대신 구독 전에 쌓인 이벤트를 다시 보내게 해서, 202 직후 이벤트가 먼저 와도 유실되지 않음 (9.3 "지난 이벤트 재전송"을 간단히 구현) |
| 가짜 모델은 **도구 결과를 이어 받아** 다음 도구를 정함 | `analyze_impact` 결과의 `evidence_uri`로 Resource를 읽음. 키가 없어도 "도구 결과 → 다음 도구"라는 실제 흐름을 보여 줌 |
| Dockerfile 안에서 바로 `pip install` | requirements.txt 파일을 줄이려고. 버전은 고정함 (fastmcp 4.1.1, langgraph 1.2.16) |
| MCP Client를 **도구 호출마다** 연결 | 시작 순서 문제가 없음 (agent가 먼저 떠도 됨). 대신 매번 연결 비용이 듦 |

## 5. 기준과 다른 부분

| 항목 | 기준 | 뼈대 | 이유 / 고칠 사람 |
|---|---|---|---|
| frontend | Vue 3 **+ Vite** (교재·아키텍처) | **CDN Vue** 한 장 | Vite는 파일이 7개 이상 필요함. 프론트 담당이 전환 |
| 폴더 이름 | 노션 제안 `mcp/business`, `mcp/rag` | `business-mcp/`, `rag-mcp/` | compose 서비스 이름과 맞춤. 노션도 이걸로 고쳐 둠 |
| `/internal/**` | 내부망 전용 + 서비스 토큰 (아키텍처 10장) | backend 공개 포트(8080)로도 열려 있음 | 인증 제외 범위. 백엔드 담당이 추가 |
| postgres | 아키텍처 Data tier | ~~없음~~ → 10/20 오후 추가 (스키마·계정만, `db/init.sql`) | 테이블·seed는 B 파트 다음 단계 |
| 요청자 헤더 | `X-Task-Id`, `X-User-Id`, `X-Role` 전달 (아키텍처 6장) | 없음 | 인증이 없어서 전달할 값이 없음 |
| rag-mcp | `search_docs` 도구 + Resource (8.2) | Resource만 | 요청이 "Resource 한 번 읽기"였음 |
| healthcheck | 서비스마다 (7장 표) | ~~없음~~ → 10/21 서비스마다 추가 (decisions 6.41) | 뼈대 때는 "호출할 때 연결"로 피했다 |

## 6. 일부러 뺀 것과 나중에 들어갈 곳

| 뺀 것 | 평가표 항목 | 들어갈 곳 |
|---|---|---|
| 인증·역할 | 인증 10 | backend (Spring Security + JWT) |
| 승인 → 재개 (interrupt, checkpointer, `/approval`, `/resume`) | Agent 구현 15, Harness Guardrails | agent + backend |
| 실제 계산 | 기능 적합성 | backend analysis 모듈 |
| 조회·변경 도구, `destructive_hint`, 오류 코드별 ToolError | MCP 20 | business-mcp |
| 문서 검색, pgvector 색인 | MCP 20 | rag-mcp |
| 의도 분류, verify 반복 등 5장 노드 | Agent 설계 15 | agent |
| Harness 나머지 (prompts/*.md, 재시도, 대체 모델, LangSmith) | Harness 20 | agent |
| TOKEN 스트리밍, `GET /api/ai/tasks/{id}` | Agent 구현 | agent, backend |
| 대기열 (RabbitMQ) | 가산점 +10 | backend, agent |

## 7. 한 줄 요약

**교재 5장의 ①~⑦ 흐름과 금지 사항은 지켰고, 평가 점수가 걸린 깊이(MCP 도구 개수, Harness, 승인 재개, 인증)는 일부러 비워 둔 뼈대**다. 비운 칸은 파트별 담당이 채운다.
