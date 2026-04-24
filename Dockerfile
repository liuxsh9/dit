FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir ".[server]"

RUN mkdir -p /data/objects

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --retries=3 --start-period=10s \
  CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "dit.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
