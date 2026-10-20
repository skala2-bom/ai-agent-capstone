"""rag-mcp — Tool 계층 (아키텍처 3장, 8.2).

근거 문서를 다룬다. 두 가지를 공개한다.
  - Tool `search_docs`   : 질문과 가까운 조항 chunk를 찾아 준다 (아직 없음)
  - Resource `doc://...` : 조항 **원문**을 그대로 읽어 준다 (아래 구현)

금지 (교재 5장)
  - 업무 데이터 변경 금지. 읽기만 한다.

왜 Resource를 두나
  교재 Capstone 요건이 "원문을 읽는 Resource 1개 이상"이다. 검색 도구는 잘린
  chunk를 돌려주는데, 화면의 근거 패널에는 조항 원문이 보여야 하기 때문이다.
  Tool과 Resource의 차이는 **누가 고르느냐**다 (교재 3장).
    Tool     → 모델이 고른다
    Resource → 앱(여기서는 agent 코드)이 고른다
  모델은 Tool만 부를 수 있어서, agent가 read_doc이라는 "가상 도구"로 감싸 준다.
  → agent/main.py 의 read_doc 참고

RAG 담당이 채울 것
  - search_docs 도구 (chunk + 출처: doc_id, title, clause_no, version, score, uri)
  - 조항 단위 청킹 → 임베딩 → pgvector 색인 (아키텍처 8.3)
  - 임베딩 모델 이름·차원을 색인 메타데이터에 남길 것. 질문도 **같은 모델**로
    임베딩해야 하고, 모델을 바꾸면 전체를 다시 색인해야 한다.
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError

mcp = FastMCP("rag-mcp")

# ponytail: 문서 원문을 코드에 고정. 색인(pgvector)과 search_docs 도구는 RAG 담당이 추가
#
# 이 한 건을 고른 이유: 아키텍처 v0.2 변경 9번에 "SC3 정답에 설계변경 통지서
# 인용이 있음"이라고 돼 있어서, 데모 시나리오에서 실제로 인용될 문서를 골랐다.
DOCS = {
    ("ECN-2026-014", "3.2"): "설계변경 통지서 ECN-2026-014 제3.2조: 허브 PCB의 MCU는 2026-11-20 이후 입고분부터 신규 부품으로 교체한다.",
}


@mcp.resource("doc://{doc_id}/{clause_no}")
def get_clause(doc_id: str, clause_no: str) -> str:
    """문서 조항 원문을 돌려준다. 예: doc://ECN-2026-014/3.2"""
    # 주소 안의 {doc_id}, {clause_no}가 같은 이름의 함수 인자로 들어온다
    # (교재 3장 Resource 템플릿).
    text = DOCS.get((doc_id, clause_no))
    if text is None:
        # 오류 메시지에 **있는 조항 목록**을 넣는다. 모델이 그 목록을 보고
        # 올바른 주소로 다시 부를 수 있다 (교재 2장 find_product 패턴).
        raise ResourceError(f"없는 조항입니다: {doc_id}/{clause_no}. 있는 조항: {[f'{d}/{c}' for d, c in DOCS]}")
    return text


if __name__ == "__main__":
    # business-mcp와 같은 이유로 Streamable HTTP + /mcp (교재 4장).
    mcp.run(transport="http", host="0.0.0.0", port=9102, path="/mcp", show_banner=False)
