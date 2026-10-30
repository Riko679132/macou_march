"""通用 CDP PDF 抓取器：不依赖特定出版社的锚点/直链。

思路与 fetch_sd_cdp.py 一致 —— 在浏览器网络栈内用 Fetch 域（Response 阶段）
截获响应体，因为 PDF viewer 会把 body 消费掉，页面脚本也拿不到。

差别：这里不预先知道 PDF 的 URL 形态，所以拦截 *pdf* 通配，并且只在响应头
Content-Type 含 "application/pdf" 时才取 body；其余请求直接放行。

用法: tools\.venv\Scripts\python.exe tools\fetch_pdf_cdp.py URL_OR_DOI [ ... ]
  传入 DOI（以 10. 开头）时按 ACS 的 /doi/pdf/{doi} 规则构造目标 URL。
"""
import asyncio
import base64
import hashlib
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path("data/raw_pdfs")
CDP = "http://127.0.0.1:9222"
PATTERN = "*pdf*"


def target_path(doi: str) -> Path:
    # target 可能是完整 URL（如 ACS 的 SI 直链），其中的 ":" 在 Windows 上会被
    # 当成盘符分隔符，导致写文件时静默创建一个名为 https 的空文件。统一清洗。
    base = re.sub(r'[<>:"/\\|?*]', "_", doi)[:80]
    digest = hashlib.sha256(doi.encode()).hexdigest()[:8]
    return OUT / f"{base}_{digest}.pdf"


def resolve(target: str):
    """返回 (navigation_url, doi_or_slug)。"""
    if target.startswith("10.1021/"):
        # ACS 的 PDF 入口是 /doi/pdf/{doi}；走 doi.org 只会落到文章页，
        # 页面里的 "PDF" 是按钮而非链接，导航过去拿不到响应。
        return f"https://pubs.acs.org/doi/pdf/{target}?download=true", target
    if target.startswith("10."):
        return f"https://doi.org/{target}", target
    return target, target


def is_pdf_response(headers) -> bool:
    for h in headers or []:
        if (h.get("name") or "").lower() == "content-type":
            return "application/pdf" in (h.get("value") or "").lower()
    return False


async def grab(ctx, url: str, label: str) -> dict:
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    await cdp.send("Network.enable", {
        "maxResourceBufferSize": 200 * 1024 * 1024,
        "maxTotalBufferSize": 400 * 1024 * 1024,
    })
    await cdp.send("Fetch.enable", {"patterns": [
        {"urlPattern": PATTERN, "requestStage": "Response"},
    ]})
    state = {}
    done = asyncio.Event()

    def on_paused(ev):
        async def handle():
            rid = ev["requestId"]
            try:
                if not is_pdf_response(ev.get("responseHeaders")):
                    return
                res = await cdp.send("Fetch.getResponseBody", {"requestId": rid})
                raw = res.get("body", "")
                body = (base64.b64decode(raw) if res.get("base64Encoded")
                        else raw.encode("latin-1"))
                if body.startswith(b"%PDF"):
                    state["bytes"] = body
                    state["url"] = (ev.get("request") or {}).get("url", "")[:120]
            except Exception as exc:
                state.setdefault("error", f"{type(exc).__name__}: {exc}"[:200])
            finally:
                try:
                    await cdp.send("Fetch.continueResponse", {"requestId": rid})
                except Exception:
                    try:
                        await cdp.send("Fetch.continueRequest", {"requestId": rid})
                    except Exception:
                        pass
                if "bytes" in state:
                    done.set()

        asyncio.get_event_loop().create_task(handle())

    cdp.on("Fetch.requestPaused", on_paused)

    nav = "ok"
    try:
        await page.goto(url, wait_until="commit", timeout=90000)
    except Exception as exc:
        nav = f"{type(exc).__name__}: {exc}"[:160]

    try:
        await asyncio.wait_for(done.wait(), timeout=75)
    except asyncio.TimeoutError:
        state.setdefault("error", "timeout waiting for pdf response")

    try:
        await cdp.send("Fetch.disable")
    except Exception:
        pass
    final_url = page.url
    try:
        await page.close()
    except Exception:
        pass

    payload = state.get("bytes")
    if payload:
        dest = target_path(label)
        dest.write_bytes(payload)
        return {"target": label, "status": "ok", "path": str(dest).replace("\\", "/"),
                "size": len(payload), "pages": payload.count(b"/Type /Page"),
                "via": state.get("url", "")}
    return {"target": label, "status": "not_pdf", "nav": nav,
            "final_url": final_url, "error": state.get("error"),
            "via": state.get("url", "")}


async def main(targets):
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP)
        except Exception as exc:
            raise SystemExit(
                f"CDP not reachable at {CDP} ({type(exc).__name__}).\n"
                "Start the browser first (Terminal 1, keep it running):\n"
                "  powershell -NoProfile -ExecutionPolicy Bypass -File "
                "\"tools/.venv/Lib/site-packages/campus_paper_fetch/data/scripts/start-browser.ps1\""
            )
        ctx = browser.contexts[0]
        results = []
        for t in targets:
            url, label = resolve(t)
            try:
                res = await grab(ctx, url, label)
            except Exception as exc:
                res = {"target": t, "status": "fatal",
                       "error": f"{type(exc).__name__}: {exc}"[:200]}
            results.append(res)
            print(res, flush=True)
        await browser.close()
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nSUMMARY ok={ok}/{len(targets)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
