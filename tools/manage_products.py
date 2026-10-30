#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
local_products 本地商品资料库维护脚本。

平替建议的唯一数据源：agent_brain.py 会从这张表动态读取商品再喂给大模型，
模型不得虚构库中不存在的商品名/品牌。

数据质量红线：
- 商品名称/购买地点/卖点须为真实存在的澳门特产（不得编造品牌）；
- carbon_footprint 为估算测试值时必须意识到正式上线前需替换为权威 LCA 来源。

用法：
    python tools/manage_products.py --seed          # 幂等写入 5 条澳门特产测试数据
    python tools/manage_products.py --list          # 查看全部商品
    python tools/manage_products.py --list --category 糕点
    python tools/manage_products.py --add "杏仁饼,糕点,1.5,绿码/安全,大三巴街,澳门老字号"
    python tools/manage_products.py --update 3 --set "carbon_footprint=1.2,purchase_location=官也街"
    python tools/manage_products.py --delete 4      # 删除前会要求 --yes 二次确认
    python tools/manage_products.py --import-csv products.csv
    python tools/manage_products.py --export-csv products.csv

CSV 表头（与 --add 参数顺序一致）：
    name,category,carbon_footprint,compliance_risk,purchase_location,description
"""
import argparse
import csv
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(PROJECT_ROOT, "data", "macau.sqlite")

DDL = """
CREATE TABLE IF NOT EXISTS local_products (
    product_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL UNIQUE,
    category          TEXT    NOT NULL,
    carbon_footprint  REAL    NOT NULL CHECK (carbon_footprint >= 0),
    compliance_risk   TEXT    NOT NULL DEFAULT '绿码/安全',
    purchase_location TEXT,
    description       TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_products_category ON local_products(category);
"""

# 5 条真实澳门特产测试数据（碳排为估算值，待权威 LCA 来源替换）
SEED_PRODUCTS = [
    ("钜记饼家 手工杏仁饼", "糕点", 1.5, "绿码/安全",
     "新马路福隆新街70号（大三巴街分店）", "澳门老字号，纯手工制作，送礼首选"),
    ("咀香园饼家 凤凰卷", "糕点", 1.8, "绿码/安全",
     "大三巴街", "百年老店，手工蛋卷，酥香可口"),
    ("澳门本地海味干货礼盒（瑶柱/虾米）", "海味", 0.8, "绿码/安全",
     "下环街市/官也街", "本土海产干货，低脂高蛋白，正规商户销售"),
    ("澳门葡挞磁贴 文创手信", "文创", 0.5, "绿码/安全",
     "官也街", "大三巴/葡挞主题纪念品，轻便合规"),
    ("乐宫饼家 牛油曲奇", "糕点", 2.0, "绿码/安全",
     "南湾大马路", "澳门怀旧曲奇，送礼佳品"),
]

FIELDS = ("name", "category", "carbon_footprint", "compliance_risk",
          "purchase_location", "description")


def guard(path: str) -> None:
    """绝不允许误指向父项目的 ccus 库；只允许操作 macau.sqlite。"""
    if "ccus" in os.path.basename(path).lower():
        raise SystemExit("[ABORT] 目标库名含 ccus，禁止操作父项目数据库：%s" % path)
    if os.path.basename(path).lower() != "macau.sqlite":
        raise SystemExit("[ABORT] 本脚本仅用于 macau.sqlite，收到：%s" % path)


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    guard(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)   # 幂等建表，脚本可独立于 init_db_macau.py 运行
    conn.commit()
    return conn


def parse_record(text: str):
    """把 'a,b,c,...' 解析成字段 tuple，校验 6 列且碳排为数值。"""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 6:
        raise SystemExit("[ABORT] 需要 6 列（逗号分隔）：%s" % "、".join(FIELDS))
    try:
        cf = float(parts[2])
    except ValueError:
        raise SystemExit("[ABORT] carbon_footprint 必须是数值，收到：%r" % parts[2])
    if cf < 0:
        raise SystemExit("[ABORT] carbon_footprint 不得为负数：%r" % parts[2])
    return (parts[0], parts[1], cf, parts[3], parts[4], parts[5])


def cmd_seed(conn: sqlite3.Connection) -> int:
    n_new, n_skip = 0, 0
    for rec in SEED_PRODUCTS:
        cur = conn.execute("SELECT 1 FROM local_products WHERE name = ?", (rec[0],))
        if cur.fetchone():
            n_skip += 1
            continue
        conn.execute(
            "INSERT INTO local_products (name, category, carbon_footprint, compliance_risk, "
            "purchase_location, description) VALUES (?,?,?,?,?,?)", rec)
        n_new += 1
    conn.commit()
    print("[OK] 种子数据：新增 %d 条，已存在跳过 %d 条" % (n_new, n_skip))
    return 0


def cmd_list(conn: sqlite3.Connection, category: str = None) -> int:
    if category:
        rows = conn.execute(
            "SELECT * FROM local_products WHERE category = ? ORDER BY product_id",
            (category,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM local_products ORDER BY product_id").fetchall()
    if not rows:
        print("[EMPTY] 商品库为空，先跑 --seed 或 --add / --import-csv")
        return 0
    for r in rows:
        print("[%d] %s | 类别:%s | 碳排:%.2f kg CO2e/kg | 合规:%s | 地点:%s | %s"
              % (r["product_id"], r["name"], r["category"], r["carbon_footprint"],
                 r["compliance_risk"], r["purchase_location"] or "-", r["description"] or ""))
    print("[OK] 共 %d 条" % len(rows))
    return 0


def cmd_add(conn: sqlite3.Connection, text: str) -> int:
    rec = parse_record(text)
    try:
        conn.execute(
            "INSERT INTO local_products (name, category, carbon_footprint, compliance_risk, "
            "purchase_location, description) VALUES (?,?,?,?,?,?)", rec)
        conn.commit()
    except sqlite3.IntegrityError as exc:
        print("[ABORT] 写入失败（商品名重复？）：%s" % exc)
        return 1
    print("[OK] 已添加：%s" % rec[0])
    return 0


def cmd_update(conn: sqlite3.Connection, pid: int, sets: str) -> int:
    cur = conn.execute("SELECT * FROM local_products WHERE product_id = ?", (pid,))
    if not cur.fetchone():
        print("[ABORT] product_id=%d 不存在" % pid)
        return 1
    pairs, sql_parts, vals = {}, [], []
    for item in sets.split(","):
        if "=" not in item:
            print("[ABORT] --set 格式应为 key=value,...，收到：%r" % item)
            return 1
        k, v = [x.strip() for x in item.split("=", 1)]
        if k not in FIELDS:
            print("[ABORT] 未知字段 %s（可选：%s）" % (k, "、".join(FIELDS)))
            return 1
        if k == "carbon_footprint":
            v = float(v)
            if v < 0:
                print("[ABORT] carbon_footprint 不得为负数")
                return 1
        sql_parts.append("%s = ?" % k)
        vals.append(v)
    conn.execute("UPDATE local_products SET %s WHERE product_id = ?"
                 % ", ".join(sql_parts), vals + [pid])
    conn.commit()
    print("[OK] 已更新 product_id=%d：%s" % (pid, sets))
    return 0


def cmd_delete(conn: sqlite3.Connection, pid: int, yes: bool) -> int:
    if not yes:
        print("[ABORT] 删除需二次确认：加 --yes")
        return 1
    conn.execute("DELETE FROM local_products WHERE product_id = ?", (pid,))
    conn.commit()
    print("[OK] 已删除 product_id=%d（若不存在则为空操作）" % pid)
    return 0


def cmd_import_csv(conn: sqlite3.Connection, path: str) -> int:
    if not os.path.isfile(path):
        print("[ABORT] CSV 不存在：%s" % path)
        return 1
    n_new, n_upd, n_err = 0, 0, 0
    with open(path, "r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                cf = float(row.get("carbon_footprint") or 0)
            except ValueError:
                print("[WARN] 行碳排非数值，跳过：%r" % row)
                n_err += 1
                continue
            rec = (row.get("name", "").strip(), row.get("category", "").strip(), cf,
                   (row.get("compliance_risk") or "绿码/安全").strip(),
                   (row.get("purchase_location") or "").strip(),
                   (row.get("description") or "").strip())
            if not rec[0]:
                n_err += 1
                continue
            conn.execute(
                "INSERT INTO local_products (name, category, carbon_footprint, compliance_risk, "
                "purchase_location, description) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET category=excluded.category, "
                "carbon_footprint=excluded.carbon_footprint, "
                "compliance_risk=excluded.compliance_risk, "
                "purchase_location=excluded.purchase_location, description=excluded.description",
                rec)
            # 统计新增/更新：简单起见按 upsert 计数
            n_new += 1
    conn.commit()
    print("[OK] CSV 导入完成：upsert %d 条，跳过 %d 条" % (n_new, n_err))
    return 0


def cmd_export_csv(conn: sqlite3.Connection, path: str) -> int:
    rows = conn.execute("SELECT * FROM local_products ORDER BY product_id").fetchall()
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        for r in rows:
            w.writerow([r[f] for f in FIELDS])
    print("[OK] 已导出 %d 条 -> %s" % (len(rows), path))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="local_products 本地商品资料库维护")
    ap.add_argument("--db", default=DB_PATH, help="默认 data/macau.sqlite")
    ap.add_argument("--seed", action="store_true", help="幂等写入内置 5 条测试数据")
    ap.add_argument("--list", action="store_true", help="列出商品")
    ap.add_argument("--category", help="--list 时按类别过滤")
    ap.add_argument("--add", metavar="'名称,类别,碳排,合规,地点,卖点'", help="添加一条")
    ap.add_argument("--update", type=int, metavar="ID", help="更新指定 product_id")
    ap.add_argument("--set", metavar="'name=xx,carbon_footprint=1.2'", help="配合 --update")
    ap.add_argument("--delete", type=int, metavar="ID", help="删除指定 product_id")
    ap.add_argument("--yes", action="store_true", help="确认删除")
    ap.add_argument("--import-csv", metavar="PATH", help="CSV 批量 upsert")
    ap.add_argument("--export-csv", metavar="PATH", help="导出全表为 CSV")
    args = ap.parse_args()

    if not any((args.seed, args.list, args.add, args.update, args.delete,
                args.import_csv, args.export_csv)):
        ap.print_help()
        return 0

    conn = connect(args.db)
    try:
        if args.seed:
            return cmd_seed(conn)
        if args.add:
            return cmd_add(conn, args.add)
        if args.update is not None:
            if not args.set:
                print("[ABORT] --update 需要配合 --set")
                return 1
            return cmd_update(conn, args.update, args.set)
        if args.delete is not None:
            return cmd_delete(conn, args.delete, args.yes)
        if args.import_csv:
            return cmd_import_csv(conn, args.import_csv)
        if args.export_csv:
            return cmd_export_csv(conn, args.export_csv)
        return cmd_list(conn, args.category)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
