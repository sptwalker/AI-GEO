"""Pydantic schemas：请求/响应与判定结构。

页面多用表单提交（routers 里直接读 Form），故 schema 保持精简；此处主要给
JSON 接口与判定结果解析复用。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---- 判定结果结构（eval_service 解析裁判输出用）----

PollutionLevel = Literal["none", "low", "medium", "high", "severe"]
DeviationLevel = Literal["none", "minor", "moderate", "major"]
Verdict = Literal["pass", "warn", "fail"]


class EvalOutput(BaseModel):
    """裁判模型应输出的结构化判定。字段带默认值，容忍裁判少给字段。"""

    is_correct: bool = False
    correctness_score: int = Field(default=0, ge=0, le=100)
    pollution_level: PollutionLevel = "none"
    deviation_level: DeviationLevel = "none"
    verdict: Verdict = "warn"
    reason: str = ""
    matched_points: list[str] = Field(default_factory=list)
    missing_points: list[str] = Field(default_factory=list)
    hallucinations: list[str] = Field(default_factory=list)
    risk_notes: str = ""


# ---- 运行触发 ----


class RunTrigger(BaseModel):
    name: str | None = None
    scope: Literal["all", "category", "ids"] = "all"
    category: str | None = None
    question_ids: list[int] = Field(default_factory=list)
    model_ids: list[int] = Field(default_factory=list)  # 空=全部启用模型
    judge_enabled: bool = True


# ---- 批量导入结果 ----


class ImportResult(BaseModel):
    created: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)
