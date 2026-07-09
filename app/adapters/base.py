"""接入层基类与统一数据结构。"""
from __future__ import annotations

import abc

from pydantic import BaseModel


class AnswerResult(BaseModel):
    """一次提问的标准化结果。失败也返回该结构（status=failed），不抛异常打断批次。"""

    answer_text: str = ""
    latency_ms: int = 0
    status: str = "success"  # success / failed
    error_msg: str | None = None
    token_usage: dict | None = None


class BaseAdapter(abc.ABC):
    """所有模型适配器的统一接口。"""

    def __init__(self, model_key: str, model_name: str | None = None) -> None:
        self.model_key = model_key
        self.model_name = model_name

    @abc.abstractmethod
    async def ask(self, question: str, *, timeout: float = 60.0) -> AnswerResult:
        """向模型提问并返回标准化结果。"""


class WebAutomationAdapter(BaseAdapter):
    """二期：元宝等无 API 模型的网页自动化适配器（Playwright）。

    ponytail: M1 仅占位，主流程不依赖；M3 用 Playwright 实现登录态复用 + 抓取，
    升级路径见 docs/design.md 5.3。
    """

    async def ask(self, question: str, *, timeout: float = 60.0) -> AnswerResult:
        raise NotImplementedError("网页自动化适配器将在 M3 实现（Playwright）")
