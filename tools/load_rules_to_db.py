#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
load_rules_to_db.py —— 把 customs_rules.jsonl 语料写入 macau.sqlite::customs_rules

设计要点（对齐 AGENTS.md 红线）：
    1. 无 source_ref 的行一律拒收 —— 语料必须可溯源到官网页面。
    2. limit_value 只在原文出现明确「数字+单位」时才填，且把命中的原文片段写进
       limit_evidence；解析不出就不填（NULL），绝不猜。
    3. 按 (source_ref, text_sha256) 生成 dedup_hash 唯一索引，重复执行幂等。

用法：
    python tools/load_rules_to_db.py --dry-run
    python tools/load_rules_to_db.py
    python tools/load_rules_to_db.py --jsonl knowledge/customs_rules.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(PROJECT_ROOT, "data", "macau.sqlite")
DEFAULT_JSONL = os.path.join(PROJECT_ROOT, "knowledge", "customs_rules.jsonl")

VALID_DIRECTIONS = {"inbound_macau", "outbound_macau", "inbound_mainland",
                    "outbound_mainland", "bidirectional"}

# 方向推断：原文/标签里的方向词
DIRECTION_HINTS = [
    ("inbound_macau", ("入境澳門", "入境澳门", "來澳", "来澳", "進入澳門", "运进")),
    ("inbound_mainland", ("入境內地", "入境内地", "进境", "运进", "內地旅客", "内地")),
    ("outbound_macau", ("離澳", "离澳", "出境澳門", "出口")),
    ("outbound_mainland", ("出境", "出运")),
]

# rule_type 关键词（按优先级：禁止 > 限制 > 申报 > 免税）
RULE_TYPE_HINTS = [
    ("prohibited", ("禁止", "不得攜帶", "不得携带", "不准", " confiscated", "充公", "銷毀", "销毁")),
    ("restricted", ("限", "不得超過", "不得超过", "僅限", "仅限", "額度", "額度", "上限")),
    ("declaration", ("申報", "申报", "報關", "声明")),
    ("duty_free", ("免稅", "免税", "豁免", "自由港")),
]

# 中文数字解析（如“十二萬”→“120000”，“十九”→“19”）
CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3,
            "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}
CN_BIG = {"萬": 10000, "万": 10000, "億": 100000000, "亿": 100000000}
CN_AMOUNT = r"[零〇一二两兩三四五六七八九十百千万萬億亿]+"


def cn_num_to_int(s: str) -> Optional[int]:
    """中文数字转整数；含非法字符返回 None（绝不猜）。"""
    total = section = num = 0
    for ch in s:
        if ch in CN_DIGITS:
            num = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            section += (num or 1) * CN_UNITS[ch]
            num = 0
        elif ch in CN_BIG:
            section = (section + num) * CN_BIG[ch]
            total += section
            section = num = 0
        else:
            return None
    return total + section + num


# 限额抽取：阿拉伯/中文数字 + 单位（保守匹配，命中片段整体留证）
COUNT_UNITS = r"支|條|条|盒|包|公斤|千克|克|毫升|升"
MONEY_UNITS = r"澳門元|澳门元|馬幣|马币|葡元|人民幣元|人民币元|元"
LIMIT_PATTERNS = [
    ("count_arabic", re.compile(r"([\d,，]+(?:\.\d+)?)\s*(%s)" % COUNT_UNITS)),
    ("count_cn", re.compile(r"(%s)\s*(%s)" % (CN_AMOUNT, COUNT_UNITS))),
    ("money_arabic", re.compile(r"([\d,，]+(?:\.\d+)?)\s*(萬|万)?\s*(%s)" % MONEY_UNITS)),
    ("money_cn", re.compile(r"(%s)\s*(萬|万)?\s*(%s)" % (CN_AMOUNT, MONEY_UNITS))),
]


def norm_number(raw: str) -> Optional[float]:
    s = raw.replace(",", "").replace("，", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def extract_limit(text: str) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """返回 (limit_value, limit_unit, 命中原文片段)；匹不到则全 None。"""
    for kind, pat in LIMIT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        if kind in ("count_arabic", "money_arabic"):
            num = norm_number(m.group(1))
        else:
            num = cn_num_to_int(m.group(1))
        if num is None:
            continue
        if kind.startswith("count"):
            unit = m.group(2)
        else:   # money：第2组是可选「萬」，第3组才是币种
            if m.group(2) in ("萬", "万"):
                num *= 10000
            unit = m.group(3)
        return float(num), unit or None, m.group(0)
    return None, None, None


def infer_rule_type(text: str) -> str:
    for rt, words in RULE_TYPE_HINTS:
        if any(w in text for w in words):
            return rt
    return "declaration"


def infer_direction(text: str, hint: Optional[str]) -> str:
    if hint in VALID_DIRECTIONS:
        return hint
    for d, words in DIRECTION_HINTS:
        if any(w in text for w in words):
            return d
    return "bidirectional"


def ensure_columns(conn: sqlite3.Connection) -> None:
    """已存在的库补上溯源列（新建库由 schema_macau.sql 直接带上）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(customs_rules)")}
    for name, ddl in (("dedup_hash", "TEXT"), ("limit_evidence", "TEXT")):
        if name not in cols:
            conn.execute("ALTER TABLE customs_rules ADD COLUMN %s %s" % (name, ddl))
            print("[MIGRATE] 新增列 customs_rules.%s" % name)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_dedup "
        "ON customs_rules(dedup_hash)")


def to_row(rec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    text = (rec.get("text") or "").strip()
    source_ref = (rec.get("source_ref") or "").strip()
    if not text or not source_ref:
        return None
    limit_value, limit_unit, evidence = extract_limit(text)
    dedup = hashlib.sha256(
        ("%s|%s" % (source_ref, rec.get("text_sha256") or hashlib.sha256(
            text.encode("utf-8")).hexdigest())).encode("utf-8")).hexdigest()
    return {
        "direction": infer_direction(text, rec.get("direction")),
        "item_category": rec.get("item_category") or "未分类",
        "item_subtype": None,
        "rule_type": "prohibited" if "禁止" in (rec.get("item_category") or "")
        else infer_rule_type(text),
        "limit_value": limit_value,
        "limit_unit": limit_unit,
        "penalty_clause": None,
        "authority": rec.get("authority"),
        "effective_date": None,
        "source_ref": source_ref,
        "raw_text": text,
        "dedup_hash": dedup,
        "limit_evidence": evidence,
    }


INSERT_SQL = """
INSERT OR IGNORE INTO customs_rules
    (direction, item_category, item_subtype, rule_type, limit_value, limit_unit,
     penalty_clause, authority, effective_date, source_ref, raw_text,
     dedup_hash, limit_evidence)
VALUES (:direction, :item_category, :item_subtype, :rule_type, :limit_value,
        :limit_unit, :penalty_clause, :authority, :effective_date, :source_ref,
        :raw_text, :dedup_hash, :limit_evidence)
"""


def refresh_limits(conn: sqlite3.Connection) -> None:
    """对已入库记录重算 limit_value/limit_unit/limit_evidence（中文数字解析升级后回填）。"""
    rows = conn.execute(
        "SELECT rule_id, raw_text FROM customs_rules WHERE raw_text IS NOT NULL").fetchall()
    updated = 0
    for rule_id, raw in rows:
        v, u, e = extract_limit(raw or "")
        cur = conn.execute(
            "SELECT limit_value, limit_unit, limit_evidence FROM customs_rules WHERE rule_id=?",
            (rule_id,)).fetchone()
        if (v, u, e) == (cur[0], cur[1], cur[2]):
            continue
        conn.execute(
            "UPDATE customs_rules SET limit_value=?, limit_unit=?, limit_evidence=? "
            "WHERE rule_id=?", (v, u, e, rule_id))
        updated += 1
        print("[REFRESH] rule_id=%d limit=%s%s evidence=%s"
              % (rule_id, v, u or "", (e or "-")[:20]))
    conn.commit()
    print("[REFRESH] 限额回填完成：%d/%d 行更新" % (updated, len(rows)))


def main() -> int:
    ap = argparse.ArgumentParser(description="海关法条语料入库 macau.sqlite")
    ap.add_argument("--jsonl", default=DEFAULT_JSONL, help="语料文件路径")
    ap.add_argument("--db", default=DB_PATH, help="目标数据库（仅限 macau.sqlite）")
    ap.add_argument("--dry-run", action="store_true", help="只解析预览，不写库")
    ap.add_argument("--max-chars", type=int, default=1200, help="raw_text 入库长度截断")
    ap.add_argument("--refresh-limits", action="store_true",
                    help="不导入新数据，仅对已入库记录重算限额并回填")
    args = ap.parse_args()

    if os.path.basename(args.db).lower() != "macau.sqlite":
        print("[ABORT] 本脚本只允许写 macau.sqlite，收到：%s" % args.db)
        return 1

    if args.refresh_limits:
        conn = sqlite3.connect(args.db)
        try:
            ensure_columns(conn)
            refresh_limits(conn)
        finally:
            conn.close()
        return 0

    if not os.path.exists(args.jsonl):
        print("[ABORT] 语料文件不存在：%s（先跑 fetch_customs_rules.py）" % args.jsonl)
        return 1

    rows, skipped_no_ref, dup = [], 0, 0
    seen_dedup = set()
    with open(args.jsonl, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                print("[WARN] 第 %d 行 JSON 解析失败：%s" % (lineno, exc))
                continue
            row = to_row(rec)
            if not row:
                skipped_no_ref += 1
                continue
            if row["dedup_hash"] in seen_dedup:
                dup += 1
                continue
            seen_dedup.add(row["dedup_hash"])
            if len(row["raw_text"]) > args.max_chars:
                row["raw_text"] = row["raw_text"][:args.max_chars] + "…"
            rows.append(row)

    print("[PARSE] 有效 %d 条 | 缺 source_ref 拒收 %d 条 | 文件内重复 %d 条"
          % (len(rows), skipped_no_ref, dup))
    for row in rows[:8]:
        print("   - [%s/%s] limit=%s%s evidence=%s | %s"
              % (row["direction"], row["rule_type"], row["limit_value"],
                 row["limit_unit"] or "", (row["limit_evidence"] or "-")[:20],
                 row["raw_text"][:50].replace("\n", " ")))

    if args.dry_run:
        print("[DRY-RUN] 未写库")
        return 0

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    conn = sqlite3.connect(args.db)
    try:
        ensure_columns(conn)
        before = conn.execute("SELECT COUNT(*) FROM customs_rules").fetchone()[0]
        conn.executemany(INSERT_SQL, rows)
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM customs_rules").fetchone()[0]
        print("[WRITE] customs_rules：%d -> %d 行（新增 %d，命中唯一索引跳过 %d）"
              % (before, after, after - before, len(rows) - (after - before)))
        for rt, n in conn.execute(
                "SELECT rule_type, COUNT(*) FROM customs_rules GROUP BY rule_type"):
            print("        rule_type %-12s %d" % (rt, n))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
