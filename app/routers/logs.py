"""后台查询：按问题 / 模型 / 时间 / 判定结论检索问答与判定；支持人工复核覆盖。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin, require_reviewer, require_user
from app.database import get_db
from app.models import EvalResult, LLMModel, QALog
from app.services import export_service
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

PAGE_SIZE = 50
EXPORT_CAP = 10000  # ponytail: 导出上限，防一次拉爆内存；超量再做分页/流式导出


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def _filtered_stmt(question, model_id, date_from, date_to, verdict, pollution, risk=""):
    """构建带筛选的 QALog 查询（查询页与导出共用，保证口径一致）。"""
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
    if verdict or pollution or risk:
        stmt = stmt.join(EvalResult, EvalResult.qa_log_id == QALog.id)
        if verdict:
            stmt = stmt.where(EvalResult.verdict == verdict)
        if pollution:
            stmt = stmt.where(EvalResult.pollution_level == pollution)
        if risk:
            stmt = stmt.where(EvalResult.risk_level == risk)
    return stmt


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
    risk: str = "",
    page: int = 1,
):
    page = max(1, page)
    stmt = _filtered_stmt(question, model_id, date_from, date_to, verdict, pollution, risk)
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
                "risk": risk,
            },
            "page": page,
            "has_next": len(logs) == PAGE_SIZE,
        },
    )


@router.get("/logs/export")
def export_logs(
    db: Session = Depends(get_db),
    question: str = "",
    model_id: str = "",
    date_from: str = "",
    date_to: str = "",
    verdict: str = "",
    pollution: str = "",
    risk: str = "",
):
    """按当前筛选导出 CSV（带 BOM，Excel 直接打开）。"""
    stmt = _filtered_stmt(question, model_id, date_from, date_to, verdict, pollution, risk)
    logs = list(db.scalars(stmt.limit(EXPORT_CAP)))
    names = {m.id: m.display_name for m in db.scalars(select(LLMModel))}
    data = export_service.build_csv(
        export_service.LOG_HEADER, export_service.qalog_rows(logs, names)
    )
    fname = f"ai-geo-logs-{datetime.now():%Y%m%d-%H%M%S}.csv"
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/evals/{eid}/override", dependencies=[Depends(require_reviewer)])
def override_eval(
    request: Request,
    eid: int,
    verdict: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_reviewer),
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


@router.post("/evals/{eid}/label", dependencies=[Depends(require_reviewer)])
def label_eval(
    request: Request,
    eid: int,
    label_risk: str = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_reviewer),
):
    """人工标记正确风险级（纠错回流为 few-shot + 统计判定准确率）。"""
    ev = db.get(EvalResult, eid)
    if ev and label_risk in ("normal", "minor", "moderate", "severe"):
        ev.labeled = True
        ev.label_risk = label_risk
        ev.label_note = note or None
        ev.labeled_by = user
        db.commit()
    return RedirectResponse(request.headers.get("referer", "/logs"), status_code=303)


@router.post("/evals/{eid}/flag", dependencies=[Depends(require_reviewer)])
def flag_eval(request: Request, eid: int, db: Session = Depends(get_db)):
    """疑难案例标记/取消（复核后台）。"""
    ev = db.get(EvalResult, eid)
    if ev:
        ev.flagged = not ev.flagged
        db.commit()
    return RedirectResponse(request.headers.get("referer", "/review"), status_code=303)
