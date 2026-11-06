import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
