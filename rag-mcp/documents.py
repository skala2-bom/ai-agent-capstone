"""manifest 검증·조항 청킹. 네트워크/DB 없이 시험할 수 있는 원문 처리."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path

CLAUSE = re.compile(r"^##\s+(\d+(?:\.\d+)*)\s*$", re.MULTILINE)
DOC_ID = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")
DOC_TYPES = {"POLICY", "ECN", "CONTRACT"}


def chunks(text: str) -> list[dict]:
    headings = list(CLAUSE.finditer(text))
    result, seen = [], set()
    for i, match in enumerate(headings):
        number = match.group(1)
        content = text[match.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)].strip()
        if number in seen or not content:
            raise ValueError(f"빈 조항 또는 중복 조항: {number}")
        seen.add(number)
        result.append({"clause_no": number, "content": content})
    if not result:
        raise ValueError("## 2.2 형식의 조항 제목이 없습니다.")
    return result


def load_documents(data_dir: Path) -> list[dict]:
    root = data_dir.resolve()
    manifest = json.loads((root / "documents.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("문서 manifest가 비어 있거나 배열이 아닙니다.")
    result, seen = [], set()
    for entry in manifest:
        doc_id = entry["doc_id"]
        if not DOC_ID.fullmatch(doc_id) or doc_id in seen:
            raise ValueError(f"잘못된/중복 문서 ID: {doc_id}")
        seen.add(doc_id)
        if entry["doc_type"] not in DOC_TYPES:
            raise ValueError(f"잘못된 문서 종류: {doc_id}")
        date.fromisoformat(entry["effective_date"])
        rel = Path(entry["source_path"])
        if not rel.parts or rel.parts[0] != "data":
            raise ValueError(f"source_path는 data/로 시작해야 합니다: {doc_id}")
        source = (root / Path(*rel.parts[1:])).resolve()
        if not source.is_relative_to(root):
            raise ValueError(f"문서 경로가 data 밖입니다: {doc_id}")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["content_hash"]:
            raise ValueError(f"content_hash 불일치: {doc_id}. 원문과 manifest를 같이 갱신하세요.")
        text = raw.decode("utf-8")
        result.append({**entry, "raw_content": text, "chunks": chunks(text)})
    return result
