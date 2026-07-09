"""网页自动化适配器（元宝等无 API 模型）。

用 Playwright 持久化上下文：首次由 scripts/yuanbao_login.py 手动登录一次，登录态
存 user_data_dir，之后无头复用。选择器随各站点页面而变，故全部可在「模型配置」页
按需填写（存 llm_model.config）。

ponytail: 每次提问启动一次持久化上下文（简单、无泄漏），量大时再改为浏览器复用池；
同一适配器内用锁串行化，避免并发操作同一登录态目录导致冲突。
"""
from __future__ import annotations

import asyncio
import time

from app.adapters.base import AnswerResult, BaseAdapter

# 配置项默认值（可在模型配置页覆盖）
_DEFAULTS = {
    "input_selector": "textarea",
    "headless": True,
    "poll_interval": 1.0,   # 秒：轮询回答是否还在生成
    "stable_rounds": 2,     # 连续多少轮文本不变视为生成完成
}


class WebAutomationAdapter(BaseAdapter):
    def __init__(self, model_key: str, model_name: str | None, config: dict | None = None):
        super().__init__(model_key, model_name)
        self.config = {**_DEFAULTS, **(config or {})}
        self._lock = asyncio.Lock()  # 串行化：同一站点登录态一次只跑一个提问

    async def ask(self, question: str, *, timeout: float = 60.0) -> AnswerResult:
        cfg = self.config
        url = cfg.get("url")
        answer_sel = cfg.get("answer_selector")
        if not url or not answer_sel:
            return AnswerResult(
                status="failed",
                error_msg="网页自动化未配置：需在模型配置页填写 url 与 answer_selector",
            )
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return AnswerResult(
                status="failed",
                error_msg="未安装 playwright：pip install playwright 且 playwright install chromium",
            )

        user_data_dir = cfg.get("user_data_dir") or f".playwright/{self.model_key}"
        input_sel = cfg["input_selector"]
        send_sel = cfg.get("send_selector")
        login_sel = cfg.get("login_selector")
        started = time.monotonic()

        async with self._lock:
            try:
                async with async_playwright() as p:
                    ctx = await p.chromium.launch_persistent_context(
                        user_data_dir, headless=bool(cfg.get("headless", True))
                    )
                    try:
                        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                        await page.goto(
                            url, timeout=int(timeout * 1000), wait_until="domcontentloaded"
                        )
                        # 若配置了登录标识且可见，说明登录态失效
                        if login_sel and await page.locator(login_sel).count() > 0:
                            if await page.locator(login_sel).first.is_visible():
                                return AnswerResult(
                                    status="failed",
                                    latency_ms=int((time.monotonic() - started) * 1000),
                                    error_msg="登录态失效，请重新运行登录脚本 scripts/yuanbao_login.py",
                                )
                        await page.fill(input_sel, question, timeout=int(timeout * 1000))
                        if send_sel:
                            await page.click(send_sel)
                        else:
                            await page.keyboard.press("Enter")
                        answer = await self._wait_answer(page, answer_sel, timeout)
                    finally:
                        await ctx.close()
                latency = int((time.monotonic() - started) * 1000)
                if not answer:
                    return AnswerResult(
                        status="failed",
                        latency_ms=latency,
                        error_msg="未获取到回答（可能未登录或 answer_selector 不匹配）",
                    )
                return AnswerResult(answer_text=answer, latency_ms=latency)
            except Exception as exc:  # noqa: BLE001 统一降级，不打断批次
                return AnswerResult(
                    status="failed",
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_msg=str(exc)[:300],
                )

    async def _wait_answer(self, page, answer_sel: str, timeout: float) -> str:
        """轮询最后一个 answer 元素文本，直至连续 stable_rounds 轮不变（应对流式生成）。"""
        interval = float(self.config["poll_interval"])
        need_stable = int(self.config["stable_rounds"])
        deadline = time.monotonic() + timeout
        last, stable = "", 0
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            els = await page.query_selector_all(answer_sel)
            cur = (await els[-1].inner_text()).strip() if els else ""
            if cur and cur == last:
                stable += 1
                if stable >= need_stable:
                    return cur
            else:
                stable = 0
                last = cur
        return last
