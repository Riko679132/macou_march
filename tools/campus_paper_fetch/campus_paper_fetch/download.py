from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx

from . import verify as v
from .browser import fetch_pdf_url, fetch_sciencedirect_pdf, get_context, login_status
from .config import ensure_output_dir, get_config
from .publishers import PUBLISHER_NAMES, detect_publisher, direct_pdf_urls
from .resolve import resolve

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _safe(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or "untitled"


def build_filename(meta: dict[str, Any], doi: str) -> str:
    parts: list[str] = []
    raw_year = str(meta.get("year") or "")
    raw_author = str(meta.get("first_author") or "")
    raw_title = str(meta.get("title") or "")
    if raw_year:
        parts.append(raw_year)
    if raw_author:
        parts.append(_safe(raw_author, 30))
    if raw_title:
        parts.append(_safe(raw_title, 80))
    name = "_".join(parts) if parts else _safe(doi.replace("/", "_"), 100)
    return name + ".pdf"


def _http_get_pdf(url: str, dest: Path, timeout: float = 60.0, headers: dict[str, str] | None = None) -> str | None:
    try:
        r = httpx.get(url, follow_redirects=True, headers=headers or {"User-Agent": UA}, timeout=timeout)
        if r.status_code != 200 or not v.is_pdf_bytes(r.content):
            return None
        dest.write_bytes(r.content)
        return "ok"
    except Exception:
        return None


def _elsevier_api_pdf(doi: str, api_key: str, dest: Path) -> str | None:
    url = f"https://api.elsevier.com/content/article/doi/{doi}?httpAccept=application/pdf"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/pdf",
        "User-Agent": UA,
    }
    return _http_get_pdf(url, dest, timeout=90.0, headers=headers)


def _verify_result(path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    if cfg.get("verify_pdf", True):
        return v.verify_pdf(path)
    return {"ok": True, "size": path.stat().st_size, "pages": None, "error": None}


async def download_one(
    identifier: str,
    cfg: dict[str, Any] | None = None,
    use_browser: bool = True,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    out = ensure_output_dir(cfg) if output_dir is None else Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    meta = resolve(identifier)
    doi = meta.get("doi")
    if not doi:
        return {"identifier": identifier, "status": "failed", "error": "cannot_parse_doi", "meta": meta}

    publisher = detect_publisher(doi)
    publisher_name = PUBLISHER_NAMES.get(publisher, meta.get("publisher_name") or publisher or "")
    excluded = [p.strip().lower() for p in cfg.get("exclude_publishers", []) if p.strip()]
    if publisher and publisher in excluded:
        return {
            **{
                "identifier": identifier,
                "doi": doi,
                "title": meta.get("title"),
                "publisher": publisher_name,
                "year": meta.get("year"),
                "status": "excluded",
                "source": None,
                "path": None,
                "size": None,
                "pages": None,
                "error": f"publisher_excluded:{publisher}",
                "attempts": [],
            }
        }

    result: dict[str, Any] = {
        "identifier": identifier,
        "doi": doi,
        "title": meta.get("title"),
        "publisher": publisher_name,
        "year": meta.get("year"),
        "status": "failed",
        "source": None,
        "path": None,
        "size": None,
        "pages": None,
        "error": None,
        "attempts": [],
    }

    filename = build_filename(meta, doi)
    dest = out / filename
    if dest.exists() and dest.stat().st_size > 0:
        result.update(status="ok", source="exists", path=str(dest), size=dest.stat().st_size)
        return result

    # 1) OA
    if meta.get("oa_url"):
        src = _http_get_pdf(meta["oa_url"], dest)
        if src:
            ver = _verify_result(dest, cfg)
            if ver["ok"]:
                result.update(
                    status="ok", source="oa", path=str(dest), size=ver["size"], pages=ver["pages"],
                    oa_status=meta.get("oa_status"),
                )
                return result
            result["attempts"].append({"source": "oa", "error": ver["error"]})
        else:
            result["attempts"].append({"source": "oa", "error": "http_failed_or_not_pdf"})
        # OA 纯 HTTP 被风控拦截时，回退到真实浏览器会话（同 cookie/指纹）
        if use_browser and publisher != "elsevier":
            fetched = await fetch_pdf_url(meta["oa_url"], dest)
            if fetched.get("status") == "pdf":
                ver = _verify_result(dest, cfg)
                if ver["ok"]:
                    result.update(
                        status="ok", source="oa_browser", path=str(dest), size=ver["size"], pages=ver["pages"],
                        oa_status=meta.get("oa_status"),
                    )
                    return result
                result["attempts"].append({"source": "oa_browser", "error": ver["error"]})
            else:
                result["attempts"].append(
                    {"source": "oa_browser", "status": fetched.get("status"), "error": fetched.get("title") or fetched.get("snippet") or ""}
                )

    # 2) Elsevier API
    if publisher == "elsevier" and cfg.get("elsevier_api_key"):
        src = _elsevier_api_pdf(doi, cfg["elsevier_api_key"], dest)
        if src:
            ver = _verify_result(dest, cfg)
            if ver["ok"]:
                result.update(status="ok", source="elsevier_api", path=str(dest), size=ver["size"], pages=ver["pages"])
                return result
            result["attempts"].append({"source": "elsevier_api", "error": ver["error"]})
        else:
            result["attempts"].append({"source": "elsevier_api", "error": "api_failed_or_not_pdf"})

    # 3) 出版社直连（浏览器会话，校园网 IP 授权）
    if use_browser:
        candidates: list[tuple[str, str]] = []
        if publisher == "elsevier":
            # ScienceDirect 专用流：文章页 → View PDF(md5) → 同页导航截获（跨页直链会被 Cloudflare 拦）
            fetched = await fetch_sciencedirect_pdf(meta, dest)
            if fetched.get("status") == "pdf":
                ver = _verify_result(dest, cfg)
                if ver["ok"]:
                    result.update(
                        status="ok", source="sciencedirect_direct", path=str(dest), size=ver["size"], pages=ver["pages"]
                    )
                    return result
                result["attempts"].append({"source": "sciencedirect_direct", "error": ver["error"]})
            else:
                _err = fetched.get("title") or fetched.get("snippet") or fetched.get("error") or ""
                if fetched.get("capture_errors"):
                    _err = f"{_err} | capture_errors={fetched['capture_errors']}"
                result["attempts"].append(
                    {
                        "source": "sciencedirect_direct",
                        "status": fetched.get("status"),
                        "error": _err,
                    }
                )
        else:
            candidates = direct_pdf_urls(doi, publisher, meta)
        if candidates:
            delay = float(cfg.get("publisher_delay", 3))
            for source, url in candidates:
                fetched = await fetch_pdf_url(url, dest)
                if fetched.get("status") == "pdf":
                    ver = _verify_result(dest, cfg)
                    if ver["ok"]:
                        result.update(
                            status="ok", source=source, path=str(dest), size=ver["size"], pages=ver["pages"]
                        )
                        return result
                    result["attempts"].append({"source": source, "error": ver["error"]})
                else:
                    result["attempts"].append(
                        {"source": source, "status": fetched.get("status"), "error": fetched.get("title") or fetched.get("snippet") or ""}
                    )
                await asyncio.sleep(delay)

    last = result["attempts"][-1] if result["attempts"] else {}
    if last.get("status") == "bot_check":
        result["error"] = "bot_check: 站点触发人机验证，已停止（不尝试绕过）"
    elif not result["attempts"]:
        result["error"] = "no_route: 无 OA、无 API Key、无可用直连路由"
    else:
        result["error"] = f"all_attempts_failed: {len(result['attempts'])} 条路径失败"
    return result


async def batch_download(
    identifiers: list[str],
    cfg: dict[str, Any] | None = None,
    oa_only: bool = False,
    output_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    cfg = cfg or get_config()
    workers = int(cfg.get("oa_workers", 4))
    results: list[dict[str, Any]] = [None] * len(identifiers)  # type: ignore[list-item]

    # 第一阶段：OA 并发（不碰浏览器）
    sem = asyncio.Semaphore(workers)

    async def oa_task(i: int, identifier: str) -> None:
        async with sem:
            results[i] = await download_one(identifier, cfg=cfg, use_browser=False, output_dir=output_dir)

    await asyncio.gather(*(oa_task(i, s) for i, s in enumerate(identifiers)))

    if oa_only:
        return results

    # 第二阶段：失败项走浏览器直连（串行，带间隔，避免风控）
    pending = [i for i, r in enumerate(results) if r.get("status") != "ok"]
    before: set | None = None
    if pending:
        try:
            # 快照批次开始前的页面集合，收尾时只清理本次新开的标签页
            from .browser import get_context

            before = set((await get_context()).pages)
        except Exception:
            before = None
    for i in pending:
        results[i] = await download_one(identifiers[i], cfg=cfg, use_browser=True, output_dir=output_dir)
    if pending:
        try:
            from .browser import close_idle_pages

            closed = await close_idle_pages(before)
            if closed:
                print(f"[cleanup] 已关闭 {closed} 个残留标签页")
        except Exception:
            pass
    return results


async def _login_gate(cfg: dict[str, Any]) -> None:
    """机构直连前确保浏览器会话存在（触发 profile 初始化）。"""
    await get_context()
