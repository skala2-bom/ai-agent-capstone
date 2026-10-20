# AI Agent Capstone (+MCP) — 정리본

> 원본: `AI-Agent Capstone(+MCP).pdf` (75p, 2026-10-19). 텍스트 레이어 정상.
> 실습 스택: Python + FastMCP 4.1(mcp 2.x, 프로토콜 `2026-07-28`) + LangChain/LangGraph 1.x. 실습 폴더 `전체코드/` (calculator → shop → resources·prompts → remote).
> 마지막에 **팀별 Capstone 평가표** 있음 (맨 아래).

| 장 | 핵심 한 줄 |
|---|---|
| 1. MCP 기본 구조 | Server가 Tool 정의, Client가 찾아서(list) 호출(call). 메시지는 JSON-RPC 2.0 |
| 2. 도구 네 개로 늘리기 | docstring = 모델이 읽는 도구 설명. 실패는 ToolError + 재시도 정보 |
| 3. Resource와 Prompt | Tool=모델, Resource=앱, Prompt=사용자가 선택 |
| 4. MCP 통신 방식 | stdio(로컬) vs Streamable HTTP(`POST /mcp`, compose 기본) |
| 5. 레퍼런스 아키텍처 | Vue → Spring Boot → FastAPI Agent → business_mcp/rag_mcp, 202 + SSE |
| 6. Harness | Agent = Model + Harness. 8개 구성 요소 |

---

## 1. MCP 기본 구조

**3요소**: MCP Server(기능=Tool 정의) / MCP Client(Server 연결 후 기능 찾아 호출) / MCP Protocol(발견·호출 표준 규약).

**import vs MCP**
| 항목 | 파이썬 import | MCP |
|---|---|---|
| 호출 | `add(10, 20)` | `await client.call_tool("add", {"a":10,"b":20})` |
| 함수 아는 법 | 소스 읽기 | `list_tools()`로 이름·설명·인자 스키마 받음 |
| 실행 위치 | 같은 프로세스 | Server 프로세스(stdio 또는 HTTP) |
| 언어 | 같아야 함 | 달라도 됨(Java Client도 가능) |
| 무엇을 호출할지 | 개발자 | 1·2단계는 개발자, **3단계는 모델** |

**구성 요소별 역할**: Host(사용자↔LLM 사이에서 Client 실행, 3단계 `agent.py`) / Client(Server 하나와 연결 유지) / Server(Tool·Resource·Prompt 공개) / Tool(함수 하나 + 이름·설명·스키마) / Transport(stdio 또는 Streamable HTTP) / JSON-RPC(`id`로 요청-응답 매칭).

**동작 7단계**: ① `@mcp.tool` 등록 → ② 연결 `server/discover` → ③ `tools/list` → ④ 목록·스키마 → ⑤ `tools/call add(10,20)` → ⑥ 실행 → ⑦ 결과 30.
- FastMCP 4.1(프로토콜 2026-07-28)은 연결 시 `server/discover`로 버전·기능 확인. **이전 명세는 `initialize` 요청 + `initialized` 알림**.

**Agent를 더하면**: 사용자 → LLM(어떤 Tool 쓸지 판단) → Agent(흐름 제어) → Client(call) → Server(실행). **MCP는 Agent 자체가 아니라 Agent가 외부 기능을 표준 방식으로 쓰게 하는 프로토콜**.

**server.py**
```python
from fastmcp import FastMCP
mcp = FastMCP("Calculator")

@mcp.tool
def add(a: int, b: int) -> int:
    """두 숫자를 더한다."""
    return a + b

if __name__ == "__main__":
    mcp.run(show_banner=False)   # 인자 없으면 stdio
```
- 함수 이름 → `name`, 인자 타입 → `inputSchema`, 반환 타입 → `outputSchema`, docstring → `description`
- 반환이 객체가 아니면(int) `{"result": ...}`로 감쌈 → Client `result.data`는 30, 원본은 `result.structured_content`
- `required`엔 기본값 없는 인자, `additionalProperties: false`

**client.py**
```python
SERVER = Path(__file__).parent / "server.py"   # 문자열 경로는 FastMCP 5에서 제거 예정 → Path로
async def main():
    async with Client(SERVER) as client:        # .py 경로 → 하위 프로세스 stdio 실행. 블록 나가면 종료
        tools = await client.list_tools()
        result = await client.call_tool("add", {"a": 10, "b": 20})
        print(result.data)
asyncio.run(main())
```
- server.py는 직접 실행 안 함. Client가 띄움.

**실제 JSON-RPC (stdio는 한 줄에 JSON 하나)**
```
C→S {"jsonrpc":"2.0","id":1,"method":"server/discover"}
C→S {"jsonrpc":"2.0","id":2,"method":"tools/list"}
C→S {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"add","arguments":{"a":10,"b":20}}}
S→C {... "result":{"content":[{"type":"text","text":"30"}],"structuredContent":{"result":30},"isError":false}}
```
- `content`=사람용, `structuredContent`=프로그램용. `isError: true`면 FastMCP Client가 `ToolError` 발생.

---

## 2. 도구 네 개로 늘리기

**함수 정의 → 도구 정의**
| 함수 요소 | 도구 정의 | 목적 |
|---|---|---|
| 함수 이름 | name | 호출할 도구 지정 (`get_price, get_stock, multiply, add`) |
| docstring | description | **모델이 도구 선택할 때 읽음** → 받는 값·돌려주는 값·**단위까지** |
| 인자 타입 힌트 | inputSchema | 이름·타입·필수 여부 |
| 반환 타입 | outputSchema / structuredContent | 형식 있는 결과 |
| `ToolError` | `isError: true` + 메시지 | 실패 이유 + **재시도에 필요한 정보** |
| `@mcp.tool` 없는 함수 | 공개 안 됨 | 공통 로직 감춤 (`find_product`) |

**상품 조회 Server** (`PRODUCTS` dict: 사과 1500/100, 바나나 2000/50, 오렌지 3000/30 — Level 3에서 PostgreSQL로 교체, 도구 정의는 그대로)
```python
def find_product(product: str) -> dict:
    item = PRODUCTS.get(product)
    if item is None:
        raise ToolError(f"'{product}'은(는) 없는 상품이다. 있는 상품: 사과, 바나나, 오렌지")
    return item

@mcp.tool
def get_price(product: str) -> int:
    """상품 이름(예: 사과)을 받아 한 개 가격(원)을 돌려준다."""
    return find_product(product)["price"]
```
- 오류 메시지에 "있는 상품 목록"을 넣어 3단계 모델이 그걸 보고 재호출하게 함.

**3단계 구분**
- 1단계: add 하나 / 2단계: 도구 4개, **코드가 호출 순서·인자 결정** (`get_price` 결과 1500 → `multiply(1500, 3)` → 4500) / 3단계: **모델이 순서 결정** (`agent.py`, `create_agent`, `OPENAI_API_KEY` 필요)
- 없는 상품 호출 시 Client는 `ToolError` 예외(기본 `raise_on_error=True`). 실행 로그의 `Error calling tool get_price`는 일부러 낸 오류라 정상.
- 이번 실습은 Python·FastMCP·LangChain·LangGraph만. Spring Boot·Vue 3는 이후 연결.

---

## 3. Resource와 Prompt

**세 Primitive — 누가 선택하느냐로 구분**
| 구분 | 무엇 | 선택 주체 | FastMCP | 요청 | 예 |
|---|---|---|---|---|---|
| Tool | 실행 기능 | **모델** | `@mcp.tool` | `tools/call` | 주문 조회, 환불, 가격 계산 |
| Resource | 읽기 전용 자료(URI) | **앱(Client/Host 코드)** | `@mcp.resource("scheme://…")` | `resources/read` | 설정값, 규정 원문, 사용자 정보 |
| Prompt | 지시문 템플릿 | **사용자**(메뉴·명령) | `@mcp.prompt` | `prompts/get` | 리포트 작성, 역할 지정 |

- 비슷한 자료 여러 개 → **Resource 템플릿** `repos://{owner}/{repo}/info` (변수가 같은 이름 함수 인자로 전달)

**Resource**
```python
@mcp.resource("resource://greeting")          # 고정 + 문자열
def get_greeting() -> str: ...
@mcp.resource("data://config")                # 고정 + dict → JSON
def get_config() -> dict: ...
@mcp.resource("repos://{owner}/{repo}/info")  # 템플릿
def get_repo_info(owner: str, repo: str) -> dict: ...

# Client
await client.list_resources()            # 고정 주소 목록
await client.list_resource_templates()   # 템플릿 목록 (.uri_template)
contents = await client.read_resource("data://config")
json.loads(contents[0].text)             # 결과는 목록 → [0].text
```

**Prompt** — 반환 형식 3가지: `str`(user 메시지 하나) / `PromptResult(messages=[...], description=...)` / `list[Message]`(role="assistant"도 가능)
```python
result = await client.get_prompt("ask_about_topic", {"topic": "MCP"})
```
- **Server는 메시지 조립만**, 실제 일은 메시지를 받은 LLM이 함. 모델 호출 없음.

**LLM에 연결하기 — 모델은 Tool만 호출 가능** → Host가 **가상 도구**로 다리를 놓음
| | Resource | Prompt |
|---|---|---|
| 모델에 주는 정보 | 읽을 수 있는 주소 목록(system 메시지) | 프롬프트 이름·필수 인자(system 메시지) |
| 가상 도구 | `read_resource(uri)` | `get_prompt(name, args)` |
| 선택 시 | `client.read_resource(uri)` 결과를 도구 결과로 | 받은 메시지를 대화에 추가 |

> **Capstone 요건**: `rag_mcp`에 문서 원문을 읽는 **Resource 1개 이상**. 검색 도구는 chunk 반환, 근거 패널은 Resource로 원문 읽음. Prompt는 선택.

---

## 4. MCP 통신 방식

**전송·연결 방식**
| 방식 | 내용 | 언제 | 주의 |
|---|---|---|---|
| stdio | Client가 Server를 하위 프로세스로 실행, 표준 입출력 | 로컬 개발, 데스크톱 앱(Claude Desktop, Cursor) | **부모 환경 변수 안 물려받음**, **stdout에 print하면 메시지 깨짐** |
| Streamable HTTP | 실행 중 Server에 `POST /mcp` | compose, 원격, 여러 Agent 공유 | **`/mcp` 빠뜨리면 404** |
| HTTP SSE | 이전 명세 | 옛 Server 연결 | 새 Server는 Streamable HTTP로 |
| 인메모리 | Server 객체 직접 연결 | 단위 테스트 | 프로세스·네트워크 없음 |
| Proxy | stdio처럼 보이며 원격 HTTP로 전달 | stdio만 되는 프로그램 | FastMCP 4.1은 `create_proxy` |

- 어느 방식이든 메시지는 같은 JSON-RPC, **Server 업무 코드는 그대로**. `mcp.run()` 인자만 다름: `mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")` (FastMCP 2의 `"streamable-http"`와 같음)
- HTTP 기록: 헤더에 `mcp-protocol-version`, `mcp-method: tools/call`, `mcp-name: multiply`까지 실림(본문 안 열어도 무슨 요청인지 보임). `accept: application/json, text/event-stream`.

**Client 연결 4가지** (`Client(…)`에 넘기는 값만 다름)
```python
Client(mcp)                                  # ① 인메모리 — 테스트
Client(Path("rag_mcp/server.py"))            # ② stdio — 로컬. 상대 import 있으면 python -m
Client("http://127.0.0.1:9101/mcp")          # ③ HTTP — 팀 프로젝트 기본
Client({"mcpServers": {                      # ④ 설정 — 여러 Server를 한 Client로
  "business": {"url": "http://127.0.0.1:9101/mcp", "transport": "http"},
  "rag": {"command": sys.executable, "args": ["-m", "rag_mcp.server"]}}})
# ④는 도구 이름에 접두어: business_get_order
```

**Capstone 연결**: 도구 단위 테스트=인메모리 / 로컬 디버깅=stdio / **docker compose=Streamable HTTP** (`http://business-mcp:9101/mcp`, `http://rag-mcp:9102/mcp`) / 두 Server를 한 Client=`mcpServers`.
Agent 서버 연결 순서(Pipe and Filter): Server 기동 → `server/discover` → `tools/list` → **`to_langchain` 도구 변환** → Agent `tools/call`.

**Proxy**: `create_proxy("http://서버:8000/mcp", name=...)` → `proxy.run()`은 stdio. env는 `PythonStdioTransport("proxy_server.py", env={...})`로 직접 넘김.

**자주 나는 연결 오류**
| 증상 | 원인 | 해결 |
|---|---|---|
| Client failed to connect | Server 미실행·포트 다름 | Server 먼저 실행, 주소·포트 확인 |
| MCPError: Not Found 404 | URL에 `/mcp` 누락 | `http://host:port/mcp` |
| Unknown tool: get_order | 다중 Server 설정 → 접두어 | `business_get_order` |
| stdio Connection closed | 상대 import Server를 파일 경로로 실행 | `command`+`args`로 `python -m` |
| Proxy 도구 목록이 빔 | stdio가 env 못 물려받음 | `PythonStdioTransport(..., env=...)` |
| DeprecationWarning (uriTemplate 등) | mcp 2.x에서 snake_case로 변경 | `uri_template`, `input_schema`, `destructive_hint` |

원격 실습: 터미널1 `python server_remote.py`(닫지 말 것) / 터미널2 `python client_remote.py` → `multiply result: 21.0`. 터미널1 끄고 다시 돌리면 연결 오류(= Server 먼저 실행 확인). `REMOTE_MCP_URL=... python client_remote.py`로 주소 지정.

---

## 5. 레퍼런스 아키텍처

**5가지 요구 사항 → 아키텍처 결정**
| 요구 | 결정 |
|---|---|
| LLM 응답이 수 초~수십 초 | 작업으로 접수, **202 즉시 반환**, 진행은 **SSE 이벤트** |
| 업무 규칙은 한 곳에서 | **business_mcp는 DB 직접 접근 X**, Spring Boot `/internal/**` 호출 |
| 브라우저 노출 주소 최소화 | 공개 포트는 **화면 + Spring Boot뿐**, 나머지 docker network 안 |
| 데이터 변경은 사람이 확인 | 변경 도구 앞에서 상태 저장·정지 → 승인 후 **같은 thread_id로 재개** |
| 답변엔 근거 문서 | 문서는 미리 색인, rag_mcp가 Vector DB에서 근거 검색 |

**뷰 구성** (그림 한 장으로 다 답하지 않음)
| 뷰 | 질문 | 스타일 |
|---|---|---|
| 전체 구성도 | 어떤 서비스, 무엇과 연결 | Container, Client-Server |
| 논리 뷰 | 책임 계층, 누가 누구 호출 | Layered |
| 런타임 뷰 | 메시지 순서 | Sequence, Event Driven |
| 배포 뷰 | 어느 tier·경계에서 실행 | N-tier |
| 연결 뷰 | 도구·근거 문서 출처 | Client-Server, Pipe and Filter |
| Harness | 모델 감싼 코드 구성 | Microkernel |

도식 표기: 동기 호출=채운 화살촉, 비동기 메시지=열린 화살촉, 응답=점선, Trust Boundary=docker network.

**구성도 처리 흐름 (번호 7개)**
1. Vue → Spring Boot: `POST /api/ai/tasks` → **202 {taskId}** (HTTPS)
2. Spring Boot → Agent 서버: 작업 상태 저장 후 실행 요청 (내부 HTTP)
3. LangGraph Agent → LLM API: Harness가 Instructions·Context 붙여 호출, 모델이 다음 도구 제안
4. MCP Client → MCP Server: `tools/call`. **변경 도구는 Guardrails가 승인 전까지 멈춤** (MCP `/mcp`)
5. business_mcp → Spring Boot: `/internal/**`로 업무 데이터 읽기·쓰기
6. rag_mcp → Vector DB: 질문과 가까운 chunk를 근거로 반환
7. Agent 서버 → Vue: 진행 이벤트(TOKEN·TOOL_START·APPROVAL_REQUIRED·DONE) **SSE 중계**

**구성 요소별 금지 사항 — 평가에서 위반 여부를 봄**
| 구성 요소 | 역할 | **금지** |
|---|---|---|
| Vue 3 | 질문 입력, 진행·근거·승인 화면 | 권한 판단, AI 서버 직접 호출 |
| Spring Boot | 업무 API, 작업 생성·상태 저장, SSE 중계, 내부 API | **LLM 호출** |
| FastAPI Agent | Harness로 모델 감싸기, 도구 선택, 승인 대기 | **DB 직접 쓰기** |
| business_mcp | 조회·변경 도구, `/internal/**` 호출 | **업무 규칙 복제** |
| rag_mcp | Vector DB 검색, 출처 반환 | **업무 데이터 변경** |
| PostgreSQL | 업무 데이터, 작업 이력, checkpointer | – |
| Vector DB | chunk, Embedding, 출처 메타데이터 | – |

**기술 스택**: Vue 3+Vite(+Pinia) / Spring Boot 3.x / FastAPI + LangGraph 1.x + LangChain 1.x / FastMCP 4.1, mcp 2.x / Chroma 또는 pgvector / PostgreSQL 16 / OpenAI 등(키는 `.env`에만).

**논리 아키텍처 5계층** — 위 계층은 바로 아래만 호출
Presentation(Vue) → Application(Spring Boot) → AI Serving/Orchestration(FastAPI·LangGraph·Harness) → Tool(business_mcp·rag_mcp) → Data(PostgreSQL·Vector DB)
- **의도된 예외**: business_mcp(Tool)가 Application의 `/internal/**`를 호출 = 아래→위 호출. 업무 규칙을 Spring Boot 한 곳에 두기 위함, `/internal/**`는 docker network 안에서만 열림.

**동기 vs 비동기 경로**
| 경로 | 거치는 일 | 응답 |
|---|---|---|
| 동기(수백 ms) | 업무 조회·등록, 작업 상태 조회, 승인·거절 기록 | 바로 200 |
| 비동기(수 초~수십 초) | Agent 질의응답, 문서 색인·재색인, 일괄 분석·리포트, 승인 후 재개 | 202 후 SSE 이벤트 |

**전체 흐름 1 (질문·근거 검색)**: POST → 202 → `/stream` SSE 구독 → 작업 실행 → 모델 호출 → tool_calls → rag_mcp `tools/call` → Vector DB 검색 → 조항·출처 → 진행 이벤트 → SSE 중계.
**전체 흐름 2 (변경 도구 승인)**: 모델이 변경 도구 선택 → **checkpointer에 상태 저장** → 승인 요청 이벤트 → 승인 카드(SSE) → `POST /approval` → **thread_id로 재개** → business_mcp `tools/call` → `/internal/**` → DB 변경 → DONE → SSE.

**API 계약 — Vue는 5개 API만, AI 서버 주소는 브라우저에 안 보임**
| Method | Path | 응답 |
|---|---|---|
| POST | `/api/ai/tasks` | 202 {taskId} |
| GET | `/api/ai/tasks/{id}` | status, answer, sources |
| GET | `/api/ai/tasks/{id}/stream` | text/event-stream |
| POST | `/api/ai/tasks/{id}/approval` | 202 |
| GET | `/api/ai/tasks?mine=true` | 내 작업 목록 |

**이벤트**: TOKEN(답변 이어 표시) / TOOL_START·TOOL_END("주문 조회 중…") / APPROVAL_REQUIRED(승인 카드: 도구·인자·금액) / DONE·FAILED(스트림 닫고 목록 갱신).
→ Publish-Subscribe: Agent 서버는 어느 화면 영역이 받는지 모름. 화면 영역 추가해도 Agent 코드 불변.

**배포 (docker compose)** — compose 안에선 서비스 이름이 주소
| 서비스 | 기술 | 포트 | 공개 | 의존(먼저 실행) | Tier |
|---|---|---|---|---|---|
| frontend | Vue 3 + nginx | 80 | **공개** | backend | Client |
| backend | Spring Boot | 8080 | **공개** | postgres, agent | Application |
| agent | FastAPI | 8000 | 내부 | business-mcp, rag-mcp | AI |
| business-mcp | FastMCP | 9101 | 내부 | backend(`/internal/**`) | AI |
| rag-mcp | FastMCP | 9102 | 내부 | vectordb | AI |
| postgres | PostgreSQL 16 | 5432 | 내부 | – | Data |
| vectordb | Chroma / pgvector | 8001 / 5432 | 내부 | – | Data |

- **layer = 논리적 역할 분리, tier = 물리·배포 분리**. 한 tier에 여러 layer 가능(Tool·AI Serving 둘 다 AI tier).
- LLM Provider는 외부 서비스, **agent만 HTTPS로 호출**.

**색인 경로 (오프라인, Pipe and Filter)**: Documents → Chunking(조항 단위) → Embedding(같은 모델) → Indexing(출처·버전 저장). **Embedding 모델 이름·차원을 색인 메타데이터에 남길 것** — 질문도 같은 모델로 임베딩해야 함.

---

## 6. Harness

**Agent = Model + Harness**. 모델은 다음 응답·도구를 **제안만**, 실행·한도·기록·복구는 Harness.
| Model | Harness |
|---|---|
| 다음 응답/도구 제안 | 프롬프트·도구 선택해 모델에 전달 |
| 도구 직접 실행 못 함 | 제안된 도구 실행, 결과 반환 |
| 몇 번째 시도·비용 모름 | 반복 횟수·시간·권한 제한 |
| 대화 끝나면 기억 X | 상태 저장, 실패 시 복구 |
| 같은 모델이면 팀 간 차이 없음 | **모든 단계 기록. 팀마다 차이가 나는 부분** |

**8개 구성 요소 (Microkernel)** — 왼쪽 4개는 모델 입력, 오른쪽 4개는 실행 통제·기록
| 구성 요소 | 적용 위치 | 최소 기준 (권장값, 바꾸면 Harness 명세에 기록) |
|---|---|---|
| Instructions | `prompts/*.md` | 역할·도구 사용 규칙·금지·출력 형식. **코드에 직접 넣지 말고 버전 관리** |
| Tools | MCP Client | 도구 결과는 길이 잘라 넘기고, 오류는 `ToolMessage`로 반환 |
| Context | 메시지 정리·RAG 주입 | Token 예산 설정, 넘으면 오래된 대화 요약 |
| State·Memory | checkpointer·store | thread_id 단위 저장, 승인 대기 후 그대로 재개 |
| Agent Loop | LangGraph StateGraph | **최대 8턴, 작업 120초, 같은 도구 3회까지** |
| Guardrails | approval Node 또는 HITL middleware | 변경 도구는 interrupt로 정지, 도구 결과 개인정보 마스킹 |
| Recovery | 모델 호출·Agent 서버 | **429·503은 2회 재시도**, 계속 실패하면 대체 모델 또는 FAILED |
| Observability | LangSmith | trace_id 하나로 Node·도구·Token·지연 모니터링 |

**구현 방식**: A = 직접 만든 StateGraph / B = `create_agent` + middleware.
**승인**: 변경 도구는 **`destructiveHint`로 표시**, 실행 전 멈춰 checkpointer에 저장 후 사람 결정 대기.

**확인 방법 (둘 다 제품소개자료 증빙 화면이 됨)**
- 관측: LangSmith trace, 메시지·로그·trace에 같은 trace_id, Node·도구·Token·지연·비용 기록, FAILED 작업은 trace 링크를 작업 이력에 저장
- 평가: 프롬프트·한도·모델 바꾸면 평가셋 재실행, **전후 결과표 나란히**, 한도 초과·재시도·대체 모델 발생 건수, 장애 시연을 같은 스크립트로 반복
- 요소마다 테스트 하나씩 반영

---

## 팀별 Capstone 평가표 (기본 100 + 가산 10)

| 항목 | 주요 내용 | 배점 |
|---|---|---|
| Biz. 아이템 | 참신 + **B2B 또는 B2B2C**. 효용 크면 팀 프로젝트 연속성 유지 가능(운영진·교수진 동의 시만) | 10 |
| 아키텍처 설계 | 전체 AI 아키텍처, **ISO/IEC 25010**(구 9126) 품질 고려 | 10 |
| AI Agent 설계 | State 구조·Graph 흐름(Node·분기·반복)이 업무에 맞음, Agentic AI 요소 | 15 |
| AI Agent 구현 | Graph가 코드에 반영, 모델이 도구 선택해 **근거와 함께** 답, **상태 저장·재개 + 진행 이벤트** 동작, Agentic 요소 구현 | 15 |
| **MCP 설계·구현** | business_mcp·rag_mcp가 조회·변경 도구와 설명 갖춤, Agent가 연결·호출, **오류·예외 처리** | **20** |
| **Harness** | 주요 구성 요소 적절히 적용 | **20** |
| 인증 | 사용자 인증 처리 | 10 |
| 가산점: 대기열 | RabbitMQ·Kafka로 작업 큐잉 + 실패 작업 재시도 | +10 |

**배점 큰 순서로 보면**: MCP(20)·Harness(20) > Agent 설계·구현(30) > 나머지. 체크할 것:
- [ ] business_mcp: 조회+변경 도구, docstring에 단위까지, ToolError에 재시도 정보, `/internal/**`만 호출(DB 직접 X)
- [ ] rag_mcp: 검색 도구(chunk+출처) + **원문 Resource 1개 이상**
- [ ] compose에서 Streamable HTTP, 공개 포트는 frontend·backend만
- [ ] 202 + SSE 이벤트 4종, API 5개
- [ ] 변경 도구 `destructiveHint` → interrupt → checkpointer → 승인 후 같은 thread_id 재개
- [ ] Harness 8요소 + 권장값(8턴/120초/같은 도구 3회/429·503 2회 재시도) + LangSmith trace + 평가셋 전후표
- [ ] 금지 사항 위반 없음 (Spring Boot LLM 호출 X, Agent DB 쓰기 X, Vue가 AI 서버 직접 호출 X)
