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
    key_status = {m.id: bool(os.getenv(m.api_key_env or "", "")) for m in models}
    return templates.TemplateResponse(
        request, "models.html", {"models": models, "key_status": key_status}
    )


@router.post("/models/{mid}/save")
def save_model(
    mid: int,
    enabled: str = Form("off"),
    base_url: str = Form(""),
    model_name: str = Form(""),
    concurrency: str = Form("4"),
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
        db.commit()
    return RedirectResponse("/models", status_code=303)
