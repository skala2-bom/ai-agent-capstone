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

실행 명령 (저장소 루트, `.env`에 `OPENAI_API_KEY` 입력):

```bash
# 문서 해시·조항 형식만 검사: 외부 API·DB 호출 없음
docker compose --profile indexing run --rm --no-deps --build rag-index python indexer.py --check

# 실제 색인: .env 키로 문서를 OpenAI 임베딩 API에 보내므로 비용이 발생한다
env -u OPENAI_API_KEY docker compose --env-file .env --profile indexing run --rm --build rag-index
```

`rag-mcp/sql/001_documents.sql`을 indexer가 `rag` 계정으로 적용한다. 업무 테이블·시드 초기화는 필요 없다.
`rag.document`는 원문·버전·시행일·해시, `rag.doc_chunk`는 조항·벡터, `rag.index_meta`는 모델·차원을 저장한다.
같은 원문/메타데이터는 건너뛰고, 삭제된 문서·조항은 재색인 때 지운다. 중간 오류는 전체 트랜잭션을 되돌린다.
색인이 없는 검색은 `RAG_NOT_INDEXED`, 설정이 다른 검색은 `RAG_INDEX_MISMATCH`로 실패하며 하드코딩 원문으로 대체하지 않는다.

기본 모델은 `text-embedding-3-small`, 차원은 1536이다. `RAG_EMBEDDING_MODEL`·`RAG_EMBEDDING_DIMENSIONS`를 바꾸면 서버도 같은 설정으로 재시작하고 다시 색인한다.
요청 형식은 [OpenAI Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create)를 따른다.
문서 6개 규모라 코사인 거리의 전수 검색을 사용하며, 이 변경에서는 ANN 인덱스를 추가하지 않았다.

색인 후 `search_docs(query, top_k=5, doc_type=None)`로 검색한다. `doc_type`은 `POLICY`, `ECN`, `CONTRACT`이고,
응답은 `doc_id`, `title`, `clause_no`, `version`, `effective_date`, `content`, `score`, `uri`다.
`doc://{doc_id}/{clause_no}`는 해당 조항, `doc://{doc_id}`는 문서 전체 원문을 반환한다.

## 지켜야 할 것

- **시스템과 같은 말을 해야 한다.** ID는 DB와 같고(`CASE-DOOR-V1`, `PO-2610-106` …), 규칙은 decisions.md와 같다. AI가 도구 결과와 다른 규칙을 근거로 인용하면 안 되기 때문이다. 결정이 바뀌면 이 문서도 같이 고친다.
- **금액을 적지 않는다.** RAG 검색 결과는 역할별로 가려지지 않는다. 문서에 금액이 있으면 생산관리 담당자의 질문에 AI가 금액을 인용할 수 있다 (SEC-001). 금액은 backend가 역할대로 가려 주는 도구 결과로만 나간다.
- 조항 번호를 바꾸지 않는다 — 인용 주소가 바뀐다. 조항을 지울 때는 번호를 비워 둔다.

현재 원문의 확인 사항: `POL-COST-EVAL/2.2`에는 30만/100만 원의 **등급 기준 금액**이 있다.
실제 단가·발주 비용은 아니지만, 위 “금액을 적지 않는다” 규칙의 예외인지 문서 담당자 확인이 필요하다.
이번 구현에서는 제공된 원문과 해시를 임의로 바꾸지 않았다. RAG 원문에는 역할별 마스킹을 적용하지 않는다.

## 원본

v1.1 원본과 함께 온 상세 목데이터(`seed.json`·`scenarios.json` 등)는 `docs/reference/data-v2/`에 있다. ID 체계가 달라 **DB나 평가 정답으로 쓰지 않는다** (평가 정답은 `eval/golden.json`). 원본 대비 바꾼 곳은 decisions.md 7.5.
