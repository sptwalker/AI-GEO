"""定时任务管理：创建 / 启停 / 删除 / 立即运行。"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models import AlertLog, LLMModel, Question, ScheduleConfig
from app.scheduler import service as sched
from app.scheduler.jobs import run_scheduled_batch
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


def _parse_ids(text: str) -> list[int]:
    out = []
    for part in text.replace("，", ",").replace(" ", ",").split(","):
        if part.strip().isdigit():
            out.append(int(part.strip()))
    return out


@router.get("/schedules")
def list_schedules(request: Request, db: Session = Depends(get_db)):
    schedules = list(db.scalars(select(ScheduleConfig).order_by(ScheduleConfig.id.desc())))
    next_runs = {s.id: sched.next_run_of(s.id) for s in schedules}
    cats = [c for c in db.scalars(select(Question.category).distinct()) if c]
    models = list(
        db.scalars(select(LLMModel).where(LLMModel.enabled.is_(True)).order_by(LLMModel.id))
    )
    alerts = list(db.scalars(select(AlertLog).order_by(AlertLog.id.desc()).limit(20)))
    return templates.TemplateResponse(
        request,
        "schedules.html",
        {
            "schedules": schedules,
            "next_runs": next_runs,
            "cats": cats,
            "models": models,
            "alerts": alerts,
            "scheduler_on": sched.get_scheduler() is not None,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/schedules")
def create_schedule(
    name: str = Form(...),
    cron: str = Form(...),
    scope: str = Form("all"),
    category: str = Form(""),
    question_ids: str = Form(""),
    model_ids: list[int] = Form([]),
    judge_enabled: str = Form("off"),
    enabled: str = Form("off"),
    db: Session = Depends(get_db),
    user: str = Depends(require_user),
):
    if not sched.validate_cron(cron.strip()):
        return RedirectResponse("/schedules?error=bad_cron", status_code=303)
    sc = ScheduleConfig(
        name=name.strip(),
        cron=cron.strip(),
        scope=scope,
        category=category or None,
        question_ids=_parse_ids(question_ids) or None,
        model_ids=model_ids or None,
        judge_enabled=(judge_enabled == "on"),
        enabled=(enabled == "on"),
        created_by=user,
    )
    db.add(sc)
    db.commit()
    sched.upsert_job(sc)  # 内嵌调度时立即生效
    return RedirectResponse("/schedules", status_code=303)


@router.post("/schedules/{sid}/toggle")
def toggle_schedule(sid: int, db: Session = Depends(get_db)):
    sc = db.get(ScheduleConfig, sid)
    if sc:
        sc.enabled = not sc.enabled
        db.commit()
        sched.upsert_job(sc)
    return RedirectResponse("/schedules", status_code=303)


@router.post("/schedules/{sid}/delete")
def delete_schedule(sid: int, db: Session = Depends(get_db)):
    sc = db.get(ScheduleConfig, sid)
    if sc:
        sched.remove_job(sc.id)
        db.delete(sc)
        db.commit()
    return RedirectResponse("/schedules", status_code=303)


@router.post("/schedules/{sid}/run-now")
def run_now(sid: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """立即执行一次（走后台任务，不依赖调度器是否开启）。"""
    if db.get(ScheduleConfig, sid):
        background_tasks.add_task(run_scheduled_batch, sid)
    return RedirectResponse("/runs", status_code=303)
