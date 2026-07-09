"""登录 / 登出。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import authenticate, current_user, login_session, logout_session
from app.database import get_db
from app.templating import templates

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate(db, username, password)
    if user:
        login_session(request, user)
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"error": "用户名或密码错误"}, status_code=401
    )


@router.get("/logout")
def logout(request: Request):
    logout_session(request)
    return RedirectResponse("/login", status_code=303)
