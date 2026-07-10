"""会话鉴权（M4：多用户 + 角色）。

用户存 DB（见 models.User），密码 pbkdf2 哈希。首次启动由 bootstrap 用环境变量
ADMIN_USERNAME/ADMIN_PASSWORD 播种一个 admin 用户。角色：admin=全权，viewer=只读。
登录态存签名会话 Cookie（用户名 + 角色）。
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.security import verify_password

SESSION_USER_KEY = "user"
SESSION_ROLE_KEY = "role"


class NotAuthenticated(Exception):
    """未登录：由 main 的处理器重定向到 /login。"""


class NotAuthorized(Exception):
    """已登录但权限不足（非 admin 访问写操作）：返回 403。"""


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username, User.enabled.is_(True)))
    if user and verify_password(password, user.password_hash, user.salt):
        return user
    return None


def login_session(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = user.username
    request.session[SESSION_ROLE_KEY] = user.role


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)
    request.session.pop(SESSION_ROLE_KEY, None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def current_role(request: Request) -> str | None:
    return request.session.get(SESSION_ROLE_KEY)


def require_user(request: Request) -> str:
    """依赖：需登录（任意角色）。"""
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    return user


def require_admin(request: Request) -> str:
    """依赖：需 admin 角色（管理类写操作）。"""
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    if current_role(request) != "admin":
        raise NotAuthorized()
    return user


def require_reviewer(request: Request) -> str:
    """依赖：需 admin 或 reviewer 角色（复核类操作：标记/改标准答案）。"""
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    if current_role(request) not in ("admin", "reviewer"):
        raise NotAuthorized()
    return user
