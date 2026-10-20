"""문서와 질의가 공유하는 임베딩 어댑터."""

import math
import os
import time

import httpx


class EmbeddingError(RuntimeError):
    pass


class Embeddings:
    def __init__(self, model: str, dimensions: int, api_key: str):
        if not 1 <= dimensions <= 3072:
            raise EmbeddingError("[RAG_CONFIG] 임베딩 차원은 1~3072여야 합니다.")
        self.model, self.dimensions, self.api_key = model, dimensions, api_key

    @classmethod
    def from_env(cls):
        try:
            dimensions = int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "1536"))
        except ValueError:
            raise EmbeddingError("[RAG_CONFIG] RAG_EMBEDDING_DIMENSIONS는 정수여야 합니다.") from None
        return cls(os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small"),
                   dimensions, os.getenv("OPENAI_API_KEY", ""))

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingError("[RAG_KEY_MISSING] 임베딩 키가 없습니다. OPENAI_API_KEY를 설정하세요.")
        # 오류 본문에는 입력이 포함될 수 있으므로 키·원문·응답 본문을 출력하지 않는다.
        for attempt in range(2):
            try:
                response = httpx.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "dimensions": self.dimensions,
                          "encoding_format": "float", "input": texts}, timeout=3)
            except httpx.HTTPError:
                if attempt == 1:
                    raise EmbeddingError("[RAG_EMBEDDING_UNAVAILABLE] 임베딩 연결 실패. 잠시 후 재시도하세요.") from None
                time.sleep(0.3 * (attempt + 1))
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            if response.is_error:
                raise EmbeddingError(f"[RAG_EMBEDDING_UNAVAILABLE] 임베딩 HTTP {response.status_code}. 키·모델 설정을 확인하세요.")
            try:
                rows = sorted(response.json()["data"], key=lambda row: row["index"])
                if [r["index"] for r in rows] != list(range(len(texts))):
                    raise ValueError
                vectors = [r["embedding"] for r in rows]
                for vector in vectors:
                    validate_vector(vector, self.dimensions)
                return vectors
            except (KeyError, TypeError, ValueError):
                raise EmbeddingError("[RAG_EMBEDDING_INVALID] 임베딩 응답 개수·차원이 맞지 않습니다.") from None
        raise EmbeddingError("[RAG_EMBEDDING_UNAVAILABLE] 임베딩 요청 실패.")


def validate_vector(vector: list[float], dimensions: int) -> None:
    if (len(vector) != dimensions or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector)
            or not any(vector)):
        raise ValueError("유효하지 않은 임베딩 벡터")


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"
