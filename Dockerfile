# Deployment image (HTTP transport). /mcp requires
# Authorization: Bearer $MCP_BEARER_TOKEN when that variable is set;
# set it in the host's env vars before exposing this publicly.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
ENV MCP_TRANSPORT=http MCP_PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "python server.py --transport http --port ${PORT:-8000}"]
