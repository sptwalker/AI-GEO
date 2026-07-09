"""ORM 模型：题库 / 标准答案 / 模型配置 / 批次 / 问答日志 / 判定结果。

ponytail: 6 张小表集中在一个文件（清晰分节）比每表一文件更省事、可读；
「模块化」体现在 routers / services / models 三层解耦，而非把小表拆散。

按已确认决策：qa_log 不长期存储原始报文（raw_response），仅保留回答正文与
token 用量等元数据。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# 长文本：MySQL 用 LONGTEXT，其它库（如测试用 sqlite）回退 TEXT
LongText = Text().with_variant(LONGTEXT, "mysql")
# 主键：MySQL 用 BIGINT 自增；sqlite 只有 INTEGER PRIMARY KEY 才自增，故回退 Integer
BigIntPk = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    """统一的创建/更新时间戳。"""

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Question(TimestampMixin, Base):
    """题库：产品相关提问。"""

    __tablename__ = "question"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text)  # 问题正文
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    product: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    remark: Mapped[str | None] = mapped_column(String(255))

    answers: Mapped[list["StandardAnswer"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class StandardAnswer(TimestampMixin, Base):
    """标准答案库：与题目关联的权威口径，保留历史版本（is_active 标当前）。"""

    __tablename__ = "standard_answer"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("question.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(LongText)
    source: Mapped[str | None] = mapped_column(String(255))  # 权威来源
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_by: Mapped[str | None] = mapped_column(String(64))

    question: Mapped["Question"] = relationship(back_populates="answers")


class LLMModel(TimestampMixin, Base):
    """大模型配置。密钥不入库，仅存读取密钥的环境变量名（api_key_env）。"""

    __tablename__ = "llm_model"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    model_key: Mapped[str] = mapped_column(String(32), unique=True)  # deepseek/kimi/...
    display_name: Mapped[str] = mapped_column(String(64))
    adapter_type: Mapped[str] = mapped_column(
        String(32), default="openai_compatible"
    )  # openai_compatible / web_automation(二期)
    base_url: Mapped[str | None] = mapped_column(String(255))
    model_name: Mapped[str | None] = mapped_column(String(128))
    api_key_env: Mapped[str | None] = mapped_column(String(64))
    concurrency: Mapped[int] = mapped_column(Integer, default=4)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    config: Mapped[dict | None] = mapped_column(JSON)  # 每模型差异化参数


class RunBatch(TimestampMixin, Base):
    """一次批量监控运行。"""

    __tablename__ = "run_batch"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual")  # manual/scheduled
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending/running/done/failed
    judge_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(64))


class QALog(TimestampMixin, Base):
    """问答日志：某模型对某问题的一次回答。"""

    __tablename__ = "qa_log"
    __table_args__ = (
        Index("ix_qa_log_q_m_t", "question_id", "model_id", "asked_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("run_batch.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[int] = mapped_column(BigInteger, index=True)
    model_id: Mapped[int] = mapped_column(BigInteger, index=True)
    question_snapshot: Mapped[str] = mapped_column(Text)  # 提问时的问题快照
    answer_text: Mapped[str | None] = mapped_column(LongText)
    asked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="success")  # success/failed
    error_msg: Mapped[str | None] = mapped_column(String(512))
    token_usage: Mapped[dict | None] = mapped_column(JSON)

    eval: Mapped["EvalResult | None"] = relationship(
        back_populates="qa_log", uselist=False, cascade="all, delete-orphan"
    )


class EvalResult(TimestampMixin, Base):
    """判定结果：DeepSeek 裁判对某条回答的正确性/污染/偏差评估。"""

    __tablename__ = "eval_result"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    qa_log_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("qa_log.id", ondelete="CASCADE"), unique=True, index=True
    )
    judge_model: Mapped[str] = mapped_column(String(32))
    standard_answer_snapshot: Mapped[str | None] = mapped_column(LongText)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    correctness_score: Mapped[int | None] = mapped_column(Integer)
    pollution_level: Mapped[str | None] = mapped_column(
        String(16), index=True
    )  # none/low/medium/high/severe
    deviation_level: Mapped[str | None] = mapped_column(
        String(16)
    )  # none/minor/moderate/major
    verdict: Mapped[str | None] = mapped_column(String(16), index=True)  # pass/warn/fail
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSON)
    overridden: Mapped[bool] = mapped_column(Boolean, default=False)  # 人工复核覆盖标记
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)

    qa_log: Mapped["QALog"] = relationship(back_populates="eval")


class ScheduleConfig(TimestampMixin, Base):
    """定时任务配置：cron + 题目范围 + 模型范围。调度器据此注册 APScheduler 作业。"""

    __tablename__ = "schedule_config"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    cron: Mapped[str] = mapped_column(String(64))  # 5 段 cron，如 "0 9 * * *"
    scope: Mapped[str] = mapped_column(String(16), default="all")  # all/category/ids
    category: Mapped[str | None] = mapped_column(String(64))
    question_ids: Mapped[list | None] = mapped_column(JSON)  # scope=ids 时使用
    model_ids: Mapped[list | None] = mapped_column(JSON)  # 空=全部启用模型
    judge_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[str | None] = mapped_column(String(64))


class AlertLog(TimestampMixin, Base):
    """报警记录：一次报警的内容与发送状态，便于排查。"""

    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    batch_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    level: Mapped[str] = mapped_column(String(16), default="warning")
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16), default="feishu")
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent/failed/skipped
    error_msg: Mapped[str | None] = mapped_column(String(512))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class User(TimestampMixin, Base):
    """后台用户（M4）。role：admin=全权，viewer=只读（可查询/导出，不能改动）。"""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    salt: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="viewer", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
