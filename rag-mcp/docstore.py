"""rag 스키마 저장·벡터 검색·원문 조회. 원문과 벡터는 같은 트랜잭션에서 교체한다."""

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from documents import DOC_ID, DOC_TYPES
from embeddings import EmbeddingError, validate_vector, vector_literal


class RagError(RuntimeError):
    pass


class DocumentStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    @classmethod
    def from_env(cls):
        dsn = os.getenv("RAG_DB_URL")
        if not dsn:
            raise RagError("[RAG_CONFIG] RAG_DB_URL을 rag 계정으로 설정하세요.")
        return cls(dsn)

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3)

    def index(self, docs: list[dict], embedder) -> dict:
        try:
            with self.connect() as conn:
                conn.execute("select pg_advisory_xact_lock(726401)")
                conn.execute(Path(__file__).with_name("sql").joinpath("001_documents.sql").read_text())
                meta = conn.execute("select * from rag.index_meta where singleton").fetchone()
                rebuild = (not meta or meta["embedding_model"] != embedder.model
                           or meta["embedding_dimensions"] != embedder.dimensions)
                existing = {d["doc_id"]: d for d in conn.execute("select * from rag.document")}
                changed = 0
                for doc in docs:
                    old = existing.get(doc["doc_id"])
                    keys = ("title", "doc_type", "version", "content_hash")
                    if (not rebuild and old and all(old[k] == doc[k] for k in keys)
                            and str(old["effective_date"]) == doc["effective_date"]):
                        continue
                    inputs = [f"{doc['title']} ({doc['doc_id']}) 제{c['clause_no']}조\n{c['content']}" for c in doc["chunks"]]
                    vectors = embedder.embed(inputs)
                    if len(vectors) != len(inputs):
                        raise RagError("[RAG_EMBEDDING_INVALID] 청크 수와 벡터 수가 다릅니다.")
                    for vector in vectors:
                        validate_vector(vector, embedder.dimensions)
                    conn.execute("""
                        insert into rag.document (doc_id, title, doc_type, version, effective_date, content_hash, raw_content)
                        values (%s,%s,%s,%s,%s,%s,%s)
                        on conflict (doc_id) do update set title=excluded.title, doc_type=excluded.doc_type,
                        version=excluded.version, effective_date=excluded.effective_date,
                        content_hash=excluded.content_hash, raw_content=excluded.raw_content
                        """, [doc[k] for k in ("doc_id", "title", "doc_type", "version", "effective_date", "content_hash", "raw_content")])
                    conn.execute("delete from rag.doc_chunk where doc_id=%s", (doc["doc_id"],))
                    for chunk, vector in zip(doc["chunks"], vectors):
                        conn.execute("insert into rag.doc_chunk values (%s,%s,%s,%s::rag.vector)",
                                     (doc["doc_id"], chunk["clause_no"], chunk["content"], vector_literal(vector)))
                    changed += 1
                deleted = conn.execute("delete from rag.document where not (doc_id = any(%s))",
                                       ([d["doc_id"] for d in docs],)).rowcount
                conn.execute("""
                    insert into rag.index_meta (singleton,embedding_model,embedding_dimensions) values (true,%s,%s)
                    on conflict (singleton) do update set embedding_model=excluded.embedding_model,
                    embedding_dimensions=excluded.embedding_dimensions, updated_at=now()
                    """, (embedder.model, embedder.dimensions))
                return {"documents": len(docs), "updated": changed, "deleted": deleted, "reembedded": bool(rebuild)}
        except EmbeddingError as e:
            raise RagError(str(e)) from None
        except psycopg.Error:
            raise RagError("[RAG_DB_UNAVAILABLE] 색인 실패. rag DB 연결·권한을 확인하세요.") from None

    def search(self, query: str, top_k: int, doc_type: str | None, embedder) -> list[dict]:
        if not query.strip() or len(query) > 4000 or not 1 <= top_k <= 10:
            raise RagError("[INVALID_ARGUMENT] query는 1~4000자, top_k는 1~10으로 주세요.")
        if doc_type is not None and doc_type not in DOC_TYPES:
            raise RagError("[INVALID_ARGUMENT] doc_type은 POLICY, ECN, CONTRACT 중 하나입니다.")
        try:
            with self.connect() as conn:
                conn.execute("set transaction isolation level repeatable read read only")
                meta = conn.execute("select * from rag.index_meta where singleton").fetchone()
                if not meta:
                    raise RagError("[RAG_NOT_INDEXED] 먼저 indexer를 실행하세요.")
                if (meta["embedding_model"], meta["embedding_dimensions"]) != (embedder.model, embedder.dimensions):
                    raise RagError("[RAG_INDEX_MISMATCH] 검색 모델·차원이 색인과 다릅니다. 같은 설정으로 재색인하세요.")
                vector = embedder.embed([query])[0]
                validate_vector(vector, embedder.dimensions)
                rows = conn.execute("""
                    select d.doc_id,d.title,c.clause_no,d.version,d.effective_date::text,c.content,
                           1 - (c.embedding <=> %s::rag.vector) as score
                    from rag.doc_chunk c join rag.document d using (doc_id)
                    where (%s::text is null or d.doc_type=%s)
                    order by c.embedding <=> %s::rag.vector, d.doc_id,c.clause_no limit %s
                    """, (vector_literal(vector), doc_type, doc_type, vector_literal(vector), top_k)).fetchall()
                return [{**r, "uri": f"doc://{r['doc_id']}/{r['clause_no']}"} for r in rows]
        except psycopg.errors.UndefinedTable:
            raise RagError("[RAG_NOT_INDEXED] 먼저 indexer를 실행하세요.") from None
        except psycopg.Error:
            raise RagError("[RAG_DB_UNAVAILABLE] 문서 DB 연결·권한을 확인하세요.") from None
        except EmbeddingError as e:
            raise RagError(str(e)) from None

    def read(self, doc_id: str, clause_no: str | None = None) -> str:
        if not DOC_ID.fullmatch(doc_id):
            raise RagError("[NOT_FOUND] 검색 결과의 문서 ID를 사용하세요.")
        try:
            with self.connect() as conn:
                if clause_no is None:
                    row = conn.execute("select raw_content as content from rag.document where doc_id=%s", (doc_id,)).fetchone()
                else:
                    row = conn.execute("select content from rag.doc_chunk where doc_id=%s and clause_no=%s",
                                       (doc_id, clause_no)).fetchone()
                if row is None:
                    raise RagError("[NOT_FOUND] 없는 문서/조항입니다. search_docs가 반환한 URI를 사용하세요.")
                return row["content"]
        except psycopg.errors.UndefinedTable:
            raise RagError("[RAG_NOT_INDEXED] 먼저 indexer를 실행하세요.") from None
        except psycopg.Error:
            raise RagError("[RAG_DB_UNAVAILABLE] 문서 DB 연결·권한을 확인하세요.") from None
