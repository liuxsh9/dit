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
