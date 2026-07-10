"""启动引导：建表 + 初始化模型配置（幂等）。应用启动与手动脚本共用。

模型的 base_url / model_name 以接入时官方文档为准，可在「模型配置」页调整。
按 M1 决策：DeepSeek + Kimi + 通义 默认启用（无密钥则自动跳过），其余默认关闭。
"""
from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import LLMModel, User  # noqa: F401  确保模型注册到 Base.metadata
from app.security import hash_password

DEFAULT_MODELS: list[dict] = [
    dict(
        model_key="deepseek",
        display_name="DeepSeek",
        base_url="https://api.deepseek.com",
        model_name="deepseek-chat",
        api_key_env="DEEPSEEK_API_KEY",
        enabled=True,
    ),
    dict(
        model_key="kimi",
        display_name="Kimi (Moonshot)",
        base_url="https://api.moonshot.cn/v1",
        model_name="moonshot-v1-8k",
        api_key_env="KIMI_API_KEY",
        enabled=True,
    ),
    dict(
        model_key="tongyi",
        display_name="通义千问",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen-plus",
        api_key_env="TONGYI_API_KEY",
        enabled=True,
    ),
    dict(
        model_key="doubao",
        display_name="豆包 (火山方舟)",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model_name="",  # Ark 用推理接入点 ID，需在模型配置页填写
        api_key_env="DOUBAO_API_KEY",
        enabled=False,
    ),
    dict(
        model_key="wenxin",
        display_name="文心一言",
        base_url="https://qianfan.baidubce.com/v2",
        model_name="ernie-4.0-8k",
        api_key_env="WENXIN_API_KEY",
        enabled=False,
    ),
    dict(
        model_key="xinghuo",
        display_name="讯飞星火",
        base_url="https://spark-api-open.xf-yun.com/v1",
        model_name="generalv3.5",
        api_key_env="XINGHUO_API_KEY",
        enabled=False,
    ),
    dict(
        model_key="yuanbao",
        display_name="腾讯元宝 (网页自动化)",
        adapter_type="web_automation",
        enabled=False,
        config={
            # 元宝联调所得默认值（页面改版可能需在模型配置页调整）；仍需先跑登录脚本
            "url": "https://yuanbao.tencent.com",
            "input_selector": ".ql-editor",
            "answer_selector": ".agent-chat__bubble--ai",
            "headless": True,
            "answer_strip": [
                r"您正在提供关于.*?反馈",
                r"您更喜欢哪个回答",
                r"我更喜欢这个回答",
                r"回答\s*[12]\b",
                r"内容由AI生成[，,][^\n]*",
            ],
        },
    ),
]


def ensure_schema_and_seed() -> None:
    """建表 + 补齐默认模型 + 播种管理员（均幂等，不覆盖已有）。

    AUTO_CREATE_TABLES=false 时跳过建表（交给 Alembic 迁移管理），仅做数据播种。
    """
    if settings.auto_create_tables:
        Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        existing = {m.model_key for m in db.scalars(select(LLMModel))}
        for spec in DEFAULT_MODELS:
            if spec["model_key"] not in existing:
                db.add(LLMModel(**spec))
        # 播种初始管理员（仅当无任何用户时），口令取环境变量
        if db.scalar(select(User).limit(1)) is None:
            pw_hash, salt = hash_password(settings.admin_password)
            db.add(
                User(
                    username=settings.admin_username,
                    password_hash=pw_hash,
                    salt=salt,
                    role="admin",
                    enabled=True,
                )
            )
        db.commit()
    finally:
        db.close()
