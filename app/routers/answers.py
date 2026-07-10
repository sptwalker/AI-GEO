"""标准答案库：录入 / 修改（版本递增）/ 删除。一题一条 is_active 权威答案。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_reviewer, require_user
from app.database import get_db
from app.models import Question, StandardAnswer
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/answers")
def list_answers(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(StandardAnswer, Question)
        .join(Question, StandardAnswer.question_id == Question.id)
        .where(StandardAnswer.is_active.is_(True))
        .order_by(StandardAnswer.updated_at.desc())
    ).all()
    questions = list(db.scalars(select(Question).order_by(Question.id.desc()).limit(500)))
    return templates.TemplateResponse(
        request, "answers.html", {"rows": rows, "questions": questions}
    )


@router.post("/answers/save")
def save_answer(
    question_id: int = Form(...),
    content: str = Form(...),
    source: str = Form(""),
    db: Session = Depends(get_db),
    user: str = Depends(require_reviewer),
):
    """新增或更新某题的标准答案：旧版本置为非活跃保留，新版本 version+1。"""
    content = content.strip()
    if not content:
        return RedirectResponse("/answers", status_code=303)
    active = db.scalar(
        select(StandardAnswer).where(
            StandardAnswer.question_id == question_id, StandardAnswer.is_active.is_(True)
        )
    )
    version = 1
    if active is not None:
        active.is_active = False
        version = active.version + 1
    db.add(
        StandardAnswer(
            question_id=question_id,
            content=content,
            source=source or None,
            version=version,
            is_active=True,
            updated_by=user,
        )
    )
    db.commit()
    return RedirectResponse("/answers", status_code=303)


@router.post("/answers/{aid}/delete", dependencies=[Depends(require_reviewer)])
def delete_answer(aid: int, db: Session = Depends(get_db)):
    obj = db.get(StandardAnswer, aid)
    if obj:
        db.delete(obj)
        db.commit()
    return RedirectResponse("/answers", status_code=303)
