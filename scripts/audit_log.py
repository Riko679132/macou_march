"""
audit_log.py —— 自动采集模式：向 audit_log 表插入一条审计记录（骨架版 v0.1）

用法:
    python scripts/audit_log.py --action import_incomplete \
        --table test --detail "batch=autobatch_20261026_01; n=3" \
        [--record-id ...] [--operator auto_agent] [--config config.yaml]

行为:
    向 audit_log 插入 (table_name, record_id, action, detail, operator, ts=datetime('now'))。
    仅由自动采集流程在导入 valid_incomplete.jsonl 之后调用；不做任何 UPDATE/DELETE。

注:
    - 与 import_jsonl.py 一样打开数据库连接，但只 INSERT 一行 audit_log；
    - 与主流程串行执行（Agent 显式按顺序调用），暂不加文件锁。
"""
import argparse
import sqlite3

import yaml


def main():
    ap = argparse.ArgumentParser(description="向 audit_log 表插入一条审计记录")
    ap.add_argument("--config", default="config.yaml", help="项目配置文件")
    ap.add_argument("--table", default="test", help="被审计的目标表名")
    ap.add_argument("--record-id", dest="record_id", default=None,
                    help="目标记录主键（可选，聚合审计时可留空）")
    ap.add_argument("--action", required=True,
                    help="动作标签，例如 import_incomplete / skip_rejected")
    ap.add_argument("--detail", default=None,
                    help="详情字符串，例如 'batch=autobatch_20261026_01; n=3'")
    ap.add_argument("--operator", default="auto_agent",
                    help="操作者标签，默认 auto_agent")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    db_path = cfg["database"]["path"]

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO audit_log(table_name, record_id, action, detail, operator) "
            "VALUES(?,?,?,?,?)",
            (args.table, args.record_id, args.action, args.detail, args.operator),
        )
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()

    print(f"audit_log #{new_id} 已写入："
          f"table={args.table} action={args.action} "
          f"operator={args.operator} detail={args.detail}")


if __name__ == "__main__":
    main()
