"""模型配置：启停、base_url / model_name / 并发。密钥只显示是否已配置。"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import LLMModel
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/models")
def list_models(request: Request, db: Session = Depends(get_db)):
    models = list(db.scalars(select(LLMModel).order_by(LLMModel.id)))
    # key_saved: 页面已存到 DB 的密钥；key_status: 综合(DB 或环境变量)是否已配置
    key_saved = {m.id: bool((m.config or {}).get("api_key")) for m in models}
    key_status = {
        m.id: key_saved[m.id] or bool(os.getenv(m.api_key_env or "", "")) for m in models
    }
    return templates.TemplateResponse(
        request,
        "models.html",
        {"models": models, "key_status": key_status, "key_saved": key_saved},
    )


@router.post("/models/{mid}/save")
def save_model(
    mid: int,
    enabled: str = Form("off"),
    base_url: str = Form(""),
    model_name: str = Form(""),
    concurrency: str = Form("4"),
    api_key: str = Form(""),
    clear_key: str = Form("off"),
    db: Session = Depends(get_db),
):
    m = db.get(LLMModel, mid)
    if m:
        m.enabled = enabled == "on"
        m.base_url = base_url or None
        m.model_name = model_name or None
        try:
            m.concurrency = max(1, int(concurrency))
        except ValueError:
            pass
        # 密钥：填了则更新；勾选清除则删除；都没有则保持不变（避免保存其它字段时误清空）
        # JSON 列需整体赋新对象，SQLAlchemy 才能侦测到变更
        cfg = dict(m.config or {})
        if clear_key == "on":
            cfg.pop("api_key", None)
            m.config = cfg
        elif api_key.strip():
            cfg["api_key"] = api_key.strip()
            m.config = cfg
        db.commit()
    return RedirectResponse("/models", status_code=303)
