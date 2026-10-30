#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
初始化 macau.sqlite —— 澳门绿色通关与碳足迹评估系统专用。

安全护栏：本脚本只允许操作 macau_eco_travel/data/macau.sqlite，
路径中若出现 ccus 则直接拒绝执行，确保与父项目数据库物理隔离。

用法：
    python scripts/init_db_macau.py
    python scripts/init_db_macau.py --reset   # 归档旧库后重建（不删除）
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(PROJECT_ROOT, "data", "macau.sqlite")
SCHEMA_PATH = os.path.join(HERE, "schema_macau.sql")
BACKUP_DIR = os.path.join(PROJECT_ROOT, "data", "backups")

TABLES = ("customs_rules", "carbon_factors", "search_logs", "local_products",
          "pending_products", "known_products")


def guard(path: str) -> None:
    """绝不允许误指向父项目的 ccus 库。"""
    if "ccus" in os.path.basename(path).lower():
        raise SystemExit("[ABORT] 目标库名含 ccus，禁止操作父项目数据库：%s" % path)
    if os.path.basename(path).lower() != "macau.sqlite":
        raise SystemExit("[ABORT] 本脚本仅用于 macau.sqlite，收到：%s" % path)


def archive(db_path: str) -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, "macau_%s.sqlite" % stamp)
    shutil.copy2(db_path, dst)
    return dst


def init(db_path: str, schema_path: str) -> int:
    guard(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with open(schema_path, "r", encoding="utf-8") as fh:
        ddl = fh.read()

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(ddl)
        conn.commit()
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        print("[OK] 数据库：%s" % db_path)
        print("[OK] 已建表：%s" % ", ".join(tables))
        for t in TABLES:
            n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            print("     - %-15s %d 行" % (t, n))
        missing = [t for t in TABLES if t not in tables]
        if missing:
            print("[FAIL] 缺表：%s" % ", ".join(missing))
            return 1
        return 0
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="初始化 macau.sqlite")
    ap.add_argument("--reset", action="store_true",
                    help="重建前先把旧库归档到 data/backups/（不删除文件）")
    args = ap.parse_args()

    if args.reset and os.path.exists(DB_PATH):
        print("[ARCHIVE] 旧库已归档 -> %s" % archive(DB_PATH))

    print("[SCHEMA] %s" % SCHEMA_PATH)
    return init(DB_PATH, SCHEMA_PATH)


if __name__ == "__main__":
    sys.exit(main())
