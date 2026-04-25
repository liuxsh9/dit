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
