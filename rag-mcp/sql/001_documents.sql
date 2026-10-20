-- rag 계정으로 실행. public 업무 테이블과 agent checkpointer를 변경하지 않는다.
CREATE TABLE IF NOT EXISTS rag.index_meta (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    embedding_model text NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rag.document (
    doc_id text PRIMARY KEY,
    title text NOT NULL,
    doc_type text NOT NULL CHECK (doc_type IN ('POLICY', 'ECN', 'CONTRACT')),
    version text NOT NULL,
    effective_date date NOT NULL,
    content_hash text NOT NULL,
    raw_content text NOT NULL
);
CREATE TABLE IF NOT EXISTS rag.doc_chunk (
    doc_id text NOT NULL REFERENCES rag.document ON DELETE CASCADE,
    clause_no text NOT NULL,
    content text NOT NULL,
    embedding rag.vector NOT NULL,
    PRIMARY KEY (doc_id, clause_no)
);
-- 문서 6개 규모에서는 cosine exact scan. 모델 차원이 바뀌면 전체 색인을 원자적으로 교체한다.
