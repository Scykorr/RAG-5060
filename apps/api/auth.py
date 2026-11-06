from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from apps.api.db import get_db
from apps.api.models import TenantMembership, User
from apps.api.settings import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
COOKIE = "rag5060_session"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="rag5060-session")


def create_session_token(user_id: UUID, tenant_id: UUID, role: str) -> str:
    return _serializer().dumps({"uid": str(user_id), "tid": str(tenant_id), "role": role})


def read_session_token(token: str) -> dict:
    return _serializer().loads(token, max_age=60 * 60 * 12)


@dataclass
class AuthUser:
    id: UUID
    login: str
    tenant_id: UUID
    role: str


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> AuthUser:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        data = read_session_token(token)
    except BadSignature as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
    user = db.get(User, UUID(data["uid"]))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    return AuthUser(id=user.id, login=user.login, tenant_id=UUID(data["tid"]), role=data["role"])


def require_admin(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def membership_for(db: Session, user_id: UUID) -> TenantMembership | None:
    return (
        db.query(TenantMembership)
        .filter(TenantMembership.user_id == user_id)
        .order_by(TenantMembership.role.asc())
        .first()
    )


def new_csrf() -> str:
    return secrets.token_urlsafe(24)
