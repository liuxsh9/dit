"""Tests for Prometheus metrics middleware."""
import pytest
from httpx import AsyncClient

from dit.server.models import Repo


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_format(client: AsyncClient):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    body = resp.text
    assert "datahub_http_requests_total" in body


@pytest.mark.asyncio
async def test_metrics_counts_requests(client: AsyncClient):
    await client.get("/health")
    await client.get("/health")

    resp = await client.get("/metrics")
    body = resp.text
    assert "datahub_http_requests_total" in body


@pytest.mark.asyncio
async def test_metrics_records_latency(client: AsyncClient):
    await client.get("/health")

    resp = await client.get("/metrics")
    body = resp.text
    assert "datahub_http_request_duration_seconds" in body


@pytest.mark.asyncio
async def test_metrics_path_normalization(client: AsyncClient, session, tmp_path):
    repo = Repo(name="metrics-test")
    session.add(repo)
    await session.commit()

    hash_64 = "a" * 64
    await client.get(f"/api/v1/repos/metrics-test/dedup/{hash_64}")

    resp = await client.get("/metrics")
    body = resp.text
    assert "{hash}" in body


@pytest.mark.asyncio
async def test_metrics_in_progress_gauge(client: AsyncClient):
    resp = await client.get("/metrics")
    body = resp.text
    assert "datahub_http_requests_in_progress" in body
