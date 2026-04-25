# Phase 5D: Basic Monitoring

> **Parent:** Phase 5 (Operations & Observability)  
> **Date:** 2026-04-25  
> **Depends on:** Phase 1–4 (server, object store, database)  
> **Blocks:** None

---

## Overview

Add production observability to dit-core: Prometheus-compatible metrics, an enhanced health endpoint, and structured request logging. This enables dashboarding, alerting, and troubleshooting in production deployments.

Three components:

1. **Prometheus metrics** — request count, latency histogram, and active request gauge via `prometheus-client` library, exposed at `GET /metrics`.
2. **Enhanced health check** — `GET /health` upgraded to include database connectivity and data directory accessibility checks.
3. **Request logging middleware** — structured JSON logging with request ID, method, path, status code, and latency for every request.

---

## 1. Prometheus Metrics Middleware

### 1.1 New file: `src/dit/server/middleware/metrics.py`

ASGI middleware that instruments every request with three Prometheus metrics:

```python
from prometheus_client import Counter, Histogram, Gauge

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
```

### 1.2 Path normalization

Raw paths like `/api/v1/repos/my-repo/dedup/abc123` have high cardinality. Normalize to route templates before labeling. Strategy:

- Replace 64-char hex strings with `{hash}` (commit hashes, object hashes)
- Replace path segments after known prefixes: `/repos/{repo}/`, `/pulls/{id}/`, `/tokens/{id}/`
- Keep the HTTP method and normalized path as label values

Implementation: a `_normalize_path(path: str) -> str` function with regex substitution.

### 1.3 Metrics endpoint

```
GET /metrics
```

Returns Prometheus text exposition format. No authentication (standard for metrics scraping). Uses `prometheus_client.generate_latest()`.

---

## 2. Enhanced Health Check

### 2.1 Upgrade `GET /health`

Current: returns `{"status": "ok"}` unconditionally.

New: performs actual checks and returns detailed status:

```json
{
  "status": "healthy",
  "checks": {
    "database": {"status": "healthy", "latency_ms": 2.3},
    "data_dir": {"status": "healthy"}
  }
}
```

If any check fails:

```json
{
  "status": "unhealthy",
  "checks": {
    "database": {"status": "unhealthy", "error": "connection refused"},
    "data_dir": {"status": "healthy"}
  }
}
```

HTTP status: 200 if healthy, 503 if unhealthy.

### 2.2 Checks

1. **Database**: Execute `SELECT 1` via the session factory. Measure latency. Timeout: 5 seconds.
2. **Data directory**: Verify `app.state.data_dir` exists and is a directory.

### 2.3 Backward compatibility

Docker health checks currently hit `GET /health` and expect 200. The new endpoint returns 200 when healthy, so no Docker change needed. If the database is down, 503 is correct — Docker should mark the container unhealthy.

---

## 3. Request Logging Middleware

### 3.1 New file: `src/dit/server/middleware/logging.py`

ASGI middleware that logs every request as structured JSON to stdout:

```json
{
  "timestamp": "2026-04-25T10:30:00.123Z",
  "request_id": "a1b2c3d4",
  "method": "GET",
  "path": "/api/v1/repos/my-repo/stats/abc123",
  "status": 200,
  "latency_ms": 45.2,
  "client_ip": "10.0.0.1"
}
```

### 3.2 Request ID

Generate a short random hex string (8 chars) per request. Set as `X-Request-ID` response header. If the incoming request already has `X-Request-ID`, use it.

### 3.3 Logger configuration

Use Python's `logging` module with a JSON formatter. Configure in `app.py` lifespan. Log level: INFO for normal requests, WARNING for 4xx, ERROR for 5xx.

### 3.4 Excluded paths

Skip logging for:
- `GET /health` (too noisy from Docker health checks)
- `GET /metrics` (too noisy from Prometheus scraping)

---

## 4. Integration

### 4.1 Middleware registration in `app.py`

Add both middlewares in `create_app()`:

```python
from dit.server.middleware.metrics import MetricsMiddleware
from dit.server.middleware.logging import LoggingMiddleware

application.add_middleware(MetricsMiddleware)
application.add_middleware(LoggingMiddleware)
```

### 4.2 Dependency

Add `prometheus-client` to `pyproject.toml` server extras.

### 4.3 Gateway proxy

No gateway proxy needed. The `/metrics` endpoint is for Prometheus scraping directly from dit-core. The gateway has its own metrics infrastructure (Forgejo/Prometheus built-in).

---

## 5. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Metrics library | prometheus-client | De facto standard, zero-config, works with any Prometheus-compatible stack |
| Metrics transport | /metrics endpoint (pull) | Standard Prometheus pull model, no pushgateway needed |
| Path normalization | Regex-based | Simple, no router introspection needed |
| Health check depth | DB + data_dir | Two failure modes that matter. Avoid slow checks (no full store walk) |
| Health check response | 200/503 | Standard HTTP semantics. Docker/k8s interpret correctly |
| Logging format | Structured JSON | Machine-parseable, works with ELK/Loki/CloudWatch |
| Request ID | 8-char hex | Short enough for logs, unique enough for correlation |
| Metrics auth | None | Standard practice — metrics endpoint is behind network boundary |
| Excluded log paths | /health, /metrics | Reduce noise from infrastructure probes |

---

## 6. Out of Scope

- **Distributed tracing** (OpenTelemetry, Jaeger): overkill for single-service deployment.
- **Custom business metrics** (PR merge rate, GC frequency): can be derived from request metrics.
- **Alerting rules**: deployment-specific, not part of the application.
- **Dashboard templates**: Grafana JSON not shipped with the app.
- **Log aggregation setup**: ELK/Loki is infrastructure, not application code.
- **Gateway monitoring**: Forgejo has its own Prometheus integration.
