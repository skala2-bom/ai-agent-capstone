FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY sql ./sql
# mcp.run(transport="http", ...)로 :9102/mcp에서 듣는다 (server.py 맨 아래)
CMD ["python", "server.py"]
