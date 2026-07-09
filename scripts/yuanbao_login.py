"""元宝（及其它网页自动化模型）首次登录脚本。

用法（先在「模型配置」页保存好 url，或用 --url 传入）：
    python -m scripts.yuanbao_login                 # 用 DB 中 yuanbao 的 url
    python -m scripts.yuanbao_login --model kimi_web --url https://...

流程：打开有头浏览器 + 持久化上下文（user_data_dir），你手动扫码/短信登录后，
回到终端按回车即保存登录态并退出。之后适配器无头复用该 user_data_dir。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yuanbao", help="模型 model_key（默认 yuanbao）")
    ap.add_argument("--url", default="", help="登录/聊天页 url，留空则读取该模型配置")
    args = ap.parse_args()

    url = args.url
    user_data_dir = None
    # 只有未显式传 --url 时才查库拿配置，避免无数据库环境（本地未起 MySQL）也报错
    if not url:
        from sqlalchemy import select

        from app.database import SessionLocal
        from app.models import LLMModel

        db = SessionLocal()
        m = db.scalar(select(LLMModel).where(LLMModel.model_key == args.model))
        cfg = (m.config if m else None) or {}
        url = cfg.get("url")
        user_data_dir = cfg.get("user_data_dir")
        db.close()
    user_data_dir = user_data_dir or f".playwright/{args.model}"
    if not url:
        sys.exit("未提供 url：用 --url 传入，或先在模型配置页填 url")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("未安装 playwright：pip install playwright && playwright install chromium")

    print(f"打开浏览器登录 {args.model} -> {url}\n登录态将保存到 {user_data_dir}")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url)
        input("\n>>> 在浏览器里完成登录后，回到这里按【回车】保存并退出...")
        try:
            ctx.close()
        except Exception:  # noqa: BLE001 手动关窗口会致 close 报错，登录态已落盘，忽略即可
            pass
    print("登录态已保存，之后适配器会无头复用。")


if __name__ == "__main__":
    main()
