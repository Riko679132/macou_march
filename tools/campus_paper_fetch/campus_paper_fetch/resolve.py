from __future__ import annotations

import re
from typing import Any

import httpx

from .config import get_config

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_cache: dict[str, dict[str, Any]] = {}


def _client() -> httpx.Client:
    return httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent": UA})


def normalize_identifier(idstr: str) -> str | None:
    """把 DOI / doi.org 链接 / 任意带 DOI 的 URL 规整为 DOI；无法识别返回 None。"""
    s = idstr.strip()
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", s, re.IGNORECASE)
    if m:
        return m.group(0).rstrip(".,;:)").lower()
    return None


def _fetch_openalex(doi: str) -> dict[str, Any] | None:
    """OpenAlex 元数据 + OA 链接。正常未命中（非 200）返回 None；异常向上抛给 _safe_fetch 记录。"""
    r = _client().get(f"https://api.openalex.org/works/doi:{doi}")
    if r.status_code != 200:
        return None
    w = r.json()
    best = (w.get("best_oa_location") or {}) or {}
    primary = (w.get("primary_location") or {}) or {}
    landing = best.get("landing_page_url") or primary.get("landing_page_url") or ""
    authors = (w.get("authorships") or [])
    first_author = authors[0]["author"]["display_name"] if authors else ""
    return {
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "publisher": (w.get("primary_location") or {}).get("source", {}).get("display_name") or "",
        "first_author": first_author,
        "oa_url": best.get("pdf_url") or "",
        "oa_landing": landing,
        "oa_status": w.get("oa_status") or "",
        "is_oa": bool(w.get("open_access", {}).get("is_oa")),
    }


def _fetch_unpaywall(doi: str) -> dict[str, Any] | None:
    """Unpaywall OA 检测（需已配置 email）。异常向上抛给 _safe_fetch 记录。"""
    email = get_config().get("email", "").strip()
    if not email:
        return None
    r = _client().get(
        f"https://api.unpaywall.org/v2/{doi}",
        params={"email": email},
    )
    if r.status_code != 200:
        return None
    d = r.json()
    best = (d.get("best_oa_location") or {}) or {}
    return {
        "is_oa": bool(d.get("is_oa")),
        "oa_url": best.get("url_for_pdf") or "",
        "oa_landing": best.get("url") or "",
        "host_type": best.get("host_type") or "",
        "license": best.get("license") or "",
    }


def _fetch_crossref(doi: str) -> dict[str, Any] | None:
    """Crossref 元数据。正常未命中（非 200）返回 None；异常向上抛给 _safe_fetch 记录。"""
    r = _client().get(f"https://api.crossref.org/works/{doi}")
    if r.status_code != 200:
        return None
    m = r.json()["message"]
    title = (m.get("title") or [""])[0]
    authors = m.get("author") or []
    family = ""
    for a in authors:
        if a.get("family"):
            family = a["family"]
            break
    year = None
    issued = m.get("issued", {}).get("date-parts", [[None]])
    if issued and issued[0]:
        year = issued[0][0]
    links = []
    for link in m.get("link") or []:
        u = link.get("URL", "")
        if u:
            links.append(u)
    return {
        "title": title,
        "publisher": m.get("publisher") or "",
        "year": year,
        "first_author": family,
        "links": links,
        "landing_page": m.get("URL") or "",
        "container": (m.get("container-title") or [""])[0],
        "type": m.get("type") or "",
    }


def _safe_fetch(name: str, fn, doi: str) -> tuple[dict[str, Any] | None, str | None]:
    """执行单个数据源，返回 (data, error)。正常未命中 error=None；环境异常（网络/代理等）记录原因。"""
    try:
        return fn(doi), None
    except Exception as e:  # noqa: BLE001 —— 需要把任意环境错误转成可读 warning
        return None, f"{name}: {type(e).__name__}: {str(e)[:120]}"


def resolve(identifier: str) -> dict[str, Any]:
    """解析 DOI：合并 Crossref / OpenAlex / Unpaywall 元数据与 OA 链接。

    环境性失败（网络 / 代理 / 依赖缺失等）不再静默吞掉：
    - 单个数据源失败 → 记入 ``warnings``（元数据仍尽量合并其余源）；
    - 所有元数据源失败 → ``error`` 给出具体原因（如 socks5 代理缺 socksio 这类问题一眼可见）。
    """
    doi = normalize_identifier(identifier)
    if not doi:
        return {"identifier": identifier, "doi": None, "error": "cannot_parse_doi"}
    if doi in _cache:
        return _cache[doi]

    cr, err_cr = _safe_fetch("crossref", _fetch_crossref, doi)
    oa, err_oa = _safe_fetch("openalex", _fetch_openalex, doi)
    up, err_up = _safe_fetch("unpaywall", _fetch_unpaywall, doi)
    warnings = [e for e in (err_cr, err_oa, err_up) if e]

    meta: dict[str, Any] = {
        "identifier": identifier,
        "doi": doi,
        "title": (cr or {}).get("title") or (oa or {}).get("title") or "",
        "publisher_name": (cr or {}).get("publisher") or (oa or {}).get("publisher") or "",
        "year": (cr or {}).get("year") or (oa or {}).get("year"),
        "first_author": (cr or {}).get("first_author") or (oa or {}).get("first_author") or "",
        "container": (cr or {}).get("container") or "",
        "links": list(dict.fromkeys((cr or {}).get("links", []))),
        "landing_page": (cr or {}).get("landing_page") or (oa or {}).get("oa_landing") or "",
        "oa_url": "",
        "oa_status": (oa or {}).get("oa_status") or ("oa" if (up or {}).get("is_oa") else ""),
        "is_oa": bool((oa or {}).get("is_oa") or (up or {}).get("is_oa")),
        "warnings": warnings,
        "error": None,
    }
    # OA PDF 优先级：OpenAlex pdf_url > Unpaywall url_for_pdf > Crossref link[application/pdf]
    for candidate in [
        (oa or {}).get("oa_url", ""),
        (up or {}).get("oa_url", ""),
        next((u for u in meta["links"] if "pdf" in u.lower()), ""),
    ]:
        if candidate:
            meta["oa_url"] = candidate
            break
    if not (cr or oa) and warnings:
        meta["error"] = "metadata_sources_failed: " + "; ".join(warnings)
    _cache[doi] = meta
    return meta
