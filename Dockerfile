# Warehouse Ops MCP: remote (HTTP) server image
FROM python:3.12-slim

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src
COPY data ./data
RUN uv sync --no-dev

# Cloud hosts set PORT; default to 8000 locally
ENV PORT=8000
EXPOSE 8000

# Seed fresh synthetic data at startup (timestamps anchor to "now"), then serve over HTTP.
# Writes are OFF by default in HTTP mode; set WAREHOUSE_ALLOW_WRITES=true to enable.
CMD ["sh", "-c", "uv run python data/seed.py && uv run warehouse-ops-mcp --transport http --host 0.0.0.0 --port ${PORT}"]
