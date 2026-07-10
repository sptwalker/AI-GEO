"""模型配置：启停、base_url / model_name / 并发、密钥、连通测试。"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.auth import require_admin, require_user
from app.database import get_db
from app.models import LLMModel
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/models")
def list_models(request: Request, db: Session = Depends(get_db)):
    models = list(db.scalars(select(LLMModel).order_by(LLMModel.id)))
    # key_saved: 页面已存到 DB 的密钥；key_status: 综合(DB 或环境变量)是否已配置
    key_saved = {m.id: bool((m.config or {}).get("api_key")) for m in models}
    key_status = {
        m.id: key_saved[m.id] or bool(os.getenv(m.api_key_env or "", "")) for m in models
    }
    return templates.TemplateResponse(
        request,
        "models.html",
        {"models": models, "key_status": key_status, "key_saved": key_saved,
         "error": request.query_params.get("error")},
    )


@router.post("/models", dependencies=[Depends(require_admin)])
def create_model(
    model_key: str = Form(...),
    display_name: str = Form(...),
    adapter_type: str = Form("openai_compatible"),
    base_url: str = Form(""),
    model_name: str = Form(""),
    api_key: str = Form(""),
    db: Session = Depends(get_db),
):
    """UI 自助新增自定义模型（通用/垂类/私域客服，OpenAI 兼容端点）。"""
    model_key = model_key.strip()
    if not model_key or not display_name.strip():
        return RedirectResponse("/models?error=invalid", status_code=303)
    if db.scalar(select(LLMModel).where(LLMModel.model_key == model_key)):
        return RedirectResponse("/models?error=exists", status_code=303)
    cfg = {"api_key": api_key.strip()} if api_key.strip() else None
    db.add(
        LLMModel(
            model_key=model_key,
            display_name=display_name.strip(),
            adapter_type=adapter_type if adapter_type in ("openai_compatible", "web_automation") else "openai_compatible",
            base_url=base_url.strip() or None,
            model_name=model_name.strip() or None,
            api_key_env=f"{model_key.upper()}_API_KEY",
            enabled=True,
            config=cfg,
        )
    )
    db.commit()
    return RedirectResponse("/models", status_code=303)


@router.post("/models/{mid}/delete", dependencies=[Depends(require_admin)])
def delete_model(mid: int, db: Session = Depends(get_db)):
    m = db.get(LLMModel, mid)
    if m:
        db.delete(m)
        db.commit()
    return RedirectResponse("/models", status_code=303)


@router.post("/models/{mid}/save", dependencies=[Depends(require_admin)])
def save_model(
    mid: int,
    enabled: str = Form("off"),
    base_url: str = Form(""),
    model_name: str = Form(""),
    concurrency: str = Form("4"),
    api_key: str = Form(""),
    clear_key: str = Form("off"),
    # 网页自动化（元宝）配置
    wa_url: str = Form(""),
    wa_input: str = Form(""),
    wa_send: str = Form(""),
    wa_answer: str = Form(""),
    wa_login: str = Form(""),
    wa_headless: str = Form("off"),
    db: Session = Depends(get_db),
):
    m = db.get(LLMModel, mid)
    if m:
        m.enabled = enabled == "on"
        cfg = dict(m.config or {})
        if m.adapter_type == "web_automation":
            # 元宝：保存选择器等到 config（空值也保存，方便清空）
            cfg.update(
                {
                    "url": wa_url.strip() or None,
                    "input_selector": wa_input.strip() or "textarea",
                    "send_selector": wa_send.strip() or None,
                    "answer_selector": wa_answer.strip() or None,
                    "login_selector": wa_login.strip() or None,
                    "headless": wa_headless == "on",
                }
            )
            m.config = cfg
        else:
            m.base_url = base_url or None
            m.model_name = model_name or None
            try:
                m.concurrency = max(1, int(concurrency))
            except ValueError:
                pass
            # 密钥：填了则更新；勾选清除则删除；都没有则保持不变
            if clear_key == "on":
                cfg.pop("api_key", None)
                m.config = cfg
            elif api_key.strip():
                cfg["api_key"] = api_key.strip()
                m.config = cfg
        db.commit()
    return RedirectResponse("/models", status_code=303)


@router.post("/models/{mid}/test", dependencies=[Depends(require_admin)])
def test_model(
    mid: int,
    base_url: str = Form(""),
    model_name: str = Form(""),
    api_key: str = Form(""),
    db: Session = Depends(get_db),
):
    """连通测试：用卡片当前值（回退已存配置/环境变量）发一条最小提问，返回结果 JSON。

    便于保存前先验证。前端用 fetch 调用并把结果显示在卡片上。
    """
    m = db.get(LLMModel, mid)
    if m is None:
        return {"ok": False, "msg": "模型不存在"}
    if m.adapter_type == "web_automation":
        from app.adapters.web_automation import WebAutomationAdapter

        if not (m.config or {}).get("url") or not (m.config or {}).get("answer_selector"):
            return {"ok": False, "msg": "元宝未配置：需填写 url 与 answer_selector 并保存后再测"}
        adapter = WebAutomationAdapter(m.model_key, m.model_name, m.config)
        result = asyncio.run(adapter.ask("连通测试，请回复：OK", timeout=45))
        if result.status == "success":
            snippet = (result.answer_text or "").strip().replace("\n", " ")[:40]
            return {"ok": True, "msg": f"连通 {result.latency_ms}ms · 回复：{snippet}"}
        return {"ok": False, "msg": f"失败：{(result.error_msg or '未知错误')[:120]}"}

    cfg = m.config or {}
    key = api_key.strip() or cfg.get("api_key") or os.getenv(m.api_key_env or "", "")
    base = base_url.strip() or m.base_url
    name = model_name.strip() or m.model_name
    if not (key and base and name):
        return {"ok": False, "msg": "缺少 base_url / model_name / 密钥，无法测试"}

    adapter = OpenAICompatibleAdapter(m.model_key, base, key, name)
    result = asyncio.run(adapter.ask("连通测试，请只回复：OK", timeout=20))
    if result.status == "success":
        snippet = (result.answer_text or "").strip().replace("\n", " ")[:40]
        return {"ok": True, "msg": f"连通 {result.latency_ms}ms · 回复：{snippet}"}
    return {"ok": False, "msg": f"失败：{(result.error_msg or '未知错误')[:100]}"}
