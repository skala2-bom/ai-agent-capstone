FROM python:3.12-slim
# ponytail: requirements.txt 없이 여기서 바로 설치한다. 버전은 고정해서
#           팀원마다 다른 버전이 깔리는 일을 막는다. 의존성이 늘면 파일로 뺄 것.
# langgraph-checkpoint-postgres·psycopg: 승인 대기 상태를 PostgreSQL(agent 스키마)에 저장 (contracts 1.3)
RUN pip install --no-cache-dir fastmcp==4.1.1 httpx fastapi uvicorn langgraph==1.2.16 langchain-openai==1.7.2 \
    langgraph-checkpoint-postgres==3.2.0 "psycopg[binary]==3.3.6" psycopg-pool==3.3.3
COPY *.py ./
COPY prompts ./prompts
COPY sql ./sql
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
