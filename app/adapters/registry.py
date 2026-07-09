"""适配器注册表：按 llm_model 配置 + 环境变量密钥构建适配器实例。

密钥不入库：`api_key_env` 存的是环境变量名，这里用 os.getenv 读取真实密钥。
未配置密钥/信息不全的模型返回 None，被上层自动跳过。
"""
from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.base import BaseAdapter, WebAutomationAdapter
from app.adapters.openai_compatible import OpenAICompatibleAdapter
from app.models import LLMModel


def build_adapter(model: LLMModel) -> BaseAdapter | None:
    """按模型配置构建适配器；缺密钥/未知类型返回 None（跳过该模型）。"""
    if model.adapter_type == "web_automation":
        return WebAutomationAdapter(model.model_key, model.model_name)
    if model.adapter_type == "openai_compatible":
        cfg = model.config or {}
        # 密钥优先取页面保存到 DB(config) 的值，回退到环境变量
        api_key = cfg.get("api_key") or os.getenv(model.api_key_env or "", "")
        if not api_key or not model.base_url or not model.model_name:
            return None
        extra = cfg.get("extra_headers")
        return OpenAICompatibleAdapter(
            model.model_key, model.base_url, api_key, model.model_name, extra
        )
    return None


def get_enabled_adapters(
    db: Session, model_ids: list[int] | None = None
) -> dict[int, tuple[LLMModel, BaseAdapter]]:
    """返回 {model_id: (LLMModel, adapter)}，只含启用且可用（密钥齐全）的模型。"""
    stmt = select(LLMModel).where(LLMModel.enabled.is_(True))
    if model_ids:
        stmt = stmt.where(LLMModel.id.in_(model_ids))
    result: dict[int, tuple[LLMModel, BaseAdapter]] = {}
    for m in db.scalars(stmt):
        adapter = build_adapter(m)
        if adapter is not None:
            result[m.id] = (m, adapter)
    return result


def get_judge_adapter(db: Session, model_key: str) -> BaseAdapter | None:
    """构建判定裁判适配器（不要求 enabled，但需配好密钥）。"""
    m = db.scalar(select(LLMModel).where(LLMModel.model_key == model_key))
    if m is None:
        return None
    return build_adapter(m)
