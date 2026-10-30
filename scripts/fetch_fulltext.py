"""
fetch_fulltext.py —— 用 Unpaywall 探测合法 PDF 直链并下载；其余导出待办清单

用法:
    python scripts/fetch_fulltext.py [--results data/raw_pdfs/elsevier_results.json]
                                     [--outdir data/raw_pdfs] [--config config.yaml]

行为:
    1. 读 elsevier_results.json 的 entries；
    2. 对**所有带 DOI 的条目**调 Unpaywall v2 API（不止 Scopus 标记 OA=1，
       以捞回 green OA / 作者自存档副本）；
    3. 逐 location 采集候选直链：
       - 优先 url_for_pdf；
       - 回退 url / url_for_landing_page 中疑似 PDF 的地址；
       - 对 arXiv(/abs/→/pdf/)、PMC(/articles/PMCxxx/→/pdf/) 做链接推导；
    4. 按候选顺序逐个尝试下载，成功即止（sha256 内容去重）；
    5. 全部失败 → 写入 need_manual_download.csv（doi,title,publication_year,reason），
       并按 publication_year 降序排列（近年文献工况/表征更统一，优先处理）；
    6. 打印汇总：命中数 / 探测数 / 已下载数 / 待办数。

Unpaywall 备注:
    - GET https://api.unpaywall.org/v2/{doi}?email=...
    - 免费、无需 Key，但要求 email 参数；200 命中，404 表示库中无此 DOI
    - is_oa=false 表示该 DOI 无任何合法开放版本
"""
import argparse
import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import requests
import yaml

PDF_MAGIC = b"%PDF"
TIMEOUT = 60
SLEEP = 0.4
UA = "ccus-lit-crawler/0.1 (research use; local only)"


def load_unpaywall_config(config_path):
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    u = cfg.get("unpaywall") or {}
    base = u.get("base_url", "https://api.unpaywall.org/v2")
    email = (u.get("email") or "").strip() or "research-agent@localhost"
    return base, email


def sanitize_doi(doi):
    return re.sub(r"[^\w.\-]+", "_", doi).strip("_")[:120]


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def build_existing_index(outdir):
    """返回 (hash_idx, doi_idx)；doi_idx 用于按 DOI 去重，避免同文不同哈希重复下载。"""
    hashes, dois = {}, set()
    for p in Path(outdir).glob("*.pdf"):
        try:
            hashes[sha256_bytes(p.read_bytes())] = p.name
        except Exception:
            pass
        stem = p.stem
        if "_" in stem:
            dois.add(stem.rsplit("_", 1)[0])
    return hashes, dois


def derive_pdf_candidates(loc):
    """从一个 Unpaywall location 推导所有值得尝试的 PDF 直链（按优先级）。"""
    out = []
    if loc.get("url_for_pdf"):
        out.append(loc["url_for_pdf"])
    for key in ("url", "url_for_landing_page"):
        u = loc.get(key)
        if not u:
            continue
        low = u.lower()
        tail = low.rstrip("/").rsplit("/", 1)[-1]
        if low.endswith(".pdf") or tail.endswith(".pdf") or "/pdf/" in low or tail == "pdf":
            out.append(u)
        if "arxiv.org/abs/" in low:                       # arXiv 摘要页 -> PDF
            out.append(u.replace("/abs/", "/pdf/"))
        elif "arxiv.org/pdf/" in low and not low.endswith(".pdf"):
            out.append(u.rstrip("/") + ".pdf")
        if "pmc.ncbi.nlm.nih.gov/articles/" in low and not low.rstrip("/").endswith("/pdf"):
            out.append(u.rstrip("/") + "/pdf/")           # PMC 文章页 -> PDF
    return list(dict.fromkeys(out))                       # 去重且保序


def unpaywall_probe(base, email, doi):
    """返回 (candidates: list[str], status: str)。"""
    url = f"{base.rstrip('/')}/{doi}?email={requests.utils.quote(email)}"
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    if resp.status_code == 404:
        return [], "unpaywall_404"
    if resp.status_code != 200:
        return [], f"http_{resp.status_code}"
    data = resp.json()
    if not data.get("is_oa"):
        return [], "not_oa"
    locs = []
    if data.get("best_oa_location"):
        locs.append(data["best_oa_location"])
    locs.extend(data.get("oa_locations") or [])
    cands = []
    for loc in locs:
        cands.extend(derive_pdf_candidates(loc))
    cands = list(dict.fromkeys(cands))
    return cands, ("ok" if cands else "oa_no_pdf")


def download_pdf(url, outdir, doi, hash_idx):
    resp = requests.get(url, timeout=TIMEOUT, allow_redirects=True,
                        headers={"User-Agent": UA})
    resp.raise_for_status()
    body = resp.content
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if not body.startswith(PDF_MAGIC) and "pdf" not in ctype:
        raise ValueError("响应不是 PDF")
    digest = sha256_bytes(body)
    if digest in hash_idx:
        return hash_idx[digest], "skipped_duplicate"
    name = f"{sanitize_doi(doi)}_{digest[:8]}.pdf"
    (Path(outdir) / name).write_bytes(body)
    hash_idx[digest] = name
    return name, "downloaded"


def main():
    ap = argparse.ArgumentParser(description="Unpaywall OA 全文探测 + 下载 + 待办导出")
    ap.add_argument("--results", default="data/raw_pdfs/elsevier_results.json")
    ap.add_argument("--outdir", default="data/raw_pdfs")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    rpath = Path(args.results)
    if not rpath.exists():
        sys.exit(f"[ERR] 找不到检索结果 {rpath}；请先运行 fetch_elsevier.py")
    data = json.loads(rpath.read_text(encoding="utf-8"))
    entries = data.get("entries") or []

    base, email = load_unpaywall_config(args.config)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    hash_idx, doi_idx = build_existing_index(outdir)

    with_doi = [e for e in entries if e.get("doi")]
    print(f"[1/4] 共 {len(entries)} 篇；带 DOI、将全部探测 Unpaywall: {len(with_doi)} 篇")

    downloaded, manual = [], []
    for e in entries:
        doi = e.get("doi")
        title = (e.get("title") or "").strip()
        year = e.get("publication_year")
        if not doi:
            manual.append(("", title, year, "无 DOI"))
            continue
        if sanitize_doi(doi) in doi_idx:
            downloaded.append((doi, title, "(已有本地文件)", "skipped_existing"))
            continue
        time.sleep(SLEEP)
        try:
            cands, status = unpaywall_probe(base, email, doi)
        except Exception as ex:
            manual.append((doi, title, year, f"Unpaywall 异常: {ex}"))
            continue
        if not cands:
            manual.append((doi, title, year, f"无可用直链({status})"))
            continue
        errs, ok = [], False
        for cu in cands:
            time.sleep(SLEEP)
            try:
                fname, dstatus = download_pdf(cu, outdir, doi, hash_idx)
                downloaded.append((doi, title, fname, dstatus))
                ok = True
                break
            except Exception as ex:
                errs.append(str(ex)[:60])
        if not ok:
            manual.append((doi, title, year, f"候选全部失败: {'; '.join(errs)[:120]}"))

    csv_path = outdir / "need_manual_download.csv"
    # 年份降序；无年份的排到最后
    manual.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["doi", "title", "publication_year", "reason"])
        w.writerows(manual)

    print(f"[2/4] 成功下载 OA 全文: {len(downloaded)} 篇 → {outdir}")
    for doi, title, fname, dstatus in downloaded:
        print(f"      [{dstatus}] {fname}  <- {title[:60]}")
    print(f"[3/4] 待人工下载: {len(manual)} 篇 → {csv_path}")
    print(f"[4/4] 汇总: 命中 {len(entries)} | 探测 {len(with_doi)} | 已下 {len(downloaded)} | 待办 {len(manual)}")


if __name__ == "__main__":
    main()
