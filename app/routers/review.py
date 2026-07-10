"""人工复核后台（M6）：运维审核机器判定、标注正确风险、标记疑难、就地修正标准答案。

标注结果回流为 few-shot（见 eval_service），实现"数据反哺迭代"。仅 admin/reviewer 可进。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_reviewer
from app.database import get_db
from app.models import EvalResult, LLMModel, QALog, StandardAnswer
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_reviewer)])


@router.get("/review")
def review_queue(request: Request, db: Session = Depends(get_db), show: str = "pending"):
    stmt = (
        select(EvalResult, QALog)
        .join(QALog, EvalResult.qa_log_id == QALog.id)
        .order_by(EvalResult.id.desc())
    )
    if show == "flagged":
        stmt = stmt.where(EvalResult.flagged.is_(True))
    elif show == "pending":  # 高危且未标注 = 待复核
        stmt = stmt.where(
            EvalResult.risk_level.in_(["moderate", "severe"]), EvalResult.labeled.is_(False)
        )
    rows = db.execute(stmt.limit(100)).all()
    qids = {log.question_id for _ev, log in rows}
    std = {}
    if qids:
        std = {
            a.question_id: a
            for a in db.scalars(
                select(StandardAnswer).where(
                    StandardAnswer.question_id.in_(qids), StandardAnswer.is_active.is_(True)
                )
            )
        }
    model_names = {m.id: m.display_name for m in db.scalars(select(LLMModel))}
    return templates.TemplateResponse(
        request,
        "review.html",
        {"rows": rows, "std": std, "model_names": model_names, "show": show},
    )
