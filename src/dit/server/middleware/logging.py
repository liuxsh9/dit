"""Structured request logging middleware for datahub-core."""
from __future__ import annotations

import json
import logging
import os
import time

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("datahub.access")

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
