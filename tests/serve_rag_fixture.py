"""격리 테스트 DB에만 가짜 벡터를 색인하고 실제 RAG MCP 서버를 띄운다."""
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from documents import load_documents
from rag_fixture import FixtureEmbeddings
import server

if not os.environ.get("RAG_DB_URL", "").endswith("/capstone_ai_test"):
    raise RuntimeError("테스트 DB 이외에는 fixture를 색인할 수 없습니다.")
server.Embeddings.from_env = classmethod(lambda cls: FixtureEmbeddings())
print(server.store().index(load_documents(Path("/repo/data")), FixtureEmbeddings()), flush=True)
server.mcp.run(transport="http", host="0.0.0.0", port=9102, path="/mcp", show_banner=False)
