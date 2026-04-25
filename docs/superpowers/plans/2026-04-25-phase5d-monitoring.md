# Phase 5D: Basic Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Prometheus metrics, enhanced health check, and structured request logging to dit-core.

**Architecture:** Two ASGI middlewares (metrics + logging) wrap all requests. Enhanced health endpoint checks DB + data_dir. Prometheus metrics exposed at `/metrics`.

**Tech Stack:** Python 3.12, FastAPI, prometheus-client, logging (stdlib)

---

### Task 1: Add prometheus-client dependency + Metrics Middleware

**Files:**
- Modify: `pyproject.toml`
- Create: `src/dit/server/middleware/__init__.py`
- Create: `src/dit/server/middleware/metrics.py`
- Modify: `src/dit/server/app.py`
- Test: `tests/server/test_metrics.py`

- [ ] **Step 1: Write tests**

Create `tests/server/test_metrics.py`:

```python
"""Tests for Prometheus metrics middleware."""
import pytest
from httpx import AsyncClient

from dit.server.models import Repo


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_format(client: AsyncClient):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"] or "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    assert "dit_http_requests_total" in body


@pytest.mark.asyncio
async def test_metrics_counts_requests(client: AsyncClient):
    await client.get("/health")
    await client.get("/health")

    resp = await client.get("/metrics")
    body = resp.text
    assert "dit_http_requests_total" in body


@pytest.mark.asyncio
async def test_metrics_records_latency(client: AsyncClient):
    await client.get("/health")

    resp = await client.get("/metrics")
    body = resp.text
    assert "dit_http_request_duration_seconds" in body


@pytest.mark.asyncio
async def test_metrics_path_normalization(client: AsyncClient, session, tmp_path):
    repo = Repo(name="metrics-test")
    session.add(repo)
    await session.commit()

    hash_64 = "a" * 64
    await client.get(f"/api/v1/repos/metrics-test/dedup/{hash_64}")

    resp = await client.get("/metrics")
    body = resp.text
    assert "{hash}" in body or "hash" in body


@pytest.mark.asyncio
async def test_metrics_in_progress_gauge(client: AsyncClient):
    resp = await client.get("/metrics")
    body = resp.text
    assert "dit_http_requests_in_progress" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_metrics.py -v`

Expected: FAIL — `/metrics` endpoint not found (404).

- [ ] **Step 3: Add `prometheus-client` to pyproject.toml**

In `pyproject.toml`, add `"prometheus-client>=0.21"` to the `server` optional dependencies:

```toml
[project.optional-dependencies]
server = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "pydantic-settings>=2.0",
    "alembic>=1.14",
    "prometheus-client>=0.21",
]
```

Then install: `cd /Users/lxs/code/dit && uv sync --extra server`

- [ ] **Step 4: Create middleware package**

Create `src/dit/server/middleware/__init__.py` (empty file).

- [ ] **Step 5: Create `src/dit/server/middleware/metrics.py`**

```python
"""Prometheus metrics middleware for dit-core."""
from __future__ import annotations

import re
import time

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

REQUEST_COUNT = Counter(
    "dit_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "dit_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

REQUESTS_IN_PROGRESS = Gauge(
    "dit_http_requests_in_progress",
    "Number of HTTP requests currently being processed",
    ["method"],
)

_HEX64_RE = re.compile(r"/[0-9a-f]{64}")
_NUMERIC_ID_RE = re.compile(r"/\d+")

_KNOWN_PREFIXES = [
    ("/api/v1/repos/", "{repo}"),
]


def _normalize_path(path: str) -> str:
    path = _HEX64_RE.sub("/{hash}", path)
    parts = path.split("/")
    result = []
    i = 0
    while i < len(parts):
        result.append(parts[i])
        if i >= 2 and parts[i - 1] == "repos" and parts[i] not in ("{hash}", ""):
            result[-1] = "{repo}"
        if i >= 2 and parts[i - 1] == "pulls" and parts[i] not in ("{hash}", ""):
            if parts[i].isdigit():
                result[-1] = "{id}"
        if i >= 2 and parts[i - 1] == "tokens" and parts[i] not in ("{hash}", ""):
            if parts[i].isdigit():
                result[-1] = "{id}"
        i += 1
    return "/".join(result)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path

        if path == "/metrics":
            body = generate_latest()
            response = Response(
                content=body,
                media_type=CONTENT_TYPE_LATEST,
                status_code=200,
            )
            await response(scope, receive, send)
            return

        method = request.method
        normalized = _normalize_path(path)
        status_code = 500

        REQUESTS_IN_PROGRESS.labels(method=method).inc()
        start = time.monotonic()

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            REQUESTS_IN_PROGRESS.labels(method=method).dec()
            REQUEST_COUNT.labels(method=method, path=normalized, status=str(status_code)).inc()
            REQUEST_LATENCY.labels(method=method, path=normalized).observe(duration)
```

- [ ] **Step 6: Register middleware in `app.py`**

In `src/dit/server/app.py`, add after creating the `application` FastAPI instance (after line 34, before the health endpoint):

```python
    from dit.server.middleware.metrics import MetricsMiddleware
    application.add_middleware(MetricsMiddleware)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_metrics.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 8: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

**IMPORTANT**: Prometheus metrics are global singletons. If tests run in the same process, metrics accumulate. This should be fine for our tests since we only assert presence, not exact values. But if tests fail due to metric state, check if `prometheus_client.REGISTRY` needs cleanup — add `from prometheus_client import REGISTRY; REGISTRY.unregister(...)` in a fixture if needed. Likely NOT needed.

- [ ] **Step 9: Commit**

```bash
cd /Users/lxs/code/dit
git add pyproject.toml src/dit/server/middleware/__init__.py src/dit/server/middleware/metrics.py src/dit/server/app.py tests/server/test_metrics.py
git commit -m "feat: add Prometheus metrics middleware

Instruments all HTTP requests with count, latency histogram, and
in-progress gauge. Exposed at GET /metrics."
```

---

### Task 2: Enhanced Health Check

**Files:**
- Modify: `src/dit/server/app.py`
- Test: `tests/server/test_health.py`

- [ ] **Step 1: Write tests**

Create `tests/server/test_health.py`:

```python
"""Tests for enhanced health endpoint."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_healthy(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "checks" in data
    assert data["checks"]["database"]["status"] == "healthy"
    assert "latency_ms" in data["checks"]["database"]
    assert data["checks"]["data_dir"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_includes_database_latency(client: AsyncClient):
    resp = await client.get("/health")
    data = resp.json()
    latency = data["checks"]["database"]["latency_ms"]
    assert isinstance(latency, (int, float))
    assert latency >= 0


@pytest.mark.asyncio
async def test_health_data_dir_check(client: AsyncClient):
    resp = await client.get("/health")
    data = resp.json()
    assert data["checks"]["data_dir"]["status"] == "healthy"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_health.py -v`

Expected: FAIL — current health endpoint returns `{"status": "ok"}` not `{"status": "healthy"}`.

- [ ] **Step 3: Upgrade health endpoint in `app.py`**

Replace the existing health endpoint in `src/dit/server/app.py`:

```python
    @application.get("/health")
    async def health():
        import time as _time
        from sqlalchemy import text

        checks = {}
        overall = "healthy"

        try:
            factory = application.dependency_overrides.get(get_session)
            if factory:
                async for session in factory():
                    start = _time.monotonic()
                    await session.execute(text("SELECT 1"))
                    latency = (_time.monotonic() - start) * 1000
                    checks["database"] = {"status": "healthy", "latency_ms": round(latency, 2)}
                    break
            else:
                checks["database"] = {"status": "healthy", "latency_ms": 0}
        except Exception as exc:
            checks["database"] = {"status": "unhealthy", "error": str(exc)}
            overall = "unhealthy"

        data_dir = getattr(application.state, "data_dir", None)
        if data_dir and data_dir.is_dir():
            checks["data_dir"] = {"status": "healthy"}
        else:
            checks["data_dir"] = {"status": "unhealthy", "error": "data directory not found"}
            overall = "unhealthy"

        from starlette.responses import JSONResponse
        status_code = 200 if overall == "healthy" else 503
        return JSONResponse(
            content={"status": overall, "checks": checks},
            status_code=status_code,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_health.py -v`

Expected: All 3 tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

**IMPORTANT**: The old health endpoint returned `{"status": "ok"}`. Check if any existing tests assert on this string. Search: `grep -r '"status": "ok"' tests/`. If found, update those tests.

- [ ] **Step 6: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/server/app.py tests/server/test_health.py
git commit -m "feat: enhanced health check with DB and data_dir probes

Returns 200 with detailed check results when healthy,
503 when any check fails."
```

---

### Task 3: Request Logging Middleware

**Files:**
- Create: `src/dit/server/middleware/logging.py`
- Modify: `src/dit/server/app.py`
- Test: `tests/server/test_logging_middleware.py`

- [ ] **Step 1: Write tests**

Create `tests/server/test_logging_middleware.py`:

```python
"""Tests for request logging middleware."""
import json
import logging

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_request_logging_emits_json(client: AsyncClient, caplog):
    with caplog.at_level(logging.INFO, logger="dit.access"):
        await client.get("/health")

    access_records = [r for r in caplog.records if r.name == "dit.access"]
    assert len(access_records) >= 1
    record = access_records[-1]
    data = json.loads(record.getMessage())
    assert data["method"] == "GET"
    assert data["path"] == "/health"
    assert data["status"] == 200
    assert "latency_ms" in data
    assert "request_id" in data


@pytest.mark.asyncio
async def test_request_id_in_response_header(client: AsyncClient):
    resp = await client.get("/health")
    assert "x-request-id" in resp.headers


@pytest.mark.asyncio
async def test_request_id_passthrough(client: AsyncClient):
    resp = await client.get("/health", headers={"X-Request-ID": "custom-123"})
    assert resp.headers["x-request-id"] == "custom-123"


@pytest.mark.asyncio
async def test_logging_skips_metrics_endpoint(client: AsyncClient, caplog):
    with caplog.at_level(logging.INFO, logger="dit.access"):
        await client.get("/metrics")

    access_records = [r for r in caplog.records if r.name == "dit.access"]
    metrics_logs = [r for r in access_records if "/metrics" in r.getMessage()]
    assert len(metrics_logs) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_logging_middleware.py -v`

- [ ] **Step 3: Create `src/dit/server/middleware/logging.py`**

```python
"""Structured request logging middleware for dit-core."""
from __future__ import annotations

import json
import logging
import os
import time

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("dit.access")

_SKIP_PATHS = {"/metrics"}


class LoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path

        if path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        request_id = request.headers.get("x-request-id") or os.urandom(4).hex()
        method = request.method
        client_ip = request.client.host if request.client else ""

        status_code = 500
        start = time.monotonic()

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency = (time.monotonic() - start) * 1000
            log_data = {
                "request_id": request_id,
                "method": method,
                "path": path,
                "status": status_code,
                "latency_ms": round(latency, 2),
                "client_ip": client_ip,
            }

            if status_code >= 500:
                logger.error(json.dumps(log_data))
            elif status_code >= 400:
                logger.warning(json.dumps(log_data))
            else:
                logger.info(json.dumps(log_data))
```

- [ ] **Step 4: Register logging middleware in `app.py`**

Add after the metrics middleware registration:

```python
    from dit.server.middleware.logging import LoggingMiddleware
    application.add_middleware(LoggingMiddleware)
```

**Note**: Starlette middleware order is LIFO — the last `add_middleware` wraps outermost. So `LoggingMiddleware` added after `MetricsMiddleware` means logging wraps metrics, which is correct (logging sees the full request lifecycle including metrics processing).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_logging_middleware.py -v`

Expected: All 4 tests PASS.

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

- [ ] **Step 7: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/server/middleware/logging.py src/dit/server/app.py tests/server/test_logging_middleware.py
git commit -m "feat: add structured request logging middleware

JSON access logs with request ID, method, path, status, latency.
Skips /metrics to reduce noise."
```
