"""批量提问编排（M5）：多样本采集 → 多裁判投票判定(+few-shot) → 口径一致性 →
写新版判定字段 → 中高危报警。

网络调用 asyncio 并发（有界信号量），DB 写入同步。整个 job 是同步函数，内部用
asyncio.run 完成网络扇出——契合 FastAPI BackgroundTasks 与 APScheduler。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import AnswerResult, BaseAdapter
from app.adapters.registry import get_enabled_adapters, get_judge_adapter
from app.config import settings
from app.database import SessionLocal
from app.models import (
    ConsistencyResult,
    EvalResult,
    LLMModel,
    QALog,
    Question,
    RunBatch,
    StandardAnswer,
)
from app.schemas import RISK_ORDER
from app.services import eval_service, notify_service

logger = logging.getLogger(__name__)


def resolve_question_ids(
    db: Session, scope: str, category: str | None = None, question_ids: list[int] | None = None
) -> list[int]:
    if scope == "ids" and question_ids:
        return question_ids
    stmt = select(Question.id).where(Question.enabled.is_(True))
    if scope == "category" and category:
        stmt = stmt.where(Question.category == category)
    return list(db.scalars(stmt))


async def _gather_answers(pairs: list[tuple[str, BaseAdapter]]) -> list[AnswerResult]:
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
                    await asyncio.sleep(2**attempt)
            return res

    return await asyncio.gather(*(one(t, a) for t, a in pairs))


def _active_standard_map(db: Session, question_ids: list[int]) -> dict[int, str]:
    rows = db.scalars(
        select(StandardAnswer).where(
            StandardAnswer.question_id.in_(question_ids), StandardAnswer.is_active.is_(True)
        )
    )
    return {r.question_id: r.content for r in rows}


def _fewshot_cases(db: Session) -> list[dict]:
    """取最近人工标记的纠错案例，回流为 few-shot（问题/回答/人工认定风险）。"""
    k = settings.label_fewshot_k
    if k <= 0:
        return []
    rows = db.execute(
        select(EvalResult, QALog)
        .join(QALog, EvalResult.qa_log_id == QALog.id)
        .where(EvalResult.labeled.is_(True), EvalResult.label_risk.is_not(None))
        .order_by(EvalResult.id.desc())
        .limit(k)
    ).all()
    return [
        {"question": log.question_snapshot, "answer": log.answer_text or "", "label_risk": ev.label_risk}
        for ev, log in rows
    ]


def _is_alert_risk(risk: str | None) -> bool:
    """风险达到中高危阈值？"""
    try:
        return RISK_ORDER.index(risk or "normal") >= RISK_ORDER.index(settings.alert_risk_threshold)
    except ValueError:
        return risk in ("moderate", "severe")


async def _eval_groups(
    groups: list[tuple[Question, LLMModel, list[str]]],
    std_map: dict[int, str],
    judge_adapter: BaseAdapter,
    fewshot: list[dict],
    samples: int,
) -> list[dict]:
    """并发对每个 (问题,模型) 组做多裁判判定 + （多样本时）一致性判定。"""
    sem = asyncio.Semaphore(settings.ask_concurrency)

    async def one(q: Question, m: LLMModel, answers: list[str]) -> dict:
        out: dict = {"qid": q.id, "mid": m.id}
        async with sem:
            if q.id in std_map and answers:
                merged, detail = await eval_service.judge(
                    judge_adapter,
                    question=q.content,
                    standard=std_map[q.id],
                    answer=answers[0],
                    model_name=m.display_name,
                    fewshot=fewshot,
                    timeout=settings.ask_timeout,
                )
                out["eval"] = merged
                out["vote_detail"] = detail
                out["standard"] = std_map[q.id]  # 溯源：记录判定所依据的标准答案快照
            if samples > 1 and len([a for a in answers if a]) >= 2:
                out["cons"] = await eval_service.judge_consistency(
                    judge_adapter,
                    question=q.content,
                    answers=answers,
                    model_name=m.display_name,
                    timeout=settings.ask_timeout,
                )
        return out

    return await asyncio.gather(*(one(q, m, a) for q, m, a in groups))


def run_batch_job(
    batch_id: int,
    question_ids: list[int],
    model_ids: list[int] | None,
    judge_enabled: bool,
    samples: int = 1,
) -> None:
    """执行一个批次（同步入口）。samples>1 开启一致性检测。自开会话、自兜底。"""
    db = SessionLocal()
    try:
        batch = db.get(RunBatch, batch_id)
        if batch is None:
            return
        batch.status = "running"
        batch.started_at = datetime.now()
        db.commit()

        samples = max(1, samples)
        questions = list(db.scalars(select(Question).where(Question.id.in_(question_ids))))
        adapters = get_enabled_adapters(db, model_ids)  # {model_id: (LLMModel, adapter)}

        # 任务矩阵：每题 × 每启用模型 × samples 次
        specs = [
            (q, mid, m, adapter)
            for q in questions
            for mid, (m, adapter) in adapters.items()
            for _ in range(samples)
        ]
        answers = asyncio.run(_gather_answers([(q.content, ad) for q, _mid, _m, ad in specs]))

        # 写 qa_log，并按 (题,模型) 分组以便一致性/判定
        now = datetime.now()
        grouped: dict[tuple[int, int], list[tuple[QALog, AnswerResult, Question, LLMModel]]] = defaultdict(list)
        for (q, mid, m, _ad), ans in zip(specs, answers):
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
            grouped[(q.id, mid)].append((log, ans, q, m))
        db.commit()  # 拿到 log.id

        success = sum(1 for g in grouped.values() for (_l, a, _q, _m) in g if a.status == "success")
        total = sum(len(g) for g in grouped.values())
        alert = 0

        if judge_enabled:
            judge_adapter = get_judge_adapter(db, settings.judge_model_key)
            if judge_adapter is None:
                logger.warning("裁判模型 %s 不可用（未配置密钥？），跳过判定", settings.judge_model_key)
            else:
                std_map = _active_standard_map(db, [q.id for q in questions])
                fewshot = _fewshot_cases(db)
                # 每组代表(首个成功样本)用于正确性判定；全部成功样本用于一致性
                groups, rep = [], {}
                for (qid, mid), items in grouped.items():
                    ok = [(l, a) for (l, a, _q, _m) in items if a.status == "success"]
                    q, m = items[0][2], items[0][3]
                    ans_texts = [a.answer_text or "" for (_l, a) in ok]
                    groups.append((q, m, ans_texts))
                    rep[(qid, mid)] = ok[0][0].id if ok else None

                results = asyncio.run(_eval_groups(groups, std_map, judge_adapter, fewshot, samples))
                alert = _write_eval_results(db, results, rep, batch.id, samples)

        batch.total = total
        batch.success_count = success
        batch.fail_count = total - success
        batch.alert_count = alert
        batch.status = "done"
        batch.finished_at = datetime.now()
        db.commit()

        if alert:
            try:
                notify_service.alert_batch(db, batch)
            except Exception:  # noqa: BLE001
                logger.exception("批次 %s 报警发送异常", batch_id)
    except Exception:  # noqa: BLE001 批次级兜底
        db.rollback()
        b = db.get(RunBatch, batch_id)
        if b is not None:
            b.status = "failed"
            b.finished_at = datetime.now()
            db.commit()
        logger.exception("批次 %s 执行失败", batch_id)
    finally:
        db.close()


def _write_eval_results(db: Session, results: list[dict], rep: dict, batch_id: int, samples: int) -> int:
    """写 EvalResult / ConsistencyResult，返回中高危+矛盾命中数（用于报警计数）。"""
    alert = 0
    for r in results:
        key = (r["qid"], r["mid"])
        ev = r.get("eval")
        rep_log_id = rep.get(key)
        if ev is not None and rep_log_id is not None:
            db.add(
                EvalResult(
                    qa_log_id=rep_log_id,
                    judge_model=settings.judge_model_key,
                    standard_answer_snapshot=r.get("standard"),
                    is_correct=ev.is_correct,
                    correctness_score=ev.correctness_score,
                    semantic_score=ev.semantic_score,
                    pollution_level=ev.pollution_level,
                    pollution_types=ev.pollution_types,
                    deviation_level=ev.deviation_level,
                    risk_level=ev.risk_level,
                    verdict=ev.verdict,
                    reason=ev.reason,
                    details={
                        "logic_errors": ev.logic_errors,
                        "missing_info": ev.missing_info,
                        "distortions": ev.distortions,
                        "hallucinations": ev.hallucinations,
                        "risk_notes": ev.risk_notes,
                    },
                    vote_detail=r.get("vote_detail"),
                    evaluated_at=datetime.now(),
                )
            )
            if _is_alert_risk(ev.risk_level):
                alert += 1
        cons = r.get("cons")
        if cons is not None:
            db.add(
                ConsistencyResult(
                    batch_id=batch_id,
                    question_id=r["qid"],
                    model_id=r["mid"],
                    sample_count=samples,
                    consistency_score=cons.consistency_score,
                    is_consistent=cons.is_consistent,
                    contradiction=cons.contradiction,
                    reason=cons.reason,
                    evaluated_at=datetime.now(),
                )
            )
            if cons.contradiction:
                alert += 1
    db.commit()
    return alert
