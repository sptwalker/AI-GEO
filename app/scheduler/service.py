"""APScheduler 内嵌调度：进程内单实例 + 作业持久化到数据库。

作业与 schedule_config 镜像：作业 id 固定为 f"sched-{配置id}"，增删改时同步。
用 pytz 时区（APScheduler 3.x 依赖 pytz），规避 zoneinfo 兼容问题。
"""
from __future__ import annotations

import logging

import pytz
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import ScheduleConfig
from app.scheduler.jobs import run_scheduled_batch

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _tz():
    return pytz.timezone(settings.scheduler_timezone)


def get_scheduler() -> BackgroundScheduler | None:
    """返回当前调度器实例（未启用调度时为 None）。"""
    return _scheduler


def validate_cron(expr: str) -> bool:
    """校验 5 段 cron 是否合法（供路由在保存前检查）。"""
    try:
        CronTrigger.from_crontab(expr, timezone=_tz())
        return True
    except Exception:  # noqa: BLE001
        return False


def start_scheduler() -> BackgroundScheduler:
    """启动调度器并按库中启用的定时任务注册作业（幂等）。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=settings.database_url)},
        timezone=_tz(),
    )
    scheduler.start()
    _scheduler = scheduler
    sync_jobs()
    logger.info("调度器已启动 (tz=%s)", settings.scheduler_timezone)
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def upsert_job(sc: ScheduleConfig) -> None:
    """按单条配置增/改作业；配置停用则移除。调度器未运行时为空操作。"""
    if _scheduler is None:
        return
    if sc.enabled and validate_cron(sc.cron):
        _scheduler.add_job(
            run_scheduled_batch,
            CronTrigger.from_crontab(sc.cron, timezone=_tz()),
            args=[sc.id],
            id=f"sched-{sc.id}",
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
        )
    else:
        remove_job(sc.id)


def remove_job(sc_id: int) -> None:
    if _scheduler is None:
        return
    try:
        _scheduler.remove_job(f"sched-{sc_id}")
    except Exception:  # noqa: BLE001 作业不存在即忽略
        pass


def sync_jobs() -> None:
    """把库中启用的定时任务同步为作业，并清理已删除/停用的残留作业。"""
    if _scheduler is None:
        return
    db = SessionLocal()
    try:
        active: set[str] = set()
        for sc in db.scalars(select(ScheduleConfig).where(ScheduleConfig.enabled.is_(True))):
            if validate_cron(sc.cron):
                _scheduler.add_job(
                    run_scheduled_batch,
                    CronTrigger.from_crontab(sc.cron, timezone=_tz()),
                    args=[sc.id],
                    id=f"sched-{sc.id}",
                    replace_existing=True,
                    misfire_grace_time=300,
                    coalesce=True,
                )
                active.add(f"sched-{sc.id}")
        for job in _scheduler.get_jobs():
            if job.id.startswith("sched-") and job.id not in active:
                _scheduler.remove_job(job.id)
    finally:
        db.close()


def next_run_of(sc_id: int) -> str | None:
    """返回某定时任务的下次运行时间（字符串），无则 None。供页面展示。"""
    if _scheduler is None:
        return None
    job = _scheduler.get_job(f"sched-{sc_id}")
    if job is None or job.next_run_time is None:
        return None
    return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
