"""Pydantic schemas：请求/响应与判定结构（M5 增强：语义/事实/污染类型/风险分级/一致性）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PollutionLevel = Literal["none", "low", "medium", "high", "severe"]
DeviationLevel = Literal["none", "minor", "moderate", "major"]
Verdict = Literal["pass", "warn", "fail"]
RiskLevel = Literal["normal", "minor", "moderate", "severe"]  # M5 四级风险
PollutionType = Literal["false_info", "defamation", "rumor", "exaggeration"]

RISK_ORDER = ["normal", "minor", "moderate", "severe"]  # 由轻到重
RISK_LABEL_CN = {
    "normal": "正常",
    "minor": "轻微偏差",
    "moderate": "中度偏移",
    "severe": "严重污染/错误",
}


class EvalOutput(BaseModel):
    """裁判应输出的结构化判定。字段用 str（宽松）+ eval_service.normalize 归一，
    容忍不同模型返回的中英/变体取值，避免因个别字段取值不合法导致整条判定作废。"""

    risk_level: str = "normal"
    is_correct: bool = False
    correctness_score: int = Field(default=0, ge=0, le=100)
    semantic_score: int = Field(default=0, ge=0, le=100)  # 语义与标准答案契合度
    pollution_level: str = "none"
    pollution_types: list[str] = Field(default_factory=list)
    deviation_level: str = "none"
    verdict: Verdict = "warn"  # 由风险派生，保留兼容旧查询
    reason: str = ""
    logic_errors: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    distortions: list[str] = Field(default_factory=list)  # 字面近但语义歪曲
    hallucinations: list[str] = Field(default_factory=list)
    risk_notes: str = ""


class ConsistencyOutput(BaseModel):
    """口径一致性判定（多样本对比）。"""

    consistency_score: int = Field(default=100, ge=0, le=100)
    is_consistent: bool = True
    contradiction: bool = False
    reason: str = ""


class RunTrigger(BaseModel):
    name: str | None = None
    scope: Literal["all", "category", "ids"] = "all"
    category: str | None = None
    question_ids: list[int] = Field(default_factory=list)
    model_ids: list[int] = Field(default_factory=list)  # 空=全部启用模型
    judge_enabled: bool = True
    samples: int = 1  # M5 一致性采样次数（>1 开启一致性检测）


class ImportResult(BaseModel):
    created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
