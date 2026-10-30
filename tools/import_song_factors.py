#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Song et al. 2017 食品碳足迹因子导入工具（幂等）。

数据来源：Song et al. 2017（用户提供的权威数值，单位 kg CO2e/kg）：
    牛肉 21.30 / 猪肉 4.22 / 禽肉 3.92 / 零食·饼干 1.35 / 水果 0.88

说明：系统边界未逐一核对，暂按表默认 cradle-to-gate 入库并在 notes 留痕；
正式商用前应补全论文 DOI 与边界信息后回填。

用法：
    python tools/import_song_factors.py
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(PROJECT_ROOT, "data", "macau.sqlite")

SOURCE_REF = "Song et al. 2017"
# (material, emission_factor, category)
FACTORS = [
    ("牛肉", 21.30, "food"),
    ("猪肉", 4.22, "food"),
    ("禽肉", 3.92, "food"),
    ("饼干", 1.35, "food"),   # 含零食类
    ("水果", 0.88, "food"),
]


def guard(path: str) -> None:
    if "ccus" in os.path.basename(path).lower():
        raise SystemExit("[ABORT] 目标库名含 ccus，禁止操作父项目数据库：%s" % path)
    if os.path.basename(path).lower() != "macau.sqlite":
        raise SystemExit("[ABORT] 本脚本仅用于 macau.sqlite，收到：%s" % path)


def main() -> int:
    guard(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        n_new, n_skip = 0, 0
        for material, factor, category in FACTORS:
            cur = conn.execute(
                "SELECT 1 FROM carbon_factors WHERE material = ? AND source_ref = ?",
                (material, SOURCE_REF))
            if cur.fetchone():
                n_skip += 1
                continue
            conn.execute(
                "INSERT INTO carbon_factors (material, category, emission_factor, unit, "
                "system_boundary, source_ref, data_quality, valid_year, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (material, category, factor, "kg CO2e/kg", "cradle-to-gate",
                 SOURCE_REF, "literature", 2017,
                 "用户提供数值；系统边界未逐条核对，商用前补全 DOI/边界"))
            n_new += 1
        conn.commit()
        print("[OK] Song et al. 2017 因子导入：新增 %d 条，已存在跳过 %d 条" % (n_new, n_skip))
        print("     （同 material 与内置测试因子并存，data_quality=literature 优先生效）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
