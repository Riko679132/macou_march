#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
import_extended_factors.py —— 扩充高频通关物品的碳排因子（幂等导入）

2026-10-30 扩充类别：烟草（按支）、手机（按台）、笔记本电脑（按台）。
因子全部带权威 source_ref，禁止编造：

- 烟草 0.014 kg CO2e/支
  来源：WHO FCTC (2018)《Tobacco and its environmental impact: an overview》，
  "A typical cigarette ... a climate change contribution of 14 g CO2 eq"（典型卷烟全生命周期）。
  口径：按"支"计；雪茄以典型卷烟近似，属保守口径（不单独设雪茄因子）。
- 手机 55 kg CO2e/台
  来源：Apple Product Environmental Report, iPhone 17 (256GB), Sept 2025。
  厂商官方全生命周期 LCA（制造+运输+使用+回收，含配件与包装）。
- 笔记本电脑 120 kg CO2e/台
  来源：Apple Product Environmental Report, MacBook Air 13-inch M4 (256GB), March 2025。
  口径同上。

运行：python tools/import_extended_factors.py
幂等：UNIQUE(material, unit, source_ref) 冲突时跳过，不重复插入。

护栏：本脚本只写 macau.sqlite；ccus.sqlite 一律拒绝，防止误伤主项目。
"""
from __future__ import annotations

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(HERE, "data", "macau.sqlite")

# (material, category, emission_factor, unit, data_quality, source_ref, valid_year, notes)
FACTORS = [
    ("烟草", "tobacco", 0.014, "kg CO2e/支", "literature",
     "WHO FCTC (2018) Tobacco and its environmental impact: an overview",
     2018,
     "典型卷烟全生命周期约 14 g CO2 eq/支；雪茄按典型卷烟近似，口径偏保守（生产阶段数值另见 "
     "Zheng et al. 2024, Front. Environ. Sci., 2.18 kg CO2e/kg tobacco production）"),
    ("手机", "electronics", 55.0, "kg CO2e/台", "database",
     "Apple Product Environmental Report, iPhone 17 (256GB), Sept 2025",
     2025,
     "厂商官方全生命周期 LCA（制造+运输+使用+回收，含配件与包装）；其他品牌/型号按此近似"),
    ("笔记本电脑", "electronics", 120.0, "kg CO2e/台", "database",
     "Apple Product Environmental Report, MacBook Air 13-inch M4 (256GB), March 2025",
     2025,
     "厂商官方全生命周期 LCA（制造+运输+使用+回收，含配件与包装）；其他品牌/型号按此近似"),
]


def main() -> int:
    if "ccus" in os.path.basename(DB_PATH).lower():
        print("[拒绝] 本脚本只写 macau.sqlite，禁止操作 ccus 库")
        return 1
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS carbon_factors ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "material TEXT NOT NULL, category TEXT, emission_factor REAL NOT NULL, "
            "unit TEXT DEFAULT 'kg CO2e/kg', system_boundary TEXT, "
            "source_ref TEXT, data_quality TEXT DEFAULT 'unknown', "
            "valid_year INTEGER, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "UNIQUE(material, unit, source_ref))")
        inserted = skipped = 0
        for (mat, cat, val, unit, dq, src, year, notes) in FACTORS:
            cur = conn.execute(
                "INSERT OR IGNORE INTO carbon_factors "
                "(material, category, emission_factor, unit, system_boundary, data_quality, "
                " source_ref, valid_year, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mat, cat, val, unit, "cradle-to-grave", dq, src, year, notes))
            if cur.rowcount:
                inserted += 1
                print("[+] 新增因子 %s = %s %s（%s，%s）" % (mat, val, unit, dq, src))
            else:
                skipped += 1
                print("[=] 已存在，跳过 %s（%s，%s）" % (mat, unit, src))
        conn.commit()
        print("完成：新增 %d 条，跳过 %d 条。" % (inserted, skipped))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
