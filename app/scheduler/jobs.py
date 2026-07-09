"""定时作业逻辑：按 schedule_config 组装并运行一个批次。

必须是模块级函数（APScheduler 的 SQLAlchemyJobStore 以 "模块:函数" + 简单参数
序列化作业），故只接收 schedule_id，运行时自行从库加载配置。
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.database import SessionLocal
from app.models import RunBatch, ScheduleConfig
from app.services import ask_service

logger = logging.getLogger(__name__)


def run_scheduled_batch(schedule_id: int) -> None:
    """定时触发入口：创建批次并复用 M1 的 run_batch_job（内含判定与报警）。"""
    db = SessionLocal()
    try:
        sc = db.get(ScheduleConfig, schedule_id)
        if sc is None or not sc.enabled:
            return
        qids = ask_service.resolve_question_ids(db, sc.scope, sc.category, sc.question_ids)
        if not qids:
            logger.info("定时任务 %s 无匹配题目，跳过", sc.name)
            return
        model_ids = sc.model_ids or None
        batch = RunBatch(
            name=f"{sc.name}-{datetime.now():%Y%m%d-%H%M%S}",
            trigger_type="scheduled",
            status="pending",
            judge_enabled=sc.judge_enabled,
            total=0,
            created_by="scheduler",
        )
        db.add(batch)
        sc.last_run_at = datetime.now()
        db.commit()
        batch_id = batch.id
    finally:
        db.close()
    # run_batch_job 自开会话、自兜底、跑完自动报警
    ask_service.run_batch_job(batch_id, qids, model_ids, sc.judge_enabled)
