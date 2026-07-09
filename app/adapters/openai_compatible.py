"""OpenAI 兼容适配器。

一套逻辑覆盖 DeepSeek / Kimi / 通义 / 豆包 / 文心(v2) / 星火 等——它们的兼容
接口都是 POST {base_url}/chat/completions + Bearer 鉴权。个别平台的额外 header
通过 extra_headers 注入，主体逻辑复用。
"""
from __future__ import annotations

import time

import httpx

from app.adapters.base import AnswerResult, BaseAdapter


class OpenAICompatibleAdapter(BaseAdapter):
    def __init__(
        self,
        model_key: str,
        base_url: str,
        api_key: str,
        model_name: str,
        extra_headers: dict | None = None,
    ) -> None:
        super().__init__(model_key, model_name)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra_headers = extra_headers or {}

    async def ask(self, question: str, *, timeout: float = 60.0) -> AnswerResult:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": question}],
            "temperature": 0.3,
            "stream": False,
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
            latency = int((time.monotonic() - started) * 1000)
            if resp.status_code != 200:
                return AnswerResult(
                    status="failed",
                    latency_ms=latency,
                    error_msg=f"HTTP {resp.status_code}: {resp.text[:300]}",
                )
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return AnswerResult(
                answer_text=content, latency_ms=latency, token_usage=data.get("usage")
            )
        except Exception as exc:  # noqa: BLE001 网络/解析异常统一降级为 failed，不打断批次
            latency = int((time.monotonic() - started) * 1000)
            return AnswerResult(status="failed", latency_ms=latency, error_msg=str(exc)[:300])
