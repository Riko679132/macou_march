"""
web_crawler.py —— 自动采集模式：文献搜索页爬取 + PDF 下载（骨架版 v0.1）

用法:
    python scripts/web_crawler.py --url <搜索页URL> --keyword <关键词> [--max_pdfs 5] [--outdir data/raw_pdfs]

行为:
    1. GET 搜索页 HTML，BeautifulSoup 解析所有 <a href>；
    2. 启发式筛选疑似 PDF 直链（.pdf / doi/pdf / /download 等）；
    3. 按顺序下载至 outdir，按 sha256 跳过重复；
    4. 每次运行末尾输出 manifest_<时间戳>.json 记录条目状态；
    5. 只下载文件，不触碰数据库。

TODO（站点专用规则，需按需扩展）：
    - ACS / Elsevier / ScienceDirect 通常需先访问 landing page，再拿真实 PDF；
    - 部分站点需 Referer / Cookie；
    - 搜索页分页规则（?page=2 / offset= 等）。
"""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "ccus-lit-crawler/0.1 (research use; local only)"
    ),
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}
SLEEP_SECONDS = 2          # 礼貌间隔，避免高频请求
DOWNLOAD_TIMEOUT = 60      # 单次请求超时（秒）
PDF_MAGIC = b"%PDF"


def fetch(url):
    resp = requests.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    return resp


def looks_like_pdf_link(href, text):
    """启发式：链接或链接文本包含 PDF 特征。"""
    hay = ((href or "") + " " + (text or "")).lower()
    return any(p in hay for p in (
        ".pdf", "/doi/pdf", "/download", "download%3f", "?file=",
    ))


def collect_candidates(html, base_url):
    """返回 [{"href": ..., "title": ...}, ...]。"""
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    candidates = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        if urlparse(href).scheme not in ("http", "https"):
            continue
        if href in seen:
            continue
        if looks_like_pdf_link(href, a.get_text(strip=True)):
            seen.add(href)
            candidates.append({
                "href": href,
                "title": a.get_text(strip=True) or None,
            })
    return candidates


def slugify(text, maxlen=80):
    s = re.sub(r"[^\w\-]+", "_", (text or "").strip())
    return s[:maxlen].strip("_") or "untitled"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def build_hash_index(outdir):
    """扫描 outdir 里已有的 PDF，构造 {sha256: filename}。"""
    idx = {}
    for p in Path(outdir).glob("*.pdf"):
        try:
            idx[sha256_bytes(p.read_bytes())] = p.name
        except Exception:
            pass
    return idx


def is_pdf_response(resp, body):
    ctype = (resp.headers.get("Content-Type") or "").lower()
    return "pdf" in ctype or body.startswith(PDF_MAGIC)


def download_pdf(url, outdir, hash_idx):
    """下载单个 PDF；返回 (filename, sha256, bytes, status)。"""
    resp = requests.get(url, headers=HEADERS, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    body = resp.content
    if not is_pdf_response(resp, body):
        raise ValueError(
            f"响应不是 PDF (Content-Type={resp.headers.get('Content-Type')})"
        )
    digest = sha256_bytes(body)
    if digest in hash_idx:
        return hash_idx[digest], digest, len(body), "skipped_duplicate"
    stem = slugify(Path(urlparse(url).path).stem or url.split("/")[-1])
    name = f"{stem}_{digest[:8]}.pdf"
    path = Path(outdir) / name
    path.write_bytes(body)
    hash_idx[digest] = name
    return name, digest, len(body), "downloaded"


def main():
    ap = argparse.ArgumentParser(description="文献搜索页 PDF 批量下载（骨架）")
    ap.add_argument("--url", required=True, help="搜索页 URL")
    ap.add_argument("--keyword", required=True, help="搜索关键词（仅写入 manifest）")
    ap.add_argument("--max_pdfs", type=int, default=5, help="本次最多下载几个新 PDF")
    ap.add_argument("--outdir", default="data/raw_pdfs", help="PDF 保存目录")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 抓取搜索页: {args.url}")
    page = fetch(args.url)
    candidates = collect_candidates(page.text, page.url)
    print(f"[2/4] 候选 PDF 链接: {len(candidates)} 个")

    hash_idx = build_hash_index(outdir)
    manifest = {
        "url": args.url,
        "keyword": args.keyword,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "max_pdfs": args.max_pdfs,
        "items": [],
    }

    downloaded = 0
    for cand in candidates:
        if downloaded >= args.max_pdfs:
            break
        time.sleep(SLEEP_SECONDS)
        entry = {"title": cand["title"], "url": cand["href"]}
        try:
            name, digest, size, status = download_pdf(cand["href"], outdir, hash_idx)
            entry.update({
                "file": name, "sha256": digest, "bytes": size, "status": status,
            })
            if status == "downloaded":
                downloaded += 1
        except Exception as e:
            entry.update({"status": "failed", "error": str(e)})
        manifest["items"].append(entry)
        fname = entry.get("file") or "(fail)"
        print(f"    - {entry['status']:>17}  {fname}  {cand['href']}")

    mpath = outdir / f"manifest_{time.strftime('%Y%m%d_%H%M%S')}.json"
    mpath.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[3/4] 新下载 {downloaded} 个 PDF → {outdir}")
    print(f"[4/4] 清单: {mpath}")


if __name__ == "__main__":
    main()
