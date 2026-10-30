from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, async_playwright

from .config import get_config

_state: dict[str, Any] = {"pw": None, "context": None}
_lock = asyncio.Lock()

STEALTH_JS = """
// 降低 Cloudflare/出版社风控对自动化浏览器的识别（仅隐藏自动化特征，不绕过验证）
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5]
});
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : origQuery(parameters)
  );
}
"""

SESSION_DOMAINS = [
    "sciencedirect.com",
    "springer.com",
    "link.springer.com",
    "ieeexplore.ieee.org",
    "onlinelibrary.wiley.com",
    "tandfonline.com",
    "ascelibrary.org",
    "nature.com",
    "pubs.acs.org",
    "pubs.rsc.org",
    "pubs.aip.org",
    "icevirtuallibrary.com",
    "mdpi.com",
    "atrust.hqu.edu.cn",
]


async def _wait_challenge_clear(page, timeout_s: float = 25.0) -> bool:
    """等待 Cloudflare 托管验证自动通过（不改页面、不点击、不绕过）。超时返回 False。"""
    try:
        await page.bring_to_front()
    except Exception:
        pass
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            title = await page.title()
            url = page.url or ""
        except Exception:
            title = ""
            url = ""
        if not _is_bot_page(title, url):
            return True
        await asyncio.sleep(2)
    return False


async def get_context() -> BrowserContext:
    async with _lock:
        if _state["context"] is not None and not _state["context"].is_closed():
            return _state["context"]
        cfg = get_config()
        mode = cfg.get("browser_mode", "cdp")
        if mode == "cdp":
            cdp_url = cfg.get("cdp_url", "http://127.0.0.1:9222")
            pw = await async_playwright().start()
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=10000)
            except Exception as e:
                await pw.stop()
                raise RuntimeError(
                    f"无法附加到 Chrome（{cdp_url}）：{str(e)[:200]}。\n"
                    "cdp 模式需要先启动带调试端口的真人 Chrome：\n"
                    "  仓库脚本：~/campus-paper-fetch/scripts/start-browser.sh（或 setup.sh 安装后同目录）\n"
                    "  手动命令：\"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome\" \\\n"
                    "    --user-data-dir=\"$HOME/.campus-paper-fetch/browser-profile\" \\\n"
                    "    --remote-debugging-port=9222 --no-first-run --no-default-browser-check &\n"
                    "（Windows 用 scripts/start-browser.ps1；Linux 用 start-browser.sh 自动探测 chrome/chromium）"
                )
            contexts = browser.contexts
            if not contexts:
                await pw.stop()
                raise RuntimeError("Chrome CDP 已连接但没有可用上下文。")
            context = contexts[0]
            try:
                # CDP 附加模式：开启下载事件（某些站点以 attachment 方式给 PDF）
                await context.set_accept_downloads(True)
            except Exception:
                pass
            _state.update(pw=pw, browser=browser, context=context, mode="cdp")
            return context

        profile = Path(cfg["profile_dir"]).expanduser()
        profile.mkdir(parents=True, exist_ok=True)
        pw = await async_playwright().start()
        channel = cfg.get("browser_channel", "chrome")
        headless = bool(cfg.get("headless", False))
        launch_args = ["--disable-blink-features=AutomationControlled"]
        if bool(cfg.get("browser_no_proxy", False)):
            # 绕过系统代理直连（校园网 IP 授权场景；出版社站点走代理出口常被 CDN 风控拦截）
            launch_args.append("--no-proxy-server")
        try:
            context = await pw.chromium.launch_persistent_context(
                str(profile),
                channel=channel,
                headless=headless,
                accept_downloads=True,
                viewport={"width": 1280, "height": 900},
                args=launch_args,
            )
        except Exception:
            # 回退：使用 Playwright 自带 chromium（可能需要 playwright install chromium）
            context = await pw.chromium.launch_persistent_context(
                str(profile),
                headless=headless,
                accept_downloads=True,
                viewport={"width": 1280, "height": 900},
                args=launch_args,
            )
        await context.add_init_script(STEALTH_JS)
        _state.update(pw=pw, context=context, mode="persistent")
        return context


async def close_browser() -> None:
    async with _lock:
        # CDP 附加模式：绝不关闭用户的主 Chrome，只断开驱动
        if _state.get("mode") == "cdp":
            pw = _state.get("pw")
            _state.update(pw=None, browser=None, context=None)
            if pw is not None:
                try:
                    await pw.stop()
                except Exception:
                    pass
            return
        ctx = _state.get("context")
        if ctx is not None:
            try:
                await ctx.close()
            except Exception:
                pass
        pw = _state.get("pw")
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass
        _state.update(pw=None, context=None, browser=None, mode=None)


async def close_idle_pages(before: set | None = None) -> int:
    """关闭本次操作期间新开的页面（文章页 / PDF 查看器页），保留调用前已存在的页面。

    - before 非 None：只关闭快照之后新增的页面（批次场景，用户手动开的标签页不受影响）。
    - before 为 None：仅按工具域名清理（sciencedirect / pdf.sciencedirectassets 等）。
    返回关闭的页面数；无法连接浏览器时返回 0。
    """
    try:
        ctx = await get_context()
    except Exception:
        return 0
    closed = 0
    for p in list(ctx.pages):
        try:
            if p.is_closed():
                continue
            if before is not None and p in before:
                continue
            u = (p.url or "").lower()
            if before is None and not any(
                d in u
                for d in (
                    "sciencedirect",
                    "pdf.sciencedirectassets",
                    "linkinghub.elsevier",
                )
            ):
                continue
            await p.close()
            closed += 1
        except Exception:
            pass
    return closed


def _is_bot_page(title: str, url: str, html: str = "") -> str | None:
    t = title.lower()
    u = url.lower()
    patterns = [
        "attention required",
        "cf-browser-verification",
        "just a moment",
        "verify you are human",
        "访问验证",
        "人机验证",
        "请稍候",
        "please wait",
        "captcha",
        "challenge-platform",
        "robot detection",
        "access denied",
    ]
    for p in patterns:
        # 只按标题/URL 判断：正文 HTML 常含 Cloudflare 资源引用，全量匹配会误伤正常文章页
        if p in t or p in u:
            return "bot_check"
    if "captcha" in u or "challenge" in u:
        return "bot_check"
    return None


async def _page_fetch(context: BrowserContext, url: str, timeout_ms: int, headers: dict[str, str]) -> dict[str, Any]:
    """先用页面导航（过 JS/挑战、落 cookie），再用同会话 request 拿正文。"""
    page = await context.new_page()
    download_path: str | None = None
    pdf_bodies: list[bytes] = []
    capture_errors: list[str] = []

    async def _on_download(dl) -> None:
        nonlocal download_path
        try:
            download_path = await dl.path()
        except Exception:
            pass

    async def _on_response(resp) -> None:
        # 内嵌 PDF 查看器场景：浏览器直接以 application/pdf 拉取正文，这里截获字节
        try:
            ct = (resp.headers.get("content-type") or "").lower()
            u = resp.url or ""
            if not (
                "pdf" in ct
                or ".pdf" in u
                or "pdf.sciencedirectassets" in u
                or ct in ("application/octet-stream", "binary/octet-stream")
            ):
                return
            body = await resp.body()
            if body[:5] == b"%PDF-":
                pdf_bodies.append(body)
        except Exception:
            pass

    page.on("download", _on_download)
    page.on("response", _on_response)
    try:
        goto_err = None
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            goto_err = str(e)[:300]
        # 让 JS 挑战 / crasolve 令牌重定向 / 下载触发有时间完成（静默等待，避免卡死）
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        try:
            title = await page.title()
        except Exception:
            title = ""
        try:
            html = await page.content()
        except Exception:
            html = ""
        bot = _is_bot_page(title, page.url, html)
        if bot:
            # 托管验证可能自动通过；若需人工点击，窗口保持打开等用户完成（最多 60s）
            if not await _wait_challenge_clear(page, timeout_s=60.0):
                return {"status": "bot_check", "url": url, "title": title}
            try:
                title = await page.title()
            except Exception:
                pass

        # ScienceDirect 等站点：pdfft 先返回 JS 桥接页，跳转到带令牌的最终 PDF URL。
        # 页面 URL 已变化时优先请求最终 URL。
        candidates = [url]
        final_url = page.url or ""
        if final_url and final_url != url and not final_url.startswith(("blob:", "data:")):
            candidates.insert(0, final_url)

        req = context.request
        for cand in candidates:
            try:
                hdrs = dict(headers)
                if "pdf" in cand.lower() or "sciencedirectassets" in cand:
                    hdrs["Referer"] = "https://www.sciencedirect.com/"
                resp = await req.get(cand, headers=hdrs, timeout=timeout_ms)
                body = await resp.body()
            except Exception as e:
                goto_err = (goto_err or "") + f" req_fail:{str(e)[:120]}"
                continue
            if body[:5] == b"%PDF-":
                return {"status": "pdf", "body": body, "status_code": resp.status}
            if "pdf" in (resp.headers.get("content-type") or "").lower() and len(body) > 1000:
                return {"status": "pdf", "body": body, "status_code": resp.status}

        # 兜底 1：导航过程中浏览器直接拉取的 PDF 响应（内嵌查看器）
        if pdf_bodies:
            body = max(pdf_bodies, key=len)
            return {"status": "pdf", "body": body, "status_code": 200}

        # 兜底 2：JS 直接触发浏览器下载（accept_downloads 模式）时，读取临时文件
        if download_path:
            try:
                data = Path(download_path).read_bytes()
                if data[:5] == b"%PDF-":
                    return {"status": "pdf", "body": data, "status_code": 200}
            except Exception:
                pass

        # 兜底 3：页面里有 View PDF / Download PDF 等入口时，点进去再抓（各库按钮文案不同）
        click_selectors = [
            "a:has-text('View PDF')",
            "a:has-text('Download PDF')",
            "a:has-text('PDF')",
            "a:has-text('下载 PDF')",
            "a[href*='pdfft']",
            "a[href*='/pdf']",
            "a[href$='.pdf']",
        ]
        for sel in click_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    href = await loc.get_attribute("href")
                    if href and href.startswith("http"):
                        await page.goto(href, wait_until="domcontentloaded", timeout=timeout_ms)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:
                            pass
                        if pdf_bodies:
                            body = max(pdf_bodies, key=len)
                            return {"status": "pdf", "body": body, "status_code": 200}
                        if download_path:
                            data = Path(download_path).read_bytes()
                            if data[:5] == b"%PDF-":
                                return {"status": "pdf", "body": data, "status_code": 200}
            except Exception:
                continue

        return {
            "status": "html_not_pdf",
            "url": url,
            "status_code": resp.status,
            "content_type": resp.headers.get("content-type", ""),
            "title": title,
            "snippet": body[:400].decode("utf-8", errors="ignore").replace("\n", " ")[:400],
            "goto_error": goto_err,
        }
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def fetch_pdf_url(url: str, output_path: Path, timeout: int | None = None) -> dict[str, Any]:
    """抓取 PDF 到 output_path，返回 {status, path?, size?, error?}。

    status: pdf / bot_check / html_not_pdf / error
    """
    cfg = get_config()
    timeout = timeout or int(cfg.get("browser_timeout", 90))
    context = await get_context()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )
    }
    result = await _page_fetch(context, url, timeout * 1000, headers)
    if result.get("status") == "pdf":
        output_path.write_bytes(result["body"])
        return {"status": "pdf", "path": str(output_path), "size": len(result["body"])}
    return result


async def fetch_sciencedirect_pdf(meta: dict[str, Any], output_path: Path, timeout: int | None = None) -> dict[str, Any]:
    """ScienceDirect 专用下载流：文章页 → 点击 View PDF → 截获 PDF（等价于人工操作）。

    构造的 pdfft URL 和跨页使用 md5 链接都会触发 Cloudflare 挑战或 crasolve 桥接，
    必须与文章页同会话操作：真实点击 View PDF，监听 context 级 route/response/download
    （覆盖同页导航与新标签页），挑战等待自动放行，最多重试 3 轮。
    """
    cfg = get_config()
    timeout = timeout or int(cfg.get("browser_timeout", 90))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doi = meta.get("doi") or ""
    landing = meta.get("landing_page") or f"https://doi.org/{doi}"
    pdf_bodies: list[bytes] = []
    download_path: str | None = None
    capture_errors: list[str] = []

    async def _on_response(resp) -> None:
        try:
            ct = (resp.headers.get("content-type") or "").lower()
            u = resp.url or ""
            if not (
                "pdf" in ct
                or ".pdf" in u
                or "pdf.sciencedirectassets" in u
                or ct in ("application/octet-stream", "binary/octet-stream")
            ):
                return
            body = await resp.body()
            if body[:5] == b"%PDF-":
                pdf_bodies.append(body)
        except Exception as e:
            capture_errors.append(f"{type(e).__name__}: {str(e)[:120]}")

    async def _on_download(dl) -> None:
        nonlocal download_path
        try:
            download_path = await dl.path()
        except Exception:
            pass

    async def _open_landing(ctx, p) -> str:
        """打开文章页并等待挑战放行，返回页面标题（空串表示页面已坏）。"""
        try:
            await p.goto(landing, wait_until="domcontentloaded", timeout=timeout * 1000)
        except Exception as e:
            capture_errors.append(f"goto_landing: {str(e)[:120]}")
            return ""
        try:
            await p.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        try:
            title = await p.title()
        except Exception:
            return ""
        if _is_bot_page(title, p.url):
            # 托管验证可能自动通过；若需人工点击，窗口保持打开等用户完成（最多 60s）
            if not await _wait_challenge_clear(p, timeout_s=60.0):
                return title  # 仍被挑战
            try:
                title = await p.title()
            except Exception:
                title = ""
        return title

    async def _extract_target(p) -> str | None:
        try:
            m = re.search(r"/pii/(S\d+[A-Za-z0-9]+)", p.url or "")
            pii = m.group(1) if m else None
            hrefs = await p.eval_on_selector_all(
                "a",
                "els => els.map(a => a.href).filter(h => /pdf/i.test(h) && /^(https?:)/.test(h))",
            )
            for h in hrefs:
                if pii and pii not in h:
                    continue
                if "/pdfft" in h or "/pdf" in h:
                    return h
        except Exception:
            pass
        return None

    context = await get_context()
    context.on("response", _on_response)
    context.on("download", _on_download)
    page = await context.new_page()
    try:
        # 单次执行：不再为重试而重新加载文章页（每次重载都会重新触发 Cloudflare 挑战）
        if context.is_closed() or page.is_closed():
            context = await get_context()
            context.on("response", _on_response)
            context.on("download", _on_download)
            page = await context.new_page()

        orig_page = page  # 等待循环可能把 page 切到 PDF 标签页，finally 里要关回原页面

        title = await _open_landing(context, page)
        if _is_bot_page(title, page.url):
            return {"status": "bot_check", "url": landing, "title": title, "capture_errors": capture_errors[-5:]}
        if not title:
            return {"status": "error", "url": landing, "error": "landing_page_failed", "capture_errors": capture_errors[-5:]}

        # ScienceDirect 是 React SPA：View PDF 链接延迟渲染，domcontentloaded 时还不在 DOM 里。
        # 必须先等它出现，否则 _extract_target 会拿到空列表、永远不会点击。
        try:
            m = re.search(r"/pii/(S\d+[A-Za-z0-9]+)", page.url or "")
            _pii = m.group(1) if m else None
            if _pii:
                await page.wait_for_selector(
                    f"a[href*='{_pii}'][href*='pdf']", state="attached", timeout=30000
                )
            else:
                await page.wait_for_selector("a[href*='/pdfft']", state="attached", timeout=30000)
        except Exception:
            pass

        target = await _extract_target(page)
        if not target:
            return {
                "status": "html_not_pdf",
                "url": page.url,
                "title": title,
                "error": "no_view_pdf_link",
                "capture_errors": capture_errors[-5:],
            }

        # 真实点击 View PDF（等价于人工操作；可能同页导航或开新标签页）
        clicked = False
        try:
            m = re.search(r"/pii/(S\d+[A-Za-z0-9]+)", page.url or "")
            pii = m.group(1) if m else None
            loc = page.locator(f"a[href*='{pii}'][href*='pdf']").first if pii else page.locator("a[href*='pdfft']").first
            if await loc.count() > 0:
                await loc.click(timeout=10000)
                clicked = True
        except Exception as e:
            capture_errors.append(f"click: {str(e)[:120]}")
        if not clicked:
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as e:
                return {
                    "status": "error",
                    "url": landing,
                    "error": f"goto_target: {str(e)[:150]}",
                    "capture_errors": capture_errors[-5:],
                }

        # 等待：PDF 截获 / 下载事件；期间若出现挑战，等用户或自动放行（绝不重新加载页面）
        deadline = time.time() + 45
        while time.time() < deadline:
            if pdf_bodies or download_path:
                break
            # 点击可能开了新标签页：优先跟随 PDF 标签（pdf.sciencedirectassets / pdfft / viewer），
            # 避免被 newtab 或原文章页干扰而反复切换。
            pdf_page = None
            for p in context.pages:
                if p.is_closed():
                    continue
                u = p.url or ""
                if "pdf.sciencedirectassets" in u or "/pdfft" in u or ".pdf" in u or "viewer" in u:
                    pdf_page = p
                    break
            if pdf_page is not None:
                page = pdf_page
            try:
                t = await page.title()
            except Exception:
                t = ""
            if _is_bot_page(t, page.url):
                await _wait_challenge_clear(page, timeout_s=45.0)
                continue
            # 兜底：页面已到 PDF 直链（presigned URL 5 分钟内有效）但响应未被截获时，带 Referer 直取
            if "pdf" in (page.url or "") and not pdf_bodies and not download_path:
                try:
                    resp = await context.request.get(
                        page.url,
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                            ),
                            "Referer": "https://www.sciencedirect.com/",
                        },
                        timeout=30000,
                    )
                    body = await resp.body()
                    if body[:5] == b"%PDF-":
                        pdf_bodies.append(body)
                        break
                except Exception:
                    pass
            await asyncio.sleep(2)

        if pdf_bodies:
            body = max(pdf_bodies, key=len)
            output_path.write_bytes(body)
            return {"status": "pdf", "path": str(output_path), "size": len(body)}
        if download_path:
            try:
                data = Path(download_path).read_bytes()
                if data[:5] == b"%PDF-":
                    output_path.write_bytes(data)
                    return {"status": "pdf", "path": str(output_path), "size": len(data)}
            except Exception:
                pass
        try:
            final_title = await page.title()
        except Exception:
            final_title = ""
        return {
            "status": "html_not_pdf",
            "url": page.url,
            "title": final_title,
            "snippet": "",
            "capture_errors": capture_errors[-5:],
        }
    except Exception as e:
        return {"status": "error", "url": landing, "error": str(e)[:200]}
    finally:
        # 关闭原文章页，以及点击 View PDF 时浏览器新开的 PDF 查看器标签页（防残留）
        try:
            for p in list(context.pages):
                if p.is_closed():
                    continue
                if p is orig_page:
                    await p.close()
                    continue
                u = p.url or ""
                if "pdf.sciencedirectassets" in u or "/pdfft" in u:
                    await p.close()
        except Exception:
            pass
        try:
            context.remove_listener("response", _on_response)
        except Exception:
            pass
        try:
            context.remove_listener("download", _on_download)
        except Exception:
            pass


async def resolve_elsevier_pii(doi: str) -> str | None:
    """通过 doi.org 跳转获取 ScienceDirect 文章 PII（用于构造 pdfft 直连路由）。"""
    context = await get_context()
    page = await context.new_page()
    try:
        resp = await page.goto(f"https://doi.org/{doi}", wait_until="domcontentloaded", timeout=60000)
        final_url = page.url or (resp.url if resp else "")
        m = re.search(r"/pii/(S\d+[A-Za-z0-9]+)", final_url)
        return m.group(1) if m else None
    except Exception:
        return None
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def login_status() -> dict[str, Any]:
    """检查各出版社域名是否有会话 cookie（Chrome 未启动时优雅降级，不抛 traceback）。"""
    try:
        context = await get_context()
    except RuntimeError as e:
        return {
            "browser": False,
            "error": str(e).split("\n")[0],
            "domains": {d: False for d in SESSION_DOMAINS},
        }
    try:
        cookies = await context.cookies()
    except Exception:
        return {
            "browser": True,
            "error": "cookie_read_failed",
            "domains": {d: False for d in SESSION_DOMAINS},
        }
    names = {c["domain"].lstrip(".") for c in cookies}
    result: dict[str, bool] = {}
    for d in SESSION_DOMAINS:
        result[d] = any(n == d or n.endswith("." + d) for n in names)
    return {"browser": True, "domains": result}


async def login_carsi(publisher: str, idp_name: str | None = None) -> dict[str, Any]:
    """打开出版社机构登录页，自动搜索并选中学校（CARSI），等待用户完成统一身份认证。"""
    from .publishers import login_start_urls

    cfg = get_config()
    idp_name = idp_name or cfg.get("idp_name", "华侨大学")
    urls = login_start_urls()
    start = urls.get(publisher)
    if not start:
        return {"status": "error", "error": f"unsupported_publisher:{publisher}"}

    context = await get_context()
    page = await context.new_page()
    try:
        await page.goto(start, wait_until="domcontentloaded", timeout=60000)
        # 尝试点击机构登录入口
        selectors = [
            "text=Log in via your institution",
            "text=Access through your institution",
            "text=Institutional login",
            "text=机构登录",
            "text=通过您的机构登录",
            "a:has-text('institution')",
            "a:has-text('Institution')",
        ]
        clicked = False
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue
        # 在 WAYF / CARSI 页面搜索学校
        found = False
        for sel in ["input[type='search']", "input[placeholder*='school' i]", "input[placeholder*='institution' i]", "input[placeholder*='机构' i]", "input#searchInput", "input"]:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(idp_name, timeout=3000)
                    await loc.press("Enter")
                    found = True
                    break
            except Exception:
                continue
        if found:
            # 点击包含机构名的选项
            for sel in [
                f"text={idp_name}",
                f"a:has-text('{idp_name}')",
                f"li:has-text('{idp_name}')",
                f"option:has-text('{idp_name}')",
            ]:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(timeout=5000)
                        break
                except Exception:
                    continue
        return {
            "status": "login_started",
            "publisher": publisher,
            "idp_name": idp_name,
            "url": start,
            "clicked_institution": clicked,
            "searched_idp": found,
            "note": "请在浏览器中完成统一身份认证（账号/密码/MFA 由您本人操作），完成后调用 status 确认会话。",
        }
    except Exception as e:
        return {"status": "error", "publisher": publisher, "error": str(e)[:300]}
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def login_atrust(url: str | None = None) -> dict[str, Any]:
    """打开华侨大学 aTrust 零信任门户，等待用户登录（校外兜底）。"""
    cfg = get_config()
    url = url or cfg.get("atrust_url", "https://atrust.hqu.edu.cn")
    context = await get_context()
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        return {
            "status": "login_started",
            "url": url,
            "note": "请在浏览器中完成统一身份认证，登录后门户内可访问授权资源。",
        }
    except Exception as e:
        return {"status": "error", "url": url, "error": str(e)[:300]}
    finally:
        try:
            await page.close()
        except Exception:
            pass
