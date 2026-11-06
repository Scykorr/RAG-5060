from __future__ import annotations

from sqlalchemy.orm import Session

from apps.api.auth import hash_password
from apps.api.db import Base, SessionLocal, engine
from apps.api import models as _models  # noqa: F401
from apps.api.models import AppSetting, Tenant, TenantMembership, User
from apps.api.services.storage import data_root
from apps.api.settings import get_settings


def init_db() -> None:
    data_root()
    print(f"[bootstrap] creating tables: {sorted(Base.metadata.tables)}")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    settings = get_settings()
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.login == settings.admin_login).first()
        if not user:
            password = settings.admin_password or "admin"
            user = User(login=settings.admin_login, password_hash=hash_password(password))
            tenant = Tenant(name="default")
            db.add_all([user, tenant])
            db.flush()
            db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="admin"))
            db.add(AppSetting(key="admin_pw_bootstrapped", value="1"))
            db.commit()
            print(f"[bootstrap] admin user '{settings.admin_login}' created (default local password)")
        elif not db.get(AppSetting, "admin_password_changed_via_ui"):
            # Keep env default until the operator changes password in UI.
            user.password_hash = hash_password(settings.admin_password or "admin")
            if not db.get(AppSetting, "admin_pw_bootstrapped"):
                db.add(AppSetting(key="admin_pw_bootstrapped", value="1"))
            db.commit()
            print(f"[bootstrap] admin password synced from ADMIN_PASSWORD (UI change not set)")
        if not db.get(AppSetting, "active_model"):
            db.add(AppSetting(key="active_model", value=settings.ollama_model))
            db.commit()
    finally:
        db.close()
