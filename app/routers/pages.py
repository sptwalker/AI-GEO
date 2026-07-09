"""仪表盘。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import EvalResult, LLMModel, QALog, Question, RunBatch, StandardAnswer
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    stats = {
        "questions": db.scalar(select(func.count(Question.id))) or 0,
        "answers": db.scalar(
            select(func.count(StandardAnswer.id)).where(StandardAnswer.is_active.is_(True))
        )
        or 0,
        "models": db.scalar(select(func.count(LLMModel.id)).where(LLMModel.enabled.is_(True)))
        or 0,
        "logs": db.scalar(select(func.count(QALog.id))) or 0,
        "fails": db.scalar(select(func.count(EvalResult.id)).where(EvalResult.verdict == "fail"))
        or 0,
        "severe": db.scalar(
            select(func.count(EvalResult.id)).where(EvalResult.pollution_level == "severe")
        )
        or 0,
    }
    recent = list(db.scalars(select(RunBatch).order_by(RunBatch.id.desc()).limit(10)))
    return templates.TemplateResponse(
        request, "dashboard.html", {"stats": stats, "recent": recent}
    )
