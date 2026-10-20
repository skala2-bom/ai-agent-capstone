FROM python:3.12-slim
RUN pip install --no-cache-dir fastmcp==4.1.1 httpx fastapi uvicorn langgraph==1.2.16 langchain-openai==1.7.2
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
