"""简单管理员会话鉴权。

单账号，用户名/密码来自环境变量，常量时间比对；登录态存签名会话 Cookie。
生产建议改强口令或前置反向代理鉴权（见 docs/design.md 10.2）。
"""
from __future__ import annotations

import hmac

from fastapi import Request

from app.config import settings

SESSION_USER_KEY = "user"


class NotAuthenticated(Exception):
    """未登录标记异常，由 main 的处理器统一重定向到 /login。"""


def verify_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(username, settings.admin_username) and hmac.compare_digest(
        password, settings.admin_password
    )


def login_session(request: Request, username: str) -> None:
    request.session[SESSION_USER_KEY] = username


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_USER_KEY)


def require_user(request: Request) -> str:
    """FastAPI 依赖：未登录抛 NotAuthenticated（→ 重定向登录页）。"""
    user = current_user(request)
    if not user:
        raise NotAuthenticated()
    return user
