"""运行批次：发起手动监控、查看批次进度与明细。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.registry import get_enabled_adapters
from app.auth import require_admin, require_user
from app.database import get_db
from app.models import LLMModel, QALog, Question, RunBatch
from app.services import ask_service, export_service
from app.services.ask_service import run_batch_job
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/runs")
def list_runs(request: Request, db: Session = Depends(get_db)):
    batches = list(db.scalars(select(RunBatch).order_by(RunBatch.id.desc()).limit(50)))
    cats = [c for c in db.scalars(select(Question.category).distinct()) if c]
    models = list(
        db.scalars(select(LLMModel).where(LLMModel.enabled.is_(True)).order_by(LLMModel.id))
    )
    q_enabled = db.scalar(select(func.count(Question.id)).where(Question.enabled.is_(True))) or 0
    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "batches": batches,
            "cats": cats,
            "models": models,
            "q_enabled": q_enabled,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/runs", dependencies=[Depends(require_admin)])
def trigger_run(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(""),
    scope: str = Form("all"),
    category: str = Form(""),
    model_ids: list[int] = Form([]),
    judge_enabled: str = Form("off"),
    db: Session = Depends(get_db),
    user: str = Depends(require_admin),
):
    qids = ask_service.resolve_question_ids(db, scope, category or None, None)
    if not qids:
        return RedirectResponse("/runs?error=no_question", status_code=303)
    adapters = get_enabled_adapters(db, model_ids or None)
    if not adapters:
        return RedirectResponse("/runs?error=no_model", status_code=303)

    batch = RunBatch(
        name=name or f"手动批次-{datetime.now():%Y%m%d-%H%M%S}",
        trigger_type="manual",
        status="pending",
        judge_enabled=(judge_enabled == "on"),
        total=len(qids) * len(adapters),
        created_by=user,
    )
    db.add(batch)
    db.commit()
    # 后台执行，页面立即返回批次详情（刷新查看进度）
    background_tasks.add_task(
        run_batch_job, batch.id, qids, model_ids or None, judge_enabled == "on"
    )
    return RedirectResponse(f"/runs/{batch.id}", status_code=303)


@router.get("/runs/{bid}")
def run_detail(request: Request, bid: int, db: Session = Depends(get_db)):
    batch = db.get(RunBatch, bid)
    if batch is None:
        return RedirectResponse("/runs", status_code=303)
    logs = list(db.scalars(select(QALog).where(QALog.batch_id == bid).order_by(QALog.id)))
    model_names = {m.id: m.display_name for m in db.scalars(select(LLMModel))}
    return templates.TemplateResponse(
        request, "run_detail.html", {"batch": batch, "logs": logs, "model_names": model_names}
    )


@router.get("/runs/{bid}/export")
def export_batch(bid: int, db: Session = Depends(get_db)):
    """导出某批次全部问答+判定为 CSV。"""
    logs = list(db.scalars(select(QALog).where(QALog.batch_id == bid).order_by(QALog.id)))
    names = {m.id: m.display_name for m in db.scalars(select(LLMModel))}
    data = export_service.build_csv(
        export_service.LOG_HEADER, export_service.qalog_rows(logs, names)
    )
    fname = f"ai-geo-batch{bid}-{datetime.now():%Y%m%d-%H%M%S}.csv"
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
