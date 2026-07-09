"""用户管理（M4，仅 admin）。创建 / 改角色 / 重置密码 / 启停 / 删除。

安全约束：不能停用/删除自己，也不能移除最后一个 admin（防锁死）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user, require_admin
from app.database import get_db
from app.models import User
from app.security import hash_password
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_admin)])

ROLES = ("admin", "viewer")


def _admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count(User.id)).where(User.role == "admin", User.enabled.is_(True))
    ) or 0


@router.get("/users")
def list_users(request: Request, db: Session = Depends(get_db)):
    users = list(db.scalars(select(User).order_by(User.id)))
    return templates.TemplateResponse(
        request,
        "users.html",
        {"users": users, "roles": ROLES, "me": current_user(request),
         "error": request.query_params.get("error")},
    )


@router.post("/users")
def create_user(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("viewer"),
    db: Session = Depends(get_db),
):
    username = username.strip()
    if not username or not password or role not in ROLES:
        return RedirectResponse("/users?error=invalid", status_code=303)
    if db.scalar(select(User).where(User.username == username)):
        return RedirectResponse("/users?error=exists", status_code=303)
    pw_hash, salt = hash_password(password)
    db.add(User(username=username, password_hash=pw_hash, salt=salt, role=role, enabled=True))
    db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{uid}/role")
def set_role(uid: int, role: str = Form(...), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if u and role in ROLES:
        # 不能把最后一个 admin 降级
        if u.role == "admin" and role != "admin" and _admin_count(db) <= 1:
            return RedirectResponse("/users?error=last_admin", status_code=303)
        u.role = role
        db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{uid}/reset")
def reset_password(uid: int, password: str = Form(...), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if u and password:
        u.password_hash, u.salt = hash_password(password)
        db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{uid}/toggle")
def toggle_user(request: Request, uid: int, db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if u:
        if u.username == current_user(request):
            return RedirectResponse("/users?error=self", status_code=303)
        if u.enabled and u.role == "admin" and _admin_count(db) <= 1:
            return RedirectResponse("/users?error=last_admin", status_code=303)
        u.enabled = not u.enabled
        db.commit()
    return RedirectResponse("/users", status_code=303)


@router.post("/users/{uid}/delete")
def delete_user(request: Request, uid: int, db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if u:
        if u.username == current_user(request):
            return RedirectResponse("/users?error=self", status_code=303)
        if u.role == "admin" and _admin_count(db) <= 1:
            return RedirectResponse("/users?error=last_admin", status_code=303)
        db.delete(u)
        db.commit()
    return RedirectResponse("/users", status_code=303)
