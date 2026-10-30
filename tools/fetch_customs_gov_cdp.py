# -*- coding: utf-8 -*-
"""用 CDP 真人 Chrome 渲染抓取 www.customs.gov.cn（HTTP 412 风控）。

requests 直连被 WAF 412 拦截；本脚本通过 playwright 附加到已运行的真人 Chrome
（--remote-debugging-port=9222），由真实浏览器执行 JS 挑战后取 DOM。

用法：
    python tools/fetch_customs_gov_cdp.py [URL ...]
    不带参数 = 抓默认清单（进出境旅客行李物品监管页 + 首页）。

前置：CDP Chrome 必须已运行，启动命令：
    powershell -NoProfile -ExecutionPolicy Bypass -File ^
        "tools/campus_paper_fetch/campus_paper_fetch/data/scripts/start-browser.ps1"

产物：knowledge/raw/mainland_<路径>_<时间戳>.html / .txt（HTML 原文 + 正文文本）。
后续用 fetch_customs_rules.py --file <html> --source-id mainland_customs_traveler 入库。
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE / "campus_paper_fetch"))   # 让 campus_paper_fetch 包可导入

from campus_paper_fetch.browser import STEALTH_JS, get_context  # noqa: E402

DEFAULT_URLS = [
    "http://www.customs.gov.cn/customs/302249/302266/index.html",
    "http://www.customs.gov.cn/",
]

OUT_DIR = ROOT / "knowledge" / "raw"


def slug(url: str) -> str:
    s = re.sub(r"^https?://", "", url).strip("/")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:80]


async def render(url: str, wait_s: float = 15.0):
    """附加 CDP Chrome → 开新页 → 隐身脚本 → 导航 → 等挑战清除 → 取 DOM。"""
    context = await get_context()
    page = await context.new_page()
    try:
        await page.add_init_script(STEALTH_JS)
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        status = resp.status if resp else None
        # 给 JS 风控挑战留时间
        deadline = time.time() + wait_s
        while time.time() < deadline:
            await asyncio.sleep(1)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        title = await page.title()
        html = await page.evaluate("document.documentElement.outerHTML")
        text = await page.evaluate("document.body ? document.body.innerText : ''")
        return status, title, html, text
    finally:
        await page.close()


async def main() -> int:
    urls = sys.argv[1:] or DEFAULT_URLS
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for url in urls:
        ts = time.strftime("%Y%m%d_%H%M%S")
        print("== fetch %s" % url)
        try:
            status, title, html, text = await render(url)
        except Exception as exc:
            print("[FAIL] %s -> %s" % (url, str(exc)[:300]))
            continue
        flag = ""
        if status == 412 or "412" in (title or "") or len(html) < 5000:
            flag = " [WARN] 疑似仍被风控（412/页面过小），请检查保存的 HTML"
        print("[OK] HTTP %s | title=%s | html=%d chars | text=%d chars%s"
              % (status, (title or "")[:40], len(html), len(text), flag))
        stem = "%s_%s" % (slug(url), ts)
        (OUT_DIR / (stem + ".html")).write_text(html, encoding="utf-8")
        (OUT_DIR / (stem + ".txt")).write_text(text, encoding="utf-8")
        print("      saved: knowledge/raw/%s.html / .txt" % stem)
        ok += 1
    print("== done: %d/%d pages rendered" % (ok, len(urls)))
    return 0 if ok else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
