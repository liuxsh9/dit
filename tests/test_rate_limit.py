import pytest
from fastapi.testclient import TestClient

from dit.server.app import create_app
from dit.server.config import ServerSettings


@pytest.fixture
def app_with_rate_limit():
    settings = ServerSettings(
        database_url="sqlite+aiosqlite://",
        rate_limit="5/minute",
    )
    return create_app(settings)


@pytest.fixture
def client(app_with_rate_limit):
    return TestClient(app_with_rate_limit)


class TestRateLimit:
    def test_requests_within_limit_succeed(self, client):
        for _ in range(5):
            resp = client.get("/health")
            # health may return 503 (no real DB), but must not be 429
            assert resp.status_code != 429

    def test_requests_exceeding_limit_return_429(self, client):
        for _ in range(5):
            client.get("/health")
        resp = client.get("/health")
        assert resp.status_code == 429

    def test_rate_limit_disabled_when_empty(self):
        settings = ServerSettings(
            database_url="sqlite+aiosqlite://",
            rate_limit="",
        )
        app = create_app(settings)
        c = TestClient(app)
        for _ in range(20):
            resp = c.get("/health")
            # health may return 503 (no real DB), but must not be 429
            assert resp.status_code != 429
