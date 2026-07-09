"""后台查询：按问题 / 模型 / 时间 / 判定结论检索问答与判定；支持人工复核覆盖。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import EvalResult, LLMModel, QALog
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

PAGE_SIZE = 50


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


@router.get("/logs")
def query_logs(
    request: Request,
    db: Session = Depends(get_db),
    question: str = "",
    model_id: str = "",
    date_from: str = "",
    date_to: str = "",
    verdict: str = "",
    pollution: str = "",
    page: int = 1,
):
    page = max(1, page)
    stmt = select(QALog).order_by(QALog.id.desc())
    if question:
        stmt = stmt.where(QALog.question_snapshot.contains(question))
    if model_id.isdigit():
        stmt = stmt.where(QALog.model_id == int(model_id))
    df, dt = _parse_date(date_from), _parse_date(date_to)
    if df:
        stmt = stmt.where(QALog.asked_at >= df)
    if dt:
        stmt = stmt.where(QALog.asked_at < dt + timedelta(days=1))
    if verdict or pollution:
        stmt = stmt.join(EvalResult, EvalResult.qa_log_id == QALog.id)
        if verdict:
            stmt = stmt.where(EvalResult.verdict == verdict)
        if pollution:
            stmt = stmt.where(EvalResult.pollution_level == pollution)

    logs = list(db.scalars(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)))
    models = list(db.scalars(select(LLMModel).order_by(LLMModel.id)))
    return templates.TemplateResponse(
        request,
        "logs.html",
        {
            "logs": logs,
            "models": models,
            "model_names": {m.id: m.display_name for m in models},
            "f": {
                "question": question,
                "model_id": model_id,
                "date_from": date_from,
                "date_to": date_to,
                "verdict": verdict,
                "pollution": pollution,
            },
            "page": page,
            "has_next": len(logs) == PAGE_SIZE,
        },
    )


@router.post("/evals/{eid}/override")
def override_eval(
    request: Request,
    eid: int,
    verdict: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    """人工复核：覆盖裁判结论并留痕。"""
    ev = db.get(EvalResult, eid)
    if ev:
        ev.verdict = verdict
        ev.overridden = True
        if reason:
            ev.reason = (ev.reason or "") + f"\n[人工复核 {user}] {reason}"
        db.commit()
    return RedirectResponse(request.headers.get("referer", "/logs"), status_code=303)
