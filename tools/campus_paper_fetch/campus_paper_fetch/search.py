from __future__ import annotations

from typing import Any

import httpx

from .config import get_config
from .download import download_one
from .publishers import detect_publisher

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def openalex_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        r = httpx.get(
            "https://api.openalex.org/works",
            params={
                "search": query,
                "per-page": min(max(limit, 1), 50),
                "select": "id,doi,title,publication_year,cited_by_count,best_oa_location,authorships,primary_location",
            },
            headers={"User-Agent": UA},
            timeout=25.0,
            follow_redirects=True,
        )
        r.raise_for_status()
    except Exception as e:
        return [{"error": str(e)}]
    excluded = [p.strip().lower() for p in get_config().get("exclude_publishers", []) if p.strip()]
    items: list[dict[str, Any]] = []
    for w in r.json().get("results", []):
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        pub = detect_publisher(doi)
        if pub and pub in excluded:
            continue
        best = (w.get("best_oa_location") or {}) or {}
        authors = w.get("authorships") or []
        first = authors[0]["author"]["display_name"] if authors else ""
        items.append(
            {
                "title": w.get("title") or "",
                "doi": doi,
                "year": w.get("publication_year"),
                "cited_by": w.get("cited_by_count", 0),
                "first_author": first,
                "journal": (w.get("primary_location") or {}).get("source", {}).get("display_name") or "",
                "oa": bool(best.get("pdf_url") or best.get("landing_page_url")),
            }
        )
    return items


async def search_and_download(
    query: str,
    limit: int = 5,
    oa_only: bool = True,
    output_dir: str | None = None,
) -> list[dict[str, Any]]:
    hits = openalex_search(query, limit=limit)
    results: list[dict[str, Any]] = []
    for h in hits:
        if not h.get("doi"):
            continue
        r = await download_one(h["doi"], use_browser=not oa_only, output_dir=output_dir)
        r["hit"] = h
        results.append(r)
    return results
