"""문서 검색 Tool과 원문 Resource. 업무 DB에는 접근하지 않는다."""

import asyncio
from functools import lru_cache

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from docstore import DocumentStore, RagError
from embeddings import EmbeddingError, Embeddings

mcp = FastMCP("rag-mcp")


@lru_cache
def store() -> DocumentStore:
    return DocumentStore.from_env()


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """docker healthcheck용. 문서 DB에 붙으면 200, 못 붙으면 503. 색인 여부는 indexed로만 알린다"""
    try:
        status = await asyncio.to_thread(store().health)
    except RagError as e:   # RAG_DB_URL 미설정
        status = {"ok": False, "error": str(e)}
    return JSONResponse(status, status_code=200 if status["ok"] else 503)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
def search_docs(query: str, top_k: int = 5, doc_type: str | None = None) -> list[dict]:
    """규정·ECN·협력사 계약을 조항 단위로 검색한다.

    query: 찾을 내용(품목 ID·문서 ID·생산일을 포함하면 좋다).
    top_k: 1~10, 기본 5. doc_type: POLICY | ECN | CONTRACT 또는 생략.
    반환: [{doc_id, title, clause_no, version, effective_date, content, score, uri}].
    검색 결과의 URI로 원문 Resource를 읽은 뒤 인용한다. 시행일은 생산일과 대조한다.
    """
    try:
        return store().search(query, top_k, doc_type, Embeddings.from_env())
    except (RagError, EmbeddingError) as e:
        raise ToolError(str(e)) from None


@mcp.resource("doc://{doc_id}/{clause_no}")
def get_clause(doc_id: str, clause_no: str) -> str:
    """색인된 실제 조항 원문. 검색과 같은 문서 버전을 사용한다."""
    try:
        return store().read(doc_id, clause_no)
    except RagError as e:
        raise ResourceError(str(e)) from None


@mcp.resource("doc://{doc_id}")
def get_document(doc_id: str) -> str:
    """색인된 문서 전체 원문."""
    try:
        return store().read(doc_id)
    except RagError as e:
        raise ResourceError(str(e)) from None


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=9102, path="/mcp", show_banner=False)
