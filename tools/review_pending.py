#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pending_products 定时审核：AI 扮演资深海关审核员，24h 后复核众包提交。

用法：
    python tools/review_pending.py --once                 # 跑一轮（只看满 24h 的）
    python tools/review_pending.py --once --ignore-age    # 演示：忽略 24h 限制，立即审核全部
    python tools/review_pending.py                        # 常驻：每天跑一轮（while True + sleep）

审核规则：
    - 模型给出合法状态且认可用户判断（user_status_reasonable 或与用户一致）→ 通过，写 known_products。
    - 模型给出合法状态但判定用户判断不合理 → 驳回（review_status='rejected'）。
    - 模型/网络异常 → 保持 pending，下一轮重试（不因临时故障误判）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import agent_brain as ab  # noqa: E402

SYSTEM = '''你是资深海关审核员。用户众包提交了一条商品信息，请你判断其合规状态是否合理，
并给出最终的审核结论。只依据常识与海关法规，不得编造具体条款数值。
只输出一个 JSON 对象，不要任何解释文字：
{"approved_status": "green|yellow|red", "user_status_reasonable": true, "ingredients": "配料或成分（未知填“未提供”）", "reason": "简要审核理由"}'''
USER = """众包数据：
物品：{name}
描述：{desc}
用户自选状态：{user_status}

请判断用户状态是否合理，并给出最终审核状态。"""


def guard(db_path: str) -> None:
    if "ccus" in os.path.basename(db_path).lower():
        raise SystemExit("[ABORT] 禁止操作 ccus 库")


def fetch_pending(conn: sqlite3.Connection, ignore_age: bool, limit: int):
    sql = ("SELECT id, product_name, description, user_status, submit_time "
           "FROM pending_products WHERE review_status='pending'")
    if not ignore_age:
        sql += " AND datetime(submit_time) <= datetime('now','localtime','-24 hours')"
    sql += " ORDER BY submit_time LIMIT ?"
    return conn.execute(sql, (limit,)).fetchall()


def review_one(row) -> str:
    rid, name, desc, user_status, submit_time = row
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER.format(
            name=name, desc=desc or "（无）", user_status=user_status)},
    ]
    raw, model = ab.call_deepseek(messages, ab.load_config())
    data = ab.extract_json(raw)
    approved = str(data.get("approved_status", "")).lower().strip()
    reasonable = bool(data.get("user_status_reasonable")) or approved == user_status
    reason = str(data.get("reason") or "").strip()
    ingredients = str(data.get("ingredients") or "").strip() or (desc or "未提供")
    if approved not in ("green", "yellow", "red"):
        return "invalid:%s" % approved
    return "%s|%s|%s|%s" % (approved, int(reasonable), ingredients, reason)


def run_once(ignore_age: bool, limit: int) -> None:
    cfg = ab.load_config()
    db_path = cfg["database"]["path"]
    guard(db_path)
    conn = sqlite3.connect(db_path)
    try:
        rows = fetch_pending(conn, ignore_age, limit)
        if not rows:
            print("[REVIEW] 没有待审核数据")
            return
        print("[REVIEW] 本轮 %d 条（ignore_age=%s）" % (len(rows), ignore_age))
        for row in rows:
            rid, name, desc, user_status, submit_time = row
            try:
                out = review_one(row)
            except Exception as exc:  # 网络/解析失败 → 保持 pending，下轮重试
                print("  - #%s %s: 复核失败（保持 pending）：%s" % (rid, name, type(exc).__name__))
                continue
            parts = out.split("|", 3)
            if parts[0].startswith("invalid"):
                print("  - #%s %s: 模型输出非法状态，保持 pending" % (rid, name))
                continue
            approved, reasonable, ingredients, reason = parts
            if reasonable == "1":
                conn.execute(
                    "INSERT INTO known_products (product_name, ingredients, approved_status, "
                    "source, updated_at) VALUES (?,?,?,'UGC',datetime('now','localtime')) "
                    "ON CONFLICT(product_name) DO UPDATE SET ingredients=excluded.ingredients, "
                    "approved_status=excluded.approved_status, source='UGC', "
                    "updated_at=datetime('now','localtime')",
                    (name, ingredients, approved))
                conn.execute("UPDATE pending_products SET review_status='approved' WHERE id=?", (rid,))
                conn.commit()
                print("  + #%s %s: 通过 → known_products(%s) ｜ %s" % (rid, name, approved, reason[:60]))
            else:
                conn.execute("UPDATE pending_products SET review_status='rejected' WHERE id=?", (rid,))
                conn.commit()
                print("  x #%s %s: 驳回（用户=%s，模型=%s）｜ %s"
                      % (rid, name, user_status, approved, reason[:60]))
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="pending_products AI 定时审核")
    ap.add_argument("--once", action="store_true", help="只跑一轮后退出")
    ap.add_argument("--ignore-age", action="store_true", help="忽略 24h 限制（演示用）")
    ap.add_argument("--limit", type=int, default=50, help="每轮最多处理条数")
    ap.add_argument("--interval", type=int, default=86400, help="常驻模式间隔秒数（默认 86400）")
    args = ap.parse_args()

    if args.once:
        run_once(args.ignore_age, args.limit)
        return 0
    print("[REVIEW] 常驻模式启动，每 %d 秒审核一次；Ctrl+C 退出" % args.interval)
    while True:
        try:
            run_once(args.ignore_age, args.limit)
        except Exception as exc:
            print("[REVIEW] 本轮异常：%s" % exc)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
