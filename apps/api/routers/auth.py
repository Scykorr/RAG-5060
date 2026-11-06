from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.auth import (
    AuthUser,
    create_session_token,
    clear_session_cookie,
    get_current_user,
    hash_password,
    membership_for,
    set_session_cookie,
    verify_password,
)
from apps.api.db import get_db
from apps.api.models import AppSetting, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    login: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=4, max_length=200)


@router.post("/login")
def login(body: LoginIn, response: Response, db: Annotated[Session, Depends(get_db)]):
    user = db.query(User).filter(User.login == body.login).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    mem = membership_for(db, user.id)
    if not mem:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenant membership")
    token = create_session_token(user.id, mem.tenant_id, mem.role)
    set_session_cookie(response, token)
    return {"login": user.login, "role": mem.role, "tenant_id": str(mem.tenant_id)}


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(user: Annotated[AuthUser, Depends(get_current_user)]):
    return {"id": str(user.id), "login": user.login, "role": user.role, "tenant_id": str(user.tenant_id)}


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(User, user.id)
    if not row or not verify_password(body.current_password, row.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is wrong")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ")
    row.password_hash = hash_password(body.new_password)
    if not db.get(AppSetting, "admin_password_changed_via_ui"):
        db.add(AppSetting(key="admin_password_changed_via_ui", value="1"))
    else:
        db.get(AppSetting, "admin_password_changed_via_ui").value = "1"
    db.commit()
    return {"ok": True, "message": "Password updated"}
