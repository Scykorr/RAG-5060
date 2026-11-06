"""Auth login route tests."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.models import TenantMembership, User


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("apps.api.main.init_db", lambda: None)
    with TestClient(app) as test_client:
        yield test_client


def test_login_invalid_credentials(client: TestClient):
    with patch("apps.api.routers.auth.verify_password", return_value=False):
        r = client.post("/api/auth/login", json={"login": "admin", "password": "wrong"})
    assert r.status_code == 401


def test_login_success_sets_cookie(client: TestClient):
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user = User(id=user_id, login="admin", password_hash="x", is_active=True)
    mem = TenantMembership(id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, role="admin")

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.side_effect = [user, mem]

    def override_get_db():
        yield mock_db

    from apps.api.db import get_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("apps.api.routers.auth.verify_password", return_value=True):
            r = client.post("/api/auth/login", json={"login": "admin", "password": "secret"})
        assert r.status_code == 200
        assert r.json()["login"] == "admin"
        assert "rag5060_session" in r.cookies
    finally:
        app.dependency_overrides.clear()
