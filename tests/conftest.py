"""Pytest fixtures — env for Settings before app import."""

from __future__ import annotations

import os

os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-unit-tests")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://rag:rag@localhost:5432/rag",
)

from apps.api.settings import get_settings

get_settings.cache_clear()
