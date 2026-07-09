"""仪表盘：概览统计 + 分模型判定统计 + 近7天趋势 + 最近批次。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import EvalResult, LLMModel, QALog, Question, RunBatch, StandardAnswer
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


def _pct(n: int, total: int) -> int:
    return round(n / total * 100) if total else 0


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

    # 分模型判定统计（含未判定的回答，故用外连接）
    name_map = {m.id: m.display_name for m in db.scalars(select(LLMModel))}
    rows = db.execute(
        select(
            QALog.model_id,
            func.count(QALog.id),
            func.sum(case((EvalResult.verdict == "pass", 1), else_=0)),
            func.sum(case((EvalResult.verdict == "warn", 1), else_=0)),
            func.sum(case((EvalResult.verdict == "fail", 1), else_=0)),
            func.avg(EvalResult.correctness_score),
        )
        .join(EvalResult, EvalResult.qa_log_id == QALog.id, isouter=True)
        .group_by(QALog.model_id)
    ).all()
    model_stats = []
    for mid, total, p, w, f, avg in rows:
        p, w, f = int(p or 0), int(w or 0), int(f or 0)
        model_stats.append(
            {
                "name": name_map.get(mid, mid),
                "total": total,
                "pass": p,
                "warn": w,
                "fail": f,
                "avg": round(avg or 0),
                "pass_pct": _pct(p, total),
                "warn_pct": _pct(w, total),
                "fail_pct": _pct(f, total),
            }
        )

    # 近 7 天趋势
    trows = db.execute(
        select(
            func.date(QALog.asked_at),
            func.count(QALog.id),
            func.sum(case((EvalResult.verdict == "fail", 1), else_=0)),
        )
        .join(EvalResult, EvalResult.qa_log_id == QALog.id, isouter=True)
        .group_by(func.date(QALog.asked_at))
        .order_by(func.date(QALog.asked_at).desc())
        .limit(7)
    ).all()
    trend = [{"date": str(d), "total": t, "fail": int(fl or 0)} for d, t, fl in trows]
    trend.reverse()

    recent = list(db.scalars(select(RunBatch).order_by(RunBatch.id.desc()).limit(10)))
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"stats": stats, "model_stats": model_stats, "trend": trend, "recent": recent},
    )
