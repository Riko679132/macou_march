# -*- coding: utf-8 -*-
"""把 macau.sqlite 的 customs_rules 导出为 agent_brain 的 RAG 语料 knowledge/customs_rules_db.txt。

标签格式：<方向中文>-<item_category>，方向映射与 schema 注释一致：
    inbound_macau   -> 内地入境澳门
    outbound_macau  -> 澳门入境内地
    inbound_mainland-> 澳门入境内地
    outbound_mainland-> 内地出境
    bidirectional   -> 双向
用法：python tools/export_knowledge_txt.py [--db data/macau.sqlite]
"""
import argparse
import os
import sqlite3
import sys

DIR_LABEL = {
    "inbound_macau": "内地入境澳门",
    "outbound_macau": "澳门入境内地",
    "inbound_mainland": "澳门入境内地",
    "outbound_mainland": "内地出境",
    "bidirectional": "双向",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/macau.sqlite")
    ap.add_argument("--out", default="knowledge/customs_rules_db.txt")
    ap.add_argument("--max-chars", type=int, default=600)
    args = ap.parse_args()

    if os.path.basename(args.db).lower() != "macau.sqlite":
        print("[ABORT] 只允许读 macau.sqlite，收到：%s" % args.db)
        return 1

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT direction, item_category, rule_type, limit_value, limit_unit, raw_text "
        "FROM customs_rules WHERE is_active=1 AND raw_text IS NOT NULL ORDER BY rule_id"
    ).fetchall()
    conn.close()

    lines = ["# 由 tools/export_knowledge_txt.py 自动生成 —— 内容来自 customs_rules 表（原文入库存留证）",
             "# 标签格式：<方向中文>-<item_category>[·限额]，正文为法条原文"]
    seen = set()
    n = 0
    for direction, cat, rt, lim, unit, raw in rows:
        label = DIR_LABEL.get(direction, "双向") + "-" + (cat or "未分类")
        if lim is not None:
            label += "·限%s%s" % (("%g" % lim), unit or "")
        key = label + raw
        if key in seen:
            continue
        seen.add(key)
        body = raw.replace("\n", " ").strip()
        if len(body) > args.max_chars:
            body = body[:args.max_chars] + "…"
        lines.append("%s: %s" % (label, body))
        n += 1
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("[EXPORT] %d 条语料 -> %s" % (n, args.out))
    for ln in lines[3:9]:
        print("   ", ln[:90])
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
