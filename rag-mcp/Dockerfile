FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY sql ./sql
# docker healthcheck. slim 이미지에 curl이 없어서 python으로 부른다
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9102/health', timeout=2)" || exit 1
# mcp.run(transport="http", ...)로 :9102/mcp에서 듣는다 (server.py 맨 아래)
CMD ["python", "server.py"]
