FROM python:3.12-slim
# ponytail: requirements.txt 없이 여기서 바로 설치한다. 버전은 고정해서
#           팀원마다 다른 버전이 깔리는 일을 막는다. 의존성이 늘면 파일로 뺄 것.
RUN pip install --no-cache-dir fastmcp==4.1.1 httpx fastapi uvicorn langgraph==1.2.16 langchain-openai==1.7.2
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
