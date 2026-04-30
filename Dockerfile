# Stage 1: Builder — install dependencies with uv using the lockfile
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev --extra server

# Stage 2: Runtime — minimal image with non-root user
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 1000 --create-home dit

COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src/
COPY scripts/docker-entrypoint.sh /usr/local/bin/dit-docker-entrypoint
RUN chmod +x /usr/local/bin/dit-docker-entrypoint

ENV PATH="/app/.venv/bin:$PATH" \
    DIT_SERVER_HOST=0.0.0.0 \
    DIT_SERVER_PORT=8000 \
    DIT_SERVER_WORKERS=2 \
    DIT_SERVER_DATA_DIR=/data/dit \
    DIT_SERVER_AUTO_MIGRATE=1

RUN mkdir -p /data/dit && chown dit:dit /data/dit

USER dit

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --retries=3 --start-period=10s \
  CMD sh -c 'curl -f "http://localhost:${DIT_SERVER_PORT:-8000}/health" || exit 1'

ENTRYPOINT ["dit-docker-entrypoint"]
CMD ["sh", "-c", "exec gunicorn dit.server.app:app -k uvicorn.workers.UvicornWorker --bind \"${DIT_SERVER_HOST:-0.0.0.0}:${DIT_SERVER_PORT:-8000}\" --workers \"${DIT_SERVER_WORKERS:-2}\" --timeout 120 --access-logfile -"]
