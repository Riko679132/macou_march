"""
pdf_to_text.py —— 自动采集模式：PDF 全文解析（骨架版 v0.1）

用法:
    单篇: python scripts/pdf_to_text.py --pdf data/raw_pdfs/xxx.pdf [--outdir extractions/parsed]
    批量: python scripts/pdf_to_text.py --indir data/raw_pdfs        [--outdir extractions/parsed]

行为:
    1. 用 pdfplumber 逐页 extract_text()；
    2. 每个 PDF 生成同名 .txt，页与页之间用 "--- Page N ---" 分隔；
    3. 输出 sha256 / 页数 / 字符数；每页平均不足 50 字符视为疑似扫描件，needs_ocr=True；
    4. 每次运行末尾输出 parse_report.json；
    5. 不触碰数据库，不修改原 PDF。

TODO:
    - 扫描件 OCR（可选接 pytesseract）；
    - 表格结构提取（pdfplumber 有 extract_tables，抽取时若需保留表格可加）。
"""
import argparse
import hashlib
import json
from pathlib import Path

import pdfplumber


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_one(pdf_path, out_path):
    """把单个 PDF 解析为 txt。返回 dict 报告项。"""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages, 1):
            try:
                txt = page.extract_text() or ""
            except Exception as e:
                txt = f"[page {i} extract failed: {e}]"
            pages.append(f"--- Page {i} ---\n{txt}")
    text = "\n\n".join(pages)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(text, encoding="utf-8")
    n_chars = len(text)
    return {
        "pdf": str(pdf_path),
        "txt": str(out_path),
        "sha256": sha256_file(pdf_path),
        "pages": n_pages,
        "chars": n_chars,
        "needs_ocr": n_chars < 50 * max(n_pages, 1),
        "status": "ok",
    }


def collect_pdfs(pdf_arg, indir_arg):
    if pdf_arg:
        return [Path(pdf_arg)]
    return sorted(Path(indir_arg).glob("*.pdf"))


def main():
    ap = argparse.ArgumentParser(description="PDF 全文解析（骨架）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--pdf", help="单个 PDF 文件路径")
    g.add_argument("--indir", help="包含 PDF 的目录（批量）")
    ap.add_argument("--outdir", default="extractions/parsed",
                    help="txt 输出目录（默认 extractions/parsed）")
    args = ap.parse_args()

    files = collect_pdfs(args.pdf, args.indir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    results = []
    for f in files:
        try:
            r = extract_one(f, outdir / (f.stem + ".txt"))
        except Exception as e:
            r = {"pdf": str(f), "status": "failed", "error": str(e)}
        results.append(r)
        tag = "  [needs_ocr]" if r.get("needs_ocr") else ""
        print(f"  {r['status']:>7}  {f.name}  "
              f"pages={r.get('pages','-')}  chars={r.get('chars','-')}{tag}")

    report_path = outdir / "parse_report.json"
    # 逐篇调用时会重复运行本脚本，报告按 pdf 路径合并而不是整体覆盖，
    # 否则一个批次里只有最后一次运行的那篇留档。
    merged = {}
    if report_path.exists():
        try:
            for r in json.loads(report_path.read_text(encoding="utf-8")):
                merged[r.get("pdf")] = r
        except Exception:
            pass
    for r in results:
        merged[r.get("pdf")] = r
    report_path.write_text(
        json.dumps(list(merged.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_ocr = sum(1 for r in results if r.get("needs_ocr"))
    print(f"共 {len(files)} 个 PDF：成功 {n_ok}，疑似扫描件 {n_ocr}，"
          f"本批次报告累计 {len(merged)} 篇 → {report_path}")


if __name__ == "__main__":
    main()
