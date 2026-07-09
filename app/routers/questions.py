"""题库管理：列表 / 增删改 / 批量导入。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_admin, require_user
from app.database import get_db
from app.models import Question
from app.services import import_service
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/questions")
def list_questions(
    request: Request, db: Session = Depends(get_db), q: str = "", category: str = ""
):
    stmt = select(Question).order_by(Question.id.desc())
    if q:
        stmt = stmt.where(Question.content.contains(q))
    if category:
        stmt = stmt.where(Question.category == category)
    questions = list(db.scalars(stmt.limit(500)))
    cats = [c for c in db.scalars(select(Question.category).distinct()) if c]
    return templates.TemplateResponse(
        request,
        "questions.html",
        {
            "questions": questions,
            "cats": cats,
            "q": q,
            "category": category,
            "imported": request.query_params.get("imported"),
            "skipped": request.query_params.get("skipped"),
            "failed": request.query_params.get("failed"),
        },
    )


@router.post("/questions/add", dependencies=[Depends(require_admin)])
def add_question(
    content: str = Form(...),
    category: str = Form(""),
    product: str = Form(""),
    remark: str = Form(""),
    db: Session = Depends(get_db),
):
    if content.strip():
        db.add(
            Question(
                content=content.strip(),
                category=category or None,
                product=product or None,
                remark=remark or None,
            )
        )
        db.commit()
    return RedirectResponse("/questions", status_code=303)


@router.post("/questions/import", dependencies=[Depends(require_admin)])
async def import_questions(
    text: str = Form(""),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    content = await file.read() if file and file.filename else None
    filename = file.filename if file else None
    res = import_service.import_questions(
        db, text=text or None, filename=filename, content=content
    )
    return RedirectResponse(
        f"/questions?imported={res.created}&skipped={res.skipped}&failed={res.failed}",
        status_code=303,
    )


@router.post("/questions/{qid}/edit", dependencies=[Depends(require_admin)])
def edit_question(
    qid: int,
    content: str = Form(...),
    category: str = Form(""),
    product: str = Form(""),
    remark: str = Form(""),
    enabled: str = Form("off"),
    db: Session = Depends(get_db),
):
    obj = db.get(Question, qid)
    if obj and content.strip():
        obj.content = content.strip()
        obj.category = category or None
        obj.product = product or None
        obj.remark = remark or None
        obj.enabled = enabled == "on"
        db.commit()
    return RedirectResponse("/questions", status_code=303)


@router.post("/questions/{qid}/delete", dependencies=[Depends(require_admin)])
def delete_question(qid: int, db: Session = Depends(get_db)):
    obj = db.get(Question, qid)
    if obj:
        db.delete(obj)
        db.commit()
    return RedirectResponse("/questions", status_code=303)
