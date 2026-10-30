"""
fetch_elsevier.py —— 调用 Elsevier Scopus Search API 抓元数据（骨架版 v0.1）

用法:
    python scripts/fetch_elsevier.py [--config config.yaml] [--out data/raw_pdfs/elsevier_results.json]

行为:
    1. 从 config.yaml 读 elsevier.{api_key, base_url, query, max_results};
    2. 若 api_key 缺失或仍是占位符 "你的_API_KEY" → 立即拒绝并提示；
    3. GET 请求 base_url，头 X-ELS-APIKey，params query/count/view=COMPLETE；
    4. 解析 search-results.entry[] 提取 scopus_id / doi / title / abstract / authors / year / source_title；
    5. 完整 payload + 精简结果一并写入 elsevier_results.json；
    6. 打印前 5 篇标题供用户确认；
    7. 只抓元数据，不下载 PDF、不触碰数据库。

Scopus Search API 备注:
    - 端点 https://api.elsevier.com/content/search/scopus
    - count 上限 200/请求；本脚本单页足够（默认 20），如需翻页请扩展 start 参数
    - view=COMPLETE 才会返回 dc:description（摘要）
    - 常见错误：401 未授权 / 429 频率超限 / 403 IP 不在白名单
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
import yaml

PLACEHOLDER_KEY = "你的_API_KEY"
REQUEST_TIMEOUT = 30
SCOPUS_COUNT_MAX = 200


def load_elsevier_config(config_path):
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    e = cfg.get("elsevier")
    if not e:
        sys.exit(f"[ERR] {config_path} 里没有 elsevier 段")
    api_key = (e.get("api_key") or "").strip()
    if not api_key or api_key == PLACEHOLDER_KEY:
        sys.exit(
            "[ERR] elsevier.api_key 未填或仍是占位符；\n"
            f"      请在 {config_path} 中把 api_key 改成真实 Elsevier API Key 后再运行。"
        )
    for k in ("base_url", "query"):
        if not e.get(k):
            sys.exit(f"[ERR] elsevier.{k} 缺失")
    return e


def _get_page(cfg, start, count):
    params = {"query": cfg["query"], "count": count, "start": start, "view": "COMPLETE"}
    headers = {
        "X-ELS-APIKey": cfg["api_key"].strip(),
        "Accept": "application/json",
    }
    resp = requests.get(cfg["base_url"], headers=headers, params=params,
                        timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        # Scopus 错误体常见形如 {"error": {...}} 或 {"service-error": {...}}
        try:
            err = resp.json()
        except Exception:
            err = {"raw": resp.text[:400]}
        sys.exit(f"[ERR] HTTP {resp.status_code}: {json.dumps(err, ensure_ascii=False)[:400]}")
    return resp.json()


def call_scopus(cfg):
    """分页拉取：limited 级 key 单页 count 有上限（常见 25），按 page_size 翻页聚合。"""
    max_results = int(cfg.get("max_results", 20) or 20)
    page_size = min(int(cfg.get("page_size", 25) or 25), SCOPUS_COUNT_MAX)
    agg, total, start = [], None, 0
    while len(agg) < max_results:
        want = min(page_size, max_results - len(agg))
        payload = _get_page(cfg, start, want)
        sr = payload.get("search-results") or {}
        if total is None:
            total = sr.get("opensearch:totalResults") or \
                (sr.get("meta-information") or {}).get("total-results")
        page = sr.get("entry") or []
        if not page:
            break
        agg.extend(page)
        start += len(page)
        if total and start >= int(total):
            break
        if len(page) < want:
            break
        time.sleep(0.3)
    print(f"      分页拉取完成: {len(agg)} 条 (库内 total={total})")
    return {"search-results": {"opensearch:totalResults": total, "entry": agg}}


def _extract_year(s):
    """从任意串里抠出第一个合理年份（1900–2035）。

    只认"独立"的数字段：优先取长度恰为 4 且落在 1900–2035 的段；
    其次对长度 >= 5 的段取前 4 位（如 DOI 里 '202312345' -> 2023）。
    避免把 DOI 前缀/文章号内部的前缀误判成年份：
      '10.19805/...'      -> 不再返回 1980（应取后面的 2024）
      '...5c12035'        -> 不再从 '12035' 抠出 2035
    """
    runs = re.findall(r"\d+", str(s))
    for run in runs:
        if len(run) == 4 and 1900 <= int(run) <= 2035:
            return int(run)
    for run in runs:
        if len(run) >= 5:
            y = int(run[:4])
            if 1990 <= y <= 2035:
                return y
    return None


def _year_of(entry):
    # 1) 优先从 DOI 抠年份：Elsevier 文章 DOI 通常内嵌真实出版年，
    #    而 prism:coverDate 常是"预测期"封刊日期（可能晚 1 年，甚至 2027）。
    y = _extract_year(entry.get("prism:doi") or "")
    if y:
        return y
    # 2) DOI 抠不到，再退回 coverDate / publicationDate
    for k in ("prism:coverDate", "prism:publicationDate", "publicationDate"):
        y = _extract_year(entry.get(k) or "")
        if y:
            return y
    return None


def extract_entries(payload):
    sr = (payload.get("search-results") or {})
    out = []
    for e in sr.get("entry") or []:
        authors = [a.get("authname") for a in (e.get("author") or []) if a.get("authname")]
        out.append({
            "scopus_id": e.get("eid"),
            "doi": e.get("prism:doi"),
            "title": e.get("dc:title"),
            "abstract": e.get("dc:description"),
            "authors": authors,
            "publication_year": _year_of(e),
            "source_title": e.get("srctitle") or e.get("sourceTitle"),
            "openaccess": e.get("openaccess"),
            "raw": e,
        })
    # Scopus 真总命中数在 opensearch:totalResults；meta-information 为兜底
    total = sr.get("opensearch:totalResults") or \
        (sr.get("meta-information") or {}).get("total-results")
    return total, out


def print_top5(entries):
    if not entries:
        print("  (无结果)")
        return
    for i, e in enumerate(entries[:5], 1):
        doi = e["doi"] or "N/A"
        yr = e["publication_year"] or "?"
        title = (e["title"] or "(no title)").strip()
        print(f"  {i}. [{yr}] {title}   (DOI: {doi})")


def main():
    ap = argparse.ArgumentParser(description="Elsevier Scopus Search API 元数据抓取（骨架）")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="data/raw_pdfs/elsevier_results.json")
    args = ap.parse_args()

    cfg = load_elsevier_config(args.config)
    print(f"[1/4] 调 API: base_url={cfg['base_url']}")
    print(f"      query = {cfg['query']}")
    print(f"      max_results = {cfg.get('max_results', 20)} | page_size = {cfg.get('page_size', 25)}")

    payload = call_scopus(cfg)
    total, entries = extract_entries(payload)
    print(f"[2/4] 命中 {len(entries)} 篇（库内 total-results={total}）")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "query": cfg["query"],
        "base_url": cfg["base_url"],
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_results": total,
        "returned": len(entries),
        "entries": entries,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/4] 结果写入 {out_path}")

    print(f"[4/4] 前 5 篇标题：")
    print_top5(entries)


if __name__ == "__main__":
    main()
