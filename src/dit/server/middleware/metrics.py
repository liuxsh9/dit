"""Prometheus metrics middleware for dit-core."""
from __future__ import annotations

import re
import time

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST

_HEX64_RE = re.compile(r"/[0-9a-f]{64}")


def _normalize_path(path: str) -> str:
    path = _HEX64_RE.sub("/{hash}", path)
    parts = path.split("/")
    result = []
    for i, part in enumerate(parts):
        if i >= 1 and i < len(parts) and result and result[-1] == "repos" and part not in ("{hash}", ""):
            result.append("{repo}")
        elif i >= 1 and result and result[-1] == "pulls" and part.isdigit():
            result.append("{id}")
        elif i >= 1 and result and result[-1] == "tokens" and part.isdigit():
            result.append("{id}")
        else:
            result.append(part)
    return "/".join(result)


def create_metrics(registry: CollectorRegistry):
    request_count = Counter(
        "dit_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
        registry=registry,
    )
    request_latency = Histogram(
        "dit_http_request_duration_seconds",
        "HTTP request latency in seconds",
        ["method", "path"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
        registry=registry,
    )
    requests_in_progress = Gauge(
        "dit_http_requests_in_progress",
        "Number of HTTP requests currently being processed",
        ["method"],
        registry=registry,
    )
    return request_count, request_latency, requests_in_progress


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.registry = CollectorRegistry()
        self.request_count, self.request_latency, self.requests_in_progress = create_metrics(self.registry)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path

        if path == "/metrics":
            body = generate_latest(self.registry)
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

        self.requests_in_progress.labels(method=method).inc()
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
            self.requests_in_progress.labels(method=method).dec()
            self.request_count.labels(method=method, path=normalized, status=str(status_code)).inc()
            self.request_latency.labels(method=method, path=normalized).observe(duration)
