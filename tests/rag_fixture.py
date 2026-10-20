"""테스트 전용 결정적 벡터. 검색 품질을 평가하는 모델이 아니다. 운영 서버는 이 모듈을 import하지 않는다."""
import hashlib
import re


class FixtureEmbeddings:
    model = "test-character-bigrams"
    dimensions = 128

    def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            text = re.sub(r"\s+", "", text.lower())
            for i in range(max(1, len(text) - 1)):
                token = text[i:i + 2].encode()
                vector[int.from_bytes(hashlib.sha256(token).digest()[:4], "big") % self.dimensions] += 1
            vectors.append(vector)
        return vectors
