from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from apps.api.auth import hash_password, verify_password
from apps.api.db import Base, SessionLocal, engine
from apps.api import models as _models  # noqa: F401
from apps.api.models import AppSetting, Tenant, TenantMembership, User
from apps.api.services.storage import data_root
from apps.api.settings import get_settings


def _wait_for_db(*, attempts: int = 40, delay_sec: float = 1.5) -> None:
    """Retry DB connect — compose restart races leave Postgres 'starting up' briefly."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            if i > 1:
                print(f"[bootstrap] database ready after {i} attempt(s)")
            return
        except OperationalError as exc:
            last = exc
            print(f"[bootstrap] database not ready ({i}/{attempts}): {exc.__class__.__name__}")
            time.sleep(delay_sec)
    raise RuntimeError(f"database not ready after {attempts} attempts") from last


def init_db() -> None:
    data_root()
    _wait_for_db()
    print(f"[bootstrap] creating tables: {sorted(Base.metadata.tables)}")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    settings = get_settings()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.login == settings.admin_login).first()
        env_password = settings.admin_password or "admin"
        if not user:
            user = User(login=settings.admin_login, password_hash=hash_password(env_password))
            tenant = Tenant(name="default")
            db.add_all([user, tenant])
            db.flush()
            db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="admin"))
            db.add(AppSetting(key="admin_pw_bootstrapped", value="1"))
            db.commit()
            print(f"[bootstrap] admin user '{settings.admin_login}' created (default local password)")
        elif not db.get(AppSetting, "admin_password_changed_via_ui"):
            # Keep ADMIN_PASSWORD as source of truth until UI change; rehash only on drift.
            if not verify_password(env_password, user.password_hash):
                user.password_hash = hash_password(env_password)
                if not db.get(AppSetting, "admin_pw_bootstrapped"):
                    db.add(AppSetting(key="admin_pw_bootstrapped", value="1"))
                db.commit()
                print("[bootstrap] admin password re-synced from ADMIN_PASSWORD (drift fixed)")
            elif not db.get(AppSetting, "admin_pw_bootstrapped"):
                db.add(AppSetting(key="admin_pw_bootstrapped", value="1"))
                db.commit()
                print("[bootstrap] admin password matches ADMIN_PASSWORD")
            else:
                print("[bootstrap] admin password unchanged (skip bcrypt rehash)")
        if not db.get(AppSetting, "active_model"):
            db.add(AppSetting(key="active_model", value=settings.ollama_model))
            db.commit()
    finally:
        db.close()
