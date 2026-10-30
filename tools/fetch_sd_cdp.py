"""ScienceDirect PDF fetcher: intercept the PDF body inside the browser's own
network stack via CDP Fetch domain.

Why the previous attempts failed:
  * ``ctx.request.get(presigned_url)`` -> 200 text/html, 62 KB: Elsevier serves a
    "Security verification / JavaScript is disabled" interstitial to any client
    that is not the real browser, even on a presigned S3 URL.
  * ``resp.body()`` on the PDF response -> the built-in viewer consumes it.
  * ``fetch()`` inside the popup -> a PDF document has no script context.

The only reliable source is ``Fetch.getResponseBody`` at the Response stage,
which must be armed *before* the navigation starts.

Route: DOI landing page -> real pdfft href -> new page (CDP armed) -> navigate
with the article page as Referer -> crasolve bridge -> presigned URL -> bytes.
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
PATTERN = "*pdf.sciencedirectassets.com*"
PII_RE = re.compile(r"/pii/([A-Z0-9]+)")


def target_path(doi: str) -> Path:
    base = doi.replace("/", "_")
    digest = hashlib.sha256(doi.encode()).hexdigest()[:8]
    return OUT / f"{base}_{digest}.pdf"


async def find_pdfft(ctx, doi: str):
    page = await ctx.new_page()
    try:
        await page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_selector("a[href*='/pdfft?']", timeout=25000)
        except Exception:
            await page.mouse.wheel(0, 1600)
            await page.wait_for_timeout(2500)
        anchor = page.locator("a[href*='/pdfft?']").first
        if await anchor.count() > 0:
            return await anchor.get_attribute("href"), page.url, None
        # 新版文章页把 PDF 入口做成 <button>View PDF</button>，没有 href，
        # 但 URL 里仍带 PII，可直接构造等价入口；权限仍由页面的
        # "Brought to you by" 机构标识决定，无权限时会落到 purchase 页而非 PDF。
        m = PII_RE.search(page.url)
        if m:
            href = (f"/science/article/pii/{m.group(1)}"
                    "/pdfft?isDTMRedir=true&download=true")
            return href, page.url, None
        return None, page.url, "no_anchor"
    finally:
        await page.close()


async def grab(ctx, href: str, referer: str, doi: str) -> dict:
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    await cdp.send("Network.enable", {
        "maxResourceBufferSize": 100 * 1024 * 1024,
        "maxTotalBufferSize": 200 * 1024 * 1024,
    })
    await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": PATTERN,
                                                  "requestStage": "Response"}]})
    state = {}
    done = asyncio.Event()

    def on_paused(ev):
        async def handle():
            rid = ev["requestId"]
            try:
                res = await cdp.send("Fetch.getResponseBody", {"requestId": rid})
                raw = res.get("body", "")
                state["bytes"] = (base64.b64decode(raw) if res.get("base64Encoded")
                                  else raw.encode("latin-1"))
                state["url"] = (ev.get("request") or {}).get("url", "")[:120]
            except Exception as exc:
                state["error"] = f"{type(exc).__name__}: {exc}"[:200]
            finally:
                for method in ("Fetch.continueResponse", "Fetch.continueRequest"):
                    try:
                        await cdp.send(method, {"requestId": rid})
                        break
                    except Exception:
                        continue
                done.set()

        asyncio.get_event_loop().create_task(handle())

    cdp.on("Fetch.requestPaused", on_paused)

    nav = "ok"
    try:
        await page.goto(href, referer=referer, wait_until="commit", timeout=90000)
    except Exception as exc:
        nav = f"{type(exc).__name__}: {exc}"[:160]

    try:
        await asyncio.wait_for(done.wait(), timeout=60)
    except asyncio.TimeoutError:
        state.setdefault("error", "timeout waiting for Fetch.requestPaused")

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
    if payload and payload.startswith(b"%PDF"):
        dest = target_path(doi)
        dest.write_bytes(payload)
        return {"doi": doi, "status": "ok", "path": str(dest).replace("\\", "/"),
                "size": len(payload), "pages": payload.count(b"/Type /Page"),
                "via": state.get("url", "")}
    return {"doi": doi, "status": "not_pdf", "nav": nav, "final_url": final_url,
            "size": len(payload) if payload else 0,
            "error": state.get("error"), "via": state.get("url", "")}


async def fetch_one(ctx, doi: str) -> dict:
    href, article_url, why = await find_pdfft(ctx, doi)
    if why:
        return {"doi": doi, "status": why, "url": article_url}
    if href.startswith("/"):
        href = "https://www.sciencedirect.com" + href
    return await grab(ctx, href, article_url, doi)


async def main(dois):
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
        for doi in dois:
            try:
                res = await fetch_one(ctx, doi)
            except Exception as exc:
                res = {"doi": doi, "status": "fatal",
                       "error": f"{type(exc).__name__}: {exc}"[:200]}
            results.append(res)
            print(res, flush=True)
        await browser.close()
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nSUMMARY ok={ok}/{len(dois)}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
