"""pypdf 复核下载到的 PDF：首字节 %PDF-、尾部 %%EOF、页数 > 0、首页文本命中标题关键词。

fetch_*_cdp.py 打印的 pages 基于明文 "/Type /Page" 计数，ACS 等使用对象流的 PDF 会显示 0，
以本脚本结果为准。

用法:
  python tools/verify_pdfs.py                      # 复核 data/raw_pdfs 下全部 PDF
  python tools/verify_pdfs.py a.pdf b.pdf          # 复核指定文件
  python tools/verify_pdfs.py --doi 10.1021/xxx    # 按 DOI 找文件复核
"""
import argparse
import sys
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data" / "raw_pdfs"


def check(path: Path) -> dict:
    raw = path.read_bytes()
    out = {
        "file": path.name,
        "size": len(raw),
        "head_ok": raw[:5] == b"%PDF-",
        "tail_ok": b"%%EOF" in raw[-2048:],
        "pages": 0,
        "first_page_chars": 0,
        "error": "",
    }
    try:
        reader = PdfReader(str(path))
        out["pages"] = len(reader.pages)
        txt = (reader.pages[0].extract_text() or "").strip()
        out["first_page_chars"] = len(txt)
        out["first_page_head"] = " ".join(txt.split())[:90]
    except Exception as exc:  # noqa: BLE001 - 目的是报告而不是中断批处理
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--doi", nargs="*", default=None, help="按 DOI 定位（/ 换 _，前缀匹配）")
    args = ap.parse_args()

    if args.doi:
        targets = []
        for doi in args.doi:
            stem = doi.replace("/", "_")
            found = sorted(PDF_DIR.glob(stem + "*.pdf"))
            if not found:
                print("MISSING:", doi)
                continue
            targets.extend(found)
    elif args.files:
        targets = [Path(f) for f in args.files]
    else:
        targets = sorted(PDF_DIR.glob("*.pdf"))

    bad = 0
    for p in targets:
        r = check(p)
        ok = r["head_ok"] and r["tail_ok"] and r["pages"] > 0 and r["first_page_chars"] > 50 and not r["error"]
        if not ok:
            bad += 1
        print("[%s] %-58s %9d B  %2d p  head=%s tail=%s  %s"
              % ("OK " if ok else "BAD", r["file"], r["size"], r["pages"],
                 r["head_ok"], r["tail_ok"], r["error"] or ""))
        if r["first_page_chars"]:
            print("       %s" % r.get("first_page_head", ""))

    print("\nchecked=%d bad=%d" % (len(targets), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
