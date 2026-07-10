"""操作日志中间件（M6）：记录所有写操作(POST/PUT/DELETE)——谁、何时、做了什么、结果。

只记录方法/路径/用户/状态，不记录请求体（避免把密钥/密码写进日志）。
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_WRITE = {"POST", "PUT", "DELETE", "PATCH"}


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.method in _WRITE:
            try:
                from app.database import SessionLocal
                from app.models import AuditLog

                user = None
                try:
                    user = request.session.get("user")
                except Exception:  # noqa: BLE001 session 不可用时忽略
                    pass
                db = SessionLocal()
                db.add(
                    AuditLog(
                        username=user,
                        method=request.method,
                        path=str(request.url.path)[:255],
                        status=response.status_code,
                    )
                )
                db.commit()
                db.close()
            except Exception:  # noqa: BLE001 审计失败绝不影响主流程
                logger.debug("审计记录失败", exc_info=True)
        return response
