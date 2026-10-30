"""按版面坐标重建表格：同一 y 带的 words 归为一行，按 x0 排序输出列位置。

用法: python tools/_wordgrid.py <pdf> <page> [y0 y1 x0 x1]
"""
import sys

import pdfplumber

pdf_path, pno = sys.argv[1], int(sys.argv[2])
y0 = float(sys.argv[3]) if len(sys.argv) > 3 else 0
y1 = float(sys.argv[4]) if len(sys.argv) > 4 else 1e9
x0 = float(sys.argv[5]) if len(sys.argv) > 5 else 0
x1 = float(sys.argv[6]) if len(sys.argv) > 6 else 1e9

with pdfplumber.open(pdf_path) as pdf:
    page = pdf.pages[pno - 1]
    words = [w for w in page.extract_words(use_text_flow=False, keep_blank_chars=False)
             if y0 <= w["top"] <= y1 and x0 <= w["x0"] <= x1]
    words.sort(key=lambda w: (round(w["top"] / 4), w["x0"]))
    cur_top, row = None, []
    for w in words:
        band = round(w["top"] / 4)
        if cur_top is None or band == cur_top:
            row.append(w)
            cur_top = band
        else:
            print(f"y={row[0]['top']:7.1f} | " +
                  "  ".join(f"{x['text']}@{x['x0']:.0f}" for x in row))
            row, cur_top = [w], band
    if row:
        print(f"y={row[0]['top']:7.1f} | " +
              "  ".join(f"{x['text']}@{x['x0']:.0f}" for x in row))
