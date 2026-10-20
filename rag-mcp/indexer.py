"""명시적으로 실행하는 색인 명령. 서버 시작 때 유료 임베딩을 자동 호출하지 않는다."""

import argparse
import json
import os
from pathlib import Path

from docstore import DocumentStore, RagError
from documents import load_documents
from embeddings import EmbeddingError, Embeddings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("RAG_DATA_DIR", "/app/data")))
    parser.add_argument("--check", action="store_true", help="문서와 hash만 검사 (DB·외부 API 호출 없음)")
    args = parser.parse_args()
    try:
        docs = load_documents(args.data_dir)
        if args.check:
            print(json.dumps({"documents": len(docs), "chunks": sum(len(d["chunks"]) for d in docs)}))
        else:
            print(json.dumps(DocumentStore.from_env().index(docs, Embeddings.from_env())))
        return 0
    except (ValueError, OSError, KeyError, RagError, EmbeddingError) as e:
        print(f"색인 실패: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
