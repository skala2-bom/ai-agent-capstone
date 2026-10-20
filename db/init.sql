-- DB 스키마 3개 + 계정 3개 (contracts.md 1.3)
--
-- postgres 컨테이너가 **빈 볼륨으로 처음 뜰 때 한 번만** 돈다 (/docker-entrypoint-initdb.d).
-- 고친 뒤 다시 돌리려면: docker compose down -v && docker compose up --build
--
-- 계정을 나누는 이유: 아키텍처 11장 보안성 증빙 "DB 계정 분리".
-- agent가 업무 테이블을 직접 쓰지 못하게 하는 것(교재 금지 사항)을 말이 아니라 권한으로 막는다.
--
-- ponytail: 비밀번호를 여기 고정했다. DB 포트를 밖으로 열지 않아서(compose ports 없음)
-- 내부망 전용 개발값으로만 쓴다. 외부 배포를 하게 되면 init.sh로 바꿔 .env에서 읽는다.

CREATE ROLE app   LOGIN PASSWORD 'app';
CREATE ROLE agent LOGIN PASSWORD 'agent';
CREATE ROLE rag   LOGIN PASSWORD 'rag';

-- public: 업무·작업·승인 (B 파트). PG15부터 public에 기본 CREATE가 없지만 USAGE는 남아 있어 같이 걷는다.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO app;

-- agent: LangGraph checkpointer. 스키마 소유자라 CREATE 권한이 있고, 테이블은 LangGraph가 직접 만든다.
CREATE SCHEMA agent AUTHORIZATION agent;

-- rag: doc_chunk. vector 타입을 public이 아니라 rag 스키마에 둔다.
-- public에 두면 rag 계정에 public USAGE를 열어 줘야 해서 "자기 스키마만" 원칙이 깨진다.
CREATE SCHEMA rag AUTHORIZATION rag;
CREATE EXTENSION IF NOT EXISTS vector SCHEMA rag;

-- 접속하면 자기 스키마가 기본이 되게 한다. 테이블 이름에 스키마를 안 붙여도 된다.
ALTER ROLE app   SET search_path = public;
ALTER ROLE agent SET search_path = agent;
ALTER ROLE rag   SET search_path = rag;
