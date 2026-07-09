"""元宝页面结构探测：复用已保存登录态，打开聊天页，自动打印可用于选择器的候选。

用法：
    python -m scripts.yuanbao_probe --url https://yuanbao.tencent.com/chat/xxxx

它会：①打印输入框/按钮候选；②自动填一句并回车尝试发送；③打印"回答容器"候选。
把终端输出贴回即可据此确定 input_selector / send_selector / answer_selector。
无需数据库。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# 在页面里生成简短选择器 + 各类候选，注入执行
_JS_SEL = """
const sel = el => el.id ? '#'+el.id
  : (el.tagName.toLowerCase() + (typeof el.className==='string' && el.className.trim()
     ? '.'+el.className.trim().split(/\\s+/).slice(0,2).join('.') : ''));
"""
_JS_INPUTS = _JS_SEL + """
return [...document.querySelectorAll('textarea,[contenteditable="true"],[contenteditable=""]')]
  .map(el=>({sel:sel(el), tag:el.tagName, ph:el.getAttribute('placeholder')||'', vis:!!el.offsetParent}));
"""
_JS_BUTTONS = _JS_SEL + """
return [...document.querySelectorAll('button,[role="button"]')]
  .map(el=>({sel:sel(el), text:(el.innerText||el.getAttribute('aria-label')||el.title||'').trim().slice(0,24), svg:!!el.querySelector('svg'), vis:!!el.offsetParent}))
  .filter(b=>b.vis && (b.text||b.svg));
"""
_JS_ANSWERS = _JS_SEL + """
return [...document.querySelectorAll('div,p,section,article,li')]
  .filter(el=>{const t=el.innerText||''; return t.length>15 && t.length<3000 && el.children.length<30 && el.offsetParent;})
  .slice(-15)
  .map(el=>({sel:sel(el), len:(el.innerText||'').length, text:(el.innerText||'').replace(/\\s+/g,' ').slice(0,70)}));
"""


def _dump(title: str, rows: list[dict]) -> None:
    print(f"\n===== {title}（{len(rows)}）=====")
    for r in rows:
        if "text" in r and "len" in r:  # answer 候选
            print(f"  {r['sel']:<38} len={r['len']:<4} | {r['text']}")
        elif "ph" in r:  # input 候选
            print(f"  {r['sel']:<38} tag={r['tag']:<10} placeholder={r['ph']!r}")
        else:  # button 候选
            print(f"  {r['sel']:<38} text={r['text']!r} svg={r['svg']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="yuanbao")
    ap.add_argument("--url", required=True, help="登录后的聊天页真实 URL")
    ap.add_argument("--ask", default="你好，请只回复：OK")
    args = ap.parse_args()

    user_data_dir = f".playwright/{args.model}"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("未安装 playwright：pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(user_data_dir, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_timeout(3500)  # 等 SPA 渲染

        _dump("输入框候选 input_selector", page.evaluate("() => {" + _JS_INPUTS + "}"))
        _dump("按钮候选 send_selector", page.evaluate("() => {" + _JS_BUTTONS + "}"))

        # 自动尝试发一句：优先 textarea，否则 contenteditable
        try:
            box = page.locator("textarea")
            if box.count() == 0:
                box = page.locator('[contenteditable="true"], [contenteditable=""]')
            box.first.click()
            box.first.fill(args.ask)
            page.keyboard.press("Enter")
            print(f"\n>>> 已尝试自动发送：{args.ask}（等待回答…）")
            page.wait_for_timeout(9000)
        except Exception as exc:  # noqa: BLE001
            print(f"\n>>> 自动发送未成功（{exc}）——请在浏览器里手动发一句")

        _dump("回答容器候选 answer_selector（越靠后越可能是最新回答）",
              page.evaluate("() => {" + _JS_ANSWERS + "}"))

        input("\n>>> 若上面没抓到回答：在浏览器手动发一条、等答完，再按回车重抓（否则直接回车关闭）...")
        _dump("回答容器候选（重抓）", page.evaluate("() => {" + _JS_ANSWERS + "}"))
        ctx.close()


if __name__ == "__main__":
    main()
