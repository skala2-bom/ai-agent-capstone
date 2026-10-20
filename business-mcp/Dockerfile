FROM python:3.12-slim
RUN pip install --no-cache-dir fastmcp==4.1.1 httpx
COPY server.py .
# mcp.run(transport="http", ...)로 :9101/mcp에서 듣는다 (server.py 맨 아래)
CMD ["python", "server.py"]
