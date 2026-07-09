"""FastAPI 应用装配：会话中间件、路由、启动建表、未登录重定向。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app import models  # noqa: F401  确保 ORM 模型注册到 Base.metadata
from app.auth import NotAuthenticated
from app.bootstrap import ensure_schema_and_seed
from app.config import settings
from app.routers import answers, auth, logs, pages, questions, runs
from app.routers import models as models_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_schema_and_seed()  # 幂等：建表 + 补齐默认模型
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AI-GEO 问答内容监控", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware, secret_key=settings.app_secret, max_age=86400
    )

    @app.exception_handler(NotAuthenticated)
    async def _redirect_login(request: Request, exc: NotAuthenticated):  # noqa: ARG001
        return RedirectResponse("/login", status_code=303)

    @app.get("/healthz")
    def healthz():
        """探活端点（公开，不需登录）。"""
        return {"status": "ok"}

    for r in (
        auth.router,
        pages.router,
        questions.router,
        answers.router,
        models_router.router,
        runs.router,
        logs.router,
    ):
        app.include_router(r)
    return app


app = create_app()
