from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError

mcp = FastMCP("rag-mcp")

# ponytail: 문서 원문을 코드에 고정. 색인(pgvector)과 search_docs 도구는 RAG 담당이 추가
DOCS = {
    ("ECN-2026-014", "3.2"): "설계변경 통지서 ECN-2026-014 제3.2조: 허브 PCB의 MCU는 2026-11-20 이후 입고분부터 신규 부품으로 교체한다.",
}


@mcp.resource("doc://{doc_id}/{clause_no}")
def get_clause(doc_id: str, clause_no: str) -> str:
    """문서 조항 원문을 돌려준다. 예: doc://ECN-2026-014/3.2"""
    text = DOCS.get((doc_id, clause_no))
    if text is None:
        raise ResourceError(f"없는 조항입니다: {doc_id}/{clause_no}. 있는 조항: {[f'{d}/{c}' for d, c in DOCS]}")
    return text


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=9102, path="/mcp", show_banner=False)
