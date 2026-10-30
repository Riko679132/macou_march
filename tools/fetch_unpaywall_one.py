"""对指定 DOI 逐个跑 Unpaywall 探测 + 下载，复用 scripts/fetch_fulltext.py 的实现。

不动 need_manual_download.csv —— 那个文件由全量批处理负责。
用法: .\.venv\Scripts\python.exe tools\fetch_unpaywall_one.py DOI [DOI ...]
     （本脚本 exec 了 scripts/fetch_fulltext.py，需要 requests + pyyaml，
       这两个只装在根 .venv；tools/.venv 只有 playwright/pypdf，跑不起来）
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("fetch_fulltext", "scripts/fetch_fulltext.py")
ff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ff)

OUT = Path("data/raw_pdfs")


def main(dois):
    base, email = ff.load_unpaywall_config("config.yaml")
    hash_idx, doi_idx = ff.build_existing_index(OUT)
    results = []
    for doi in dois:
        if ff.sanitize_doi(doi) in doi_idx:
            print(f"[skip] 已有本地文件: {doi}")
            results.append({"doi": doi, "status": "skipped_existing"})
            continue
        time.sleep(ff.SLEEP)
        try:
            cands, status = ff.unpaywall_probe(base, email, doi)
        except Exception as exc:
            print(f"[err ] {doi}: {type(exc).__name__}: {exc}")
            results.append({"doi": doi, "status": "error", "error": str(exc)[:120]})
            continue
        if not cands:
            print(f"[none] {doi}: {status}")
            results.append({"doi": doi, "status": status})
            continue
        got = None
        errs = []
        for cu in cands:
            time.sleep(ff.SLEEP)
            try:
                name, dstatus = ff.download_pdf(cu, OUT, doi, hash_idx)
                got = {"doi": doi, "status": dstatus, "file": name, "via": cu[:110]}
                break
            except Exception as exc:
                errs.append(f"{type(exc).__name__}: {exc}"[:80])
        if got:
            print(f"[ok  ] {got}")
            results.append(got)
        else:
            print(f"[fail] {doi}: {'; '.join(errs)[:160]}")
            results.append({"doi": doi, "status": "cand_failed", "errors": errs})
    ok = sum(1 for r in results if r["status"] in ("downloaded", "skipped_duplicate",
                                                   "skipped_existing"))
    print(f"\nSUMMARY ok={ok}/{len(dois)}")
    print(json.dumps(results, ensure_ascii=False, indent=2))


main(sys.argv[1:])
