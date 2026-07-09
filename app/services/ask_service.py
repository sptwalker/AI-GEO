"""批量提问编排：并发向各模型提问、写 qa_log；可选调用判定写 eval_result。

设计要点：网络调用用 asyncio 并发（有界信号量），DB 写入用同步会话。整个批次
job 是同步函数，内部用 asyncio.run 完成网络扇出——契合 FastAPI BackgroundTasks
（线程池执行同步函数）与 M2 的 APScheduler。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import AnswerResult, BaseAdapter
from app.adapters.registry import get_enabled_adapters, get_judge_adapter
from app.config import settings
from app.database import SessionLocal
from app.models import EvalResult, LLMModel, QALog, Question, RunBatch, StandardAnswer
from app.services import eval_service, notify_service

logger = logging.getLogger(__name__)


def resolve_question_ids(
    db: Session,
    scope: str,
    category: str | None = None,
    question_ids: list[int] | None = None,
) -> list[int]:
    """把「范围选择」解析成具体题目 id 列表。"""
    if scope == "ids" and question_ids:
        return question_ids
    stmt = select(Question.id).where(Question.enabled.is_(True))
    if scope == "category" and category:
        stmt = stmt.where(Question.category == category)
    return list(db.scalars(stmt))


async def _gather_answers(
    pairs: list[tuple[str, BaseAdapter]]
) -> list[AnswerResult]:
    """并发提问，结果与入参顺序对齐。失败自带退避重试。"""
    sem = asyncio.Semaphore(settings.ask_concurrency)

    async def one(text: str, adapter: BaseAdapter) -> AnswerResult:
        async with sem:
            res = AnswerResult(status="failed", error_msg="未执行")
            for attempt in range(settings.ask_max_retries + 1):
                res = await adapter.ask(text, timeout=settings.ask_timeout)
                if res.status == "success":
                    return res
                if attempt < settings.ask_max_retries:
                    await asyncio.sleep(2**attempt)  # 指数退避
            return res

    return await asyncio.gather(*(one(t, a) for t, a in pairs))


def _active_standard_map(db: Session, question_ids: list[int]) -> dict[int, str]:
    rows = db.scalars(
        select(StandardAnswer).where(
            StandardAnswer.question_id.in_(question_ids),
            StandardAnswer.is_active.is_(True),
        )
    )
    return {r.question_id: r.content for r in rows}


def _run_evals(
    db: Session,
    logs: list[tuple[QALog, Question, LLMModel, AnswerResult]],
    std_map: dict[int, str],
    judge_adapter: BaseAdapter,
) -> int:
    """对「成功且有标准答案」的回答并发判定，写 eval_result，返回命中报警数。"""
    targets = [
        (log, q, m) for (log, q, m, ans) in logs if ans.status == "success" and q.id in std_map
    ]
    if not targets:
        return 0

    async def run_all():
        sem = asyncio.Semaphore(settings.ask_concurrency)

        async def one(q: Question, m: LLMModel, answer: str):
            async with sem:
                return await eval_service.judge(
                    judge_adapter,
                    question=q.content,
                    standard=std_map[q.id],
                    answer=answer,
                    model_name=m.display_name,
                    timeout=settings.ask_timeout,
                )

        return await asyncio.gather(
            *(one(q, m, log.answer_text or "") for (log, q, m) in targets)
        )

    outputs = asyncio.run(run_all())
    alert = 0
    for (log, q, _m), out in zip(targets, outputs):
        db.add(
            EvalResult(
                qa_log_id=log.id,
                judge_model=settings.judge_model_key,
                standard_answer_snapshot=std_map[q.id],
                is_correct=out.is_correct,
                correctness_score=out.correctness_score,
                pollution_level=out.pollution_level,
                deviation_level=out.deviation_level,
                verdict=out.verdict,
                reason=out.reason,
                details={
                    "matched_points": out.matched_points,
                    "missing_points": out.missing_points,
                    "hallucinations": out.hallucinations,
                    "risk_notes": out.risk_notes,
                },
                evaluated_at=datetime.now(),
            )
        )
        if out.verdict == "fail" or out.pollution_level == "severe":
            alert += 1
    db.commit()
    return alert


def run_batch_job(
    batch_id: int,
    question_ids: list[int],
    model_ids: list[int] | None,
    judge_enabled: bool,
) -> None:
    """执行一个批次（同步入口，供 BackgroundTasks / M2 调度调用）。自开会话、自兜底。"""
    db = SessionLocal()
    try:
        batch = db.get(RunBatch, batch_id)
        if batch is None:
            return
        batch.status = "running"
        batch.started_at = datetime.now()
        db.commit()

        questions = list(db.scalars(select(Question).where(Question.id.in_(question_ids))))
        adapters = get_enabled_adapters(db, model_ids)  # {model_id: (LLMModel, adapter)}

        # 任务矩阵：每题 × 每启用模型
        specs = [
            (q, mid, m, adapter)
            for q in questions
            for mid, (m, adapter) in adapters.items()
        ]
        answers = asyncio.run(_gather_answers([(q.content, adapter) for q, _mid, _m, adapter in specs]))

        logs: list[tuple[QALog, Question, LLMModel, AnswerResult]] = []
        now = datetime.now()
        for (q, mid, m, _adapter), ans in zip(specs, answers):
            log = QALog(
                batch_id=batch.id,
                question_id=q.id,
                model_id=mid,
                question_snapshot=q.content,
                answer_text=ans.answer_text,
                asked_at=now,
                latency_ms=ans.latency_ms,
                status=ans.status,
                error_msg=ans.error_msg,
                token_usage=ans.token_usage,
            )
            db.add(log)
            logs.append((log, q, m, ans))
        db.commit()  # flush 拿到 log.id

        success = sum(1 for _l, _q, _m, a in logs if a.status == "success")
        alert = 0
        if judge_enabled:
            judge_adapter = get_judge_adapter(db, settings.judge_model_key)
            if judge_adapter is None:
                logger.warning("裁判模型 %s 不可用（未配置密钥？），跳过判定", settings.judge_model_key)
            else:
                std_map = _active_standard_map(db, [q.id for q in questions])
                alert = _run_evals(db, logs, std_map, judge_adapter)

        batch.total = len(logs)
        batch.success_count = success
        batch.fail_count = len(logs) - success
        batch.alert_count = alert
        batch.status = "done"
        batch.finished_at = datetime.now()
        db.commit()

        # 命中异常则报警（飞书）；失败不影响批次结果
        if alert:
            try:
                notify_service.alert_batch(db, batch)
            except Exception:  # noqa: BLE001
                logger.exception("批次 %s 报警发送异常", batch_id)
    except Exception:  # noqa: BLE001 批次级兜底：标记失败并记录，MVP 不做重试队列
        db.rollback()
        b = db.get(RunBatch, batch_id)
        if b is not None:
            b.status = "failed"
            b.finished_at = datetime.now()
            db.commit()
        logger.exception("批次 %s 执行失败", batch_id)
    finally:
        db.close()
