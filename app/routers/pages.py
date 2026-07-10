"""仪表盘：概览统计 + 分模型判定统计 + 近7天趋势 + 最近批次。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.auth import require_admin, require_user
from app.database import get_db
from app.models import AuditLog, EvalResult, LLMModel, QALog, Question, RunBatch, StandardAnswer
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
        "highrisk": db.scalar(
            select(func.count(EvalResult.id)).where(EvalResult.risk_level.in_(["moderate", "severe"]))
        )
        or 0,
    }
    # 判定准确率：人工标记案例中裁判与人工一致的占比
    labeled_total = db.scalar(select(func.count(EvalResult.id)).where(EvalResult.labeled.is_(True))) or 0
    labeled_agree = (
        db.scalar(
            select(func.count(EvalResult.id)).where(
                EvalResult.labeled.is_(True), EvalResult.label_risk == EvalResult.risk_level
            )
        )
        or 0
    )
    stats["accuracy"] = f"{round(labeled_agree / labeled_total * 100)}%" if labeled_total else "—"

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

    # ===== Chart.js 看板数据 =====
    total_eval = db.scalar(select(func.count(EvalResult.id))) or 0
    normal_eval = (
        db.scalar(select(func.count(EvalResult.id)).where(EvalResult.risk_level == "normal")) or 0
    )
    compliance = round(normal_eval / total_eval * 100) if total_eval else 0

    # 各模型高危趋势（折线，多序列；取数据中出现的最近 7 天）
    mt = db.execute(
        select(QALog.model_id, func.date(QALog.asked_at), func.count(EvalResult.id))
        .join(EvalResult, EvalResult.qa_log_id == QALog.id)
        .where(EvalResult.risk_level.in_(["moderate", "severe"]))
        .group_by(QALog.model_id, func.date(QALog.asked_at))
    ).all()
    dates = sorted({str(d) for _m, d, _c in mt})[-7:]
    series: dict = {}
    for mid, d, c in mt:
        series.setdefault(mid, {})[str(d)] = c
    trend_datasets = [
        {"label": name_map.get(mid, str(mid)), "data": [dc.get(dt, 0) for dt in dates]}
        for mid, dc in series.items()
    ]

    # 风险问题 TOP 排行（高危计数）
    rt = db.execute(
        select(QALog.question_snapshot, func.count(EvalResult.id))
        .join(EvalResult, EvalResult.qa_log_id == QALog.id)
        .where(EvalResult.risk_level.in_(["moderate", "severe"]))
        .group_by(QALog.question_snapshot)
        .order_by(func.count(EvalResult.id).desc())
        .limit(8)
    ).all()

    # 污染类型分布（负面/竞品动态总览）—— JSON 列在 Python 侧统计，限量防海量拉爆
    tally = {"false_info": 0, "defamation": 0, "rumor": 0, "exaggeration": 0}
    for pts in db.scalars(
        select(EvalResult.pollution_types).where(EvalResult.pollution_types.is_not(None)).limit(5000)
    ):
        for t in pts or []:
            if t in tally:
                tally[t] += 1

    charts = {
        "compliance": compliance,
        "model_trend": {"labels": dates, "datasets": trend_datasets},
        "risk_top": {"labels": [(q or "")[:18] for q, _c in rt], "data": [c for _q, c in rt]},
        "pollution": {
            "labels": ["虚假信息", "负面抹黑", "不实谣言", "违规夸大"],
            "data": [tally["false_info"], tally["defamation"], tally["rumor"], tally["exaggeration"]],
        },
    }

    recent = list(db.scalars(select(RunBatch).order_by(RunBatch.id.desc()).limit(10)))
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"stats": stats, "model_stats": model_stats, "charts": charts, "recent": recent},
    )


@router.get("/audit", dependencies=[Depends(require_admin)])
def audit_log(request: Request, db: Session = Depends(get_db), page: int = 1):
    """操作日志（审计溯源，仅 admin）。"""
    page = max(1, page)
    size = 100
    rows = list(
        db.scalars(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(size).offset((page - 1) * size)
        )
    )
    return templates.TemplateResponse(
        request, "audit.html", {"rows": rows, "page": page, "has_next": len(rows) == size}
    )
