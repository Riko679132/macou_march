import argparse, json, os, sqlite3, yaml, hashlib, time, shutil
from pathlib import Path

def acquire_lock(lock_path):
    import os
    f = open(lock_path, "w")
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except Exception:
        raise RuntimeError("数据库正被其他任务占用，请稍后重试")

def get_or_create_compound(cur, c):
    if not c or not c.get("inchikey"):
        return None
    cur.execute("SELECT compound_id FROM compound WHERE inchikey=?", (c["inchikey"],))
    row = cur.fetchone()
    if row: return row[0]
    cur.execute("INSERT INTO compound(inchikey,cas_no,smiles,standard_name) VALUES(?,?,?,?)",
                (c["inchikey"], c.get("cas_no"), c.get("smiles"), c.get("standard_name")))
    return cur.lastrowid

def get_or_create_source(cur, s):
    """按 DOI 或 title 去重；命中返回现有 source_id，否则插入并返回 lastrowid。"""
    doi = s.get("doi")
    title = s.get("title")
    if doi:
        cur.execute("SELECT source_id FROM source WHERE doi=?", (doi,))
        row = cur.fetchone()
        if row: return row[0]
    elif title:
        cur.execute("SELECT source_id FROM source WHERE title=?", (title,))
        row = cur.fetchone()
        if row: return row[0]
    cur.execute(
        "INSERT INTO source(doi,url,title,authors,year,journal,pdf_path,pdf_hash,source_type) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (doi, s.get("url"), title, s.get("authors"), s.get("year"),
         s.get("journal"), s.get("pdf_path"), s.get("pdf_hash"),
         s.get("source_type", "paper"))
    )
    return cur.lastrowid

def _compact_loc_detail(s, r):
    """生成精简 per-row 定位串，例：'Table 1, MEA 30.0 wt%' 或 'Table 1, MEA 25.0 wt% + PZ 5.0 wt%'。
    该串写入 source_location.loc_detail，作为 (source_id, loc_type, loc_label, loc_detail) 去重键之一。
    完整 excerpt 保留在 JSONL 中，excerpt_path 指向 excerpt 文件。"""
    label = s.get("loc_label") or s.get("loc_type") or "location"
    a = r.get("amine_a") or {}
    parts = []
    if a.get("standard_name"):
        w = r.get("wt_a")
        parts.append(f"{a['standard_name']} {w} wt%" if w is not None else a["standard_name"])
    b = r.get("amine_b")
    if b and b.get("standard_name"):
        w = r.get("wt_b")
        parts.append(f"{b['standard_name']} {w} wt%" if w is not None else b["standard_name"])
    amine_sig = " + ".join(parts) if parts else "?"
    return f"{label}, {amine_sig}"

def get_or_create_location(cur, source_id, s, loc_detail):
    """按 (source_id, loc_type, loc_label, loc_detail) 四元组去重（细粒度：每行独立 location）。"""
    loc_type = s.get("loc_type")
    loc_label = s.get("loc_label")
    cur.execute(
        "SELECT location_id FROM source_location "
        "WHERE source_id IS ? AND loc_type IS ? AND loc_label IS ? AND loc_detail IS ?",
        (source_id, loc_type, loc_label, loc_detail)
    )
    row = cur.fetchone()
    if row: return row[0]
    cur.execute(
        "INSERT INTO source_location(source_id,loc_type,loc_label,loc_detail,excerpt_path,extraction_method) "
        "VALUES(?,?,?,?,?,?)",
        (source_id, loc_type, loc_label, loc_detail,
         s.get("excerpt_path"), s.get("extraction_method"))
    )
    return cur.lastrowid

def insert_test(cur, recipe_id, source_id, location_id, t, qc):
    """每条记录写一行 test；method_id 若 JSON 未提供则留 NULL。
    工况字段（temperature_K / co2_vol_pct / total_pressure_kPa 等）允许为 None → SQL NULL；
    qc_level 只接受 L1/L2/L3/L4，其他值强制转 None（避免 CHECK 失败导致整批事务回滚）。"""
    qc_level = qc.get("qc_level")
    if qc_level not in ("L1", "L2", "L3", "L4"):
        qc_level = None
    cur.execute(
        "INSERT INTO test(recipe_id,source_id,location_id,method_id,test_origin,batch_id,"
        "temperature_K,co2_vol_pct,co2_partial_kPa,total_pressure_kPa,gas_flow,liquid_volume,gas_liquid_ratio,"
        "initial_loading,rich_loading,lean_loading,qc_level,qc_reason,"
        "raw_data_path,raw_data_hash,candidate_id,model_version) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (recipe_id, source_id, location_id, t.get("method_id"),
         t.get("test_origin"), t.get("batch_id"),
         t.get("temperature_K"), t.get("co2_vol_pct"), t.get("co2_partial_kPa"),
         t.get("total_pressure_kPa"), t.get("gas_flow"), t.get("liquid_volume"),
         t.get("gas_liquid_ratio"),
         t.get("initial_loading"), t.get("rich_loading"), t.get("lean_loading"),
         qc_level, qc.get("qc_reason"),
         t.get("raw_data_path"), t.get("raw_data_hash"),
         t.get("candidate_id"), t.get("model_version"))
    )
    return cur.lastrowid

def insert_performance(cur, test_id, perf):
    cur.execute(
        "INSERT INTO performance(test_id,metric_name,value,unit,value_original,unit_original,"
        "error_value,repeat_count,metric_definition,is_derived) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (test_id, perf.get("metric_name"), perf.get("value"), perf.get("unit"),
         perf.get("value_original"), perf.get("unit_original"),
         perf.get("error_value"), perf.get("repeat_count"),
         perf.get("metric_definition"), perf.get("is_derived", 0))
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--batch-id", required=True)
    args = ap.parse_args()
    
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    db_path = cfg["database"]["path"]
    lock = acquire_lock(cfg["database"]["lock_file"])
    
    backup = Path(cfg["database"]["backup_dir"]) / f"ccus_{time.strftime('%Y%m%d_%H%M%S')}.sqlite"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if Path(db_path).exists():
        shutil.copy(db_path, backup)
        
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    stats = {"inserted": 0, "skipped": 0, "failed": 0}
    tracked = ("compound", "recipe", "source", "source_location", "test", "performance")
    
    def _counts():
        return {t: cur.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in tracked}
    
    pre = _counts()
    try:
        cur.execute("BEGIN IMMEDIATE")
        for line in open(args.input, encoding="utf-8"):
            if not line.strip(): continue
            rec = json.loads(line)
            a_id = get_or_create_compound(cur, rec["recipe"]["amine_a"])
            b_id = get_or_create_compound(cur, rec["recipe"].get("amine_b"))
            
            recipe_key = f'{a_id}|{b_id}|{rec["recipe"]["wt_a"]}|{rec["recipe"].get("wt_b")}|{rec["recipe"]["water_wt"]}'
            recipe_id = hashlib.sha1(recipe_key.encode()).hexdigest()
            
            cur.execute("INSERT OR IGNORE INTO recipe(recipe_id,recipe_type,amine_a_id,amine_b_id,wt_a,wt_b,water_wt,total_amine_wt) VALUES(?,?,?,?,?,?,?,?)",
                        (recipe_id, rec["recipe"]["recipe_type"], a_id, b_id,
                         rec["recipe"]["wt_a"], rec["recipe"].get("wt_b"),
                         rec["recipe"]["water_wt"], rec["recipe"]["total_amine_wt"]))
            
            # --- 新增：source / source_location / test / performance ---
            s = rec.get("source", {}) or {}
            source_id = get_or_create_source(cur, s)
            loc_detail = _compact_loc_detail(s, rec["recipe"])
            location_id = get_or_create_location(cur, source_id, s, loc_detail)
            t = rec.get("test", {}) or {}
            qc = rec.get("qc", {}) or {}
            test_id = insert_test(cur, recipe_id, source_id, location_id, t, qc)
            for perf in (rec.get("performance") or []):
                insert_performance(cur, test_id, perf)
            
            stats["inserted"] += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        stats["failed"] += 1
        print("导入失败，已回滚：", e)
        raise
    finally:
        conn.close(); lock.close()
    post = {t: 0 for t in tracked}
    # 事务提交后再开一个只读连接统计行数
    conn2 = sqlite3.connect(db_path)
    for t in tracked:
        post[t] = conn2.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
    conn2.close()
    for t in tracked:
        stats[f"{t}_new"] = post[t] - pre[t]
    print("导入完成：", stats, "批次：", args.batch_id)

if __name__ == "__main__":
    main()
