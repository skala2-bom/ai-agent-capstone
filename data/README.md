# RAG 근거 문서 (`data/`)

rag-mcp가 색인하고 `search_docs`·`doc://{doc_id}/{clause_no}`로 돌려줄 **원문**이다 (아키텍처 8.2·8.3, decisions.md 6.9).

| 파일 | 내용 |
|---|---|
| `documents.json` | 문서 목록: `doc_id`·제목·종류·버전·시행일·원문 경로·`content_hash`(sha256) |
| `docs/*.md` | 원문 6개: 구매 규정(POL-PURCHASE), 비용 등급 규정(POL-COST-EVAL), 설계변경 통지서(ECN-2026-014), 협력사 계약 3개(CT-SUP01~03) |

## 색인하는 법 (C 파트)

- **조항 단위로 자른다**: `## 2.2` 제목 하나 = 청크 하나. 인용 주소는 `doc://POL-PURCHASE/2.2`.
- 메타데이터는 `documents.json`에서: `doc_id`, `title`, `version`, `doc_type`, `effective_date`.
- `content_hash`가 바뀐 문서만 다시 색인하면 된다.
- 테이블은 `rag` 스키마(`rag` 계정, pgvector 확장 있음, contracts.md 1.3).

## 지켜야 할 것

- **시스템과 같은 말을 해야 한다.** ID는 DB와 같고(`CASE-DOOR-V1`, `PO-2610-106` …), 규칙은 decisions.md와 같다. AI가 도구 결과와 다른 규칙을 근거로 인용하면 안 되기 때문이다. 결정이 바뀌면 이 문서도 같이 고친다.
- **금액을 적지 않는다.** RAG 검색 결과는 역할별로 가려지지 않는다. 문서에 금액이 있으면 생산관리 담당자의 질문에 AI가 금액을 인용할 수 있다 (SEC-001). 금액은 backend가 역할대로 가려 주는 도구 결과로만 나간다.
- 조항 번호를 바꾸지 않는다 — 인용 주소가 바뀐다. 조항을 지울 때는 번호를 비워 둔다.

## 원본

v1.1 원본과 함께 온 상세 목데이터(`seed.json`·`scenarios.json` 등)는 `docs/reference/data-v2/`에 있다. ID 체계가 달라 **DB나 평가 정답으로 쓰지 않는다** (평가 정답은 `eval/golden.json`). 원본 대비 바꾼 곳은 decisions.md 7.5.
