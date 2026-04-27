FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/
COPY scripts/docker-entrypoint.sh /usr/local/bin/dit-docker-entrypoint

RUN pip install --no-cache-dir ".[server]"
RUN chmod +x /usr/local/bin/dit-docker-entrypoint

ENV DIT_SERVER_HOST=0.0.0.0 \
    DIT_SERVER_PORT=8000 \
    DIT_SERVER_DATA_DIR=/data/dit \
    DIT_SERVER_AUTO_MIGRATE=1

RUN mkdir -p /data/dit

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --retries=3 --start-period=10s \
  CMD sh -c 'curl -f "http://localhost:${DIT_SERVER_PORT:-8000}/health" || exit 1'

ENTRYPOINT ["dit-docker-entrypoint"]
CMD ["sh", "-c", "exec uvicorn dit.server.app:app --host \"${DIT_SERVER_HOST:-0.0.0.0}\" --port \"${DIT_SERVER_PORT:-8000}\""]
