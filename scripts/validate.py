import argparse, json, os

REQUIRED_TEST_FIELDS = ["temperature_K", "co2_vol_pct", "initial_loading"]
REQUIRED_SOURCE_FIELDS = ["doi", "loc_type", "loc_label", "excerpt"]

# 硬性拒绝（永不入库）：违反物理/边界/身份铁律
# 硬性检查全部通过才可能进入 valid 或 valid_incomplete
# 例外：test_origin == "simulation" 的机理模型数据允许突破 20-50 wt% 浓度上限，
#       但质量守恒与配方身份仍是铁律，且一律强制 L3（见 main），不进 L1/L2 金标准。
def is_simulation(rec):
    return (rec.get("test", {}) or {}).get("test_origin") == "simulation"

def classify_record(rec):
    """返回 (hard, soft) 两组原因列表。
    hard 非空 → rejected（永不入库）；
    hard 空且 soft 非空 → incomplete（可入库，qc_level=L3，incomplete_flag=true）；
    hard 空且 soft 空 → valid（可入库，qc_level 保持原样）。"""
    hard, soft = [], []
    r = rec.get("recipe", {})
    sim = is_simulation(rec)
    if r.get("recipe_type") not in ("mono", "binary"):
        hard.append("recipe_type")
    if r.get("additive_flag") or r.get("salt_flag") or r.get("phase_flag"):
        hard.append("excluded_system")
    total = r.get("total_amine_wt")
    if total is None or (not sim and not (20 <= total <= 50)):
        hard.append("concentration_out_of_range")
    wt_b = r.get("wt_b") or 0
    if abs((r.get("wt_a") or 0) + wt_b + (r.get("water_wt") or 0) - 100) > 0.1:
        hard.append("mass_balance")
    if r.get("recipe_type") == "binary":
        if not r.get("amine_b") or not r["amine_b"].get("inchikey"):
            hard.append("binary_identity")
    src = rec.get("source", {})
    for f in REQUIRED_SOURCE_FIELDS:
        if not src.get(f):
            soft.append(f"source_{f}")
    test = rec.get("test", {})
    for f in REQUIRED_TEST_FIELDS:
        if test.get(f) is None:
            soft.append(f"test_{f}")
    return hard, soft

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    valid, incomplete, rejected = [], [], []
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            rec = json.loads(line)
            hard, soft = classify_record(rec)
            rec["qc"] = rec.get("qc", {})
            if hard:
                # 硬违规：qc_level 强制 L4；无论是否同时有 soft 都记入 qc_reason
                rec["qc"]["qc_level"] = "L4"
                rec["qc"]["qc_reason"] = ";".join(hard + soft)
                rejected.append(rec)
            elif soft:
                # 缺字段但配方闭合：入库但强制 L3 + incomplete_flag=true
                # qc_reason 采用"追加"而非"覆盖"：保留上游人工备注（如"非核心研究边界指标"）
                rec["qc"]["qc_level"] = "L3"
                prev = [p for p in (rec["qc"].get("qc_reason") or "").split(";") if p]
                reasons = list(soft)
                if is_simulation(rec):
                    reasons = ["simulation_data"] + reasons
                rec["qc"]["qc_reason"] = ";".join(dict.fromkeys(reasons + prev))
                rec["incomplete_flag"] = True
                incomplete.append(rec)
            elif is_simulation(rec):
                # 仿真数据：字段齐全也不进金标准，强制 L3（虚拟参考域）。
                # 不设 incomplete_flag：该标记专指字段不全，与数据来源无关。
                rec["qc"]["qc_level"] = "L3"
                prev = [p for p in (rec["qc"].get("qc_reason") or "").split(";") if p]
                rec["qc"]["qc_reason"] = ";".join(
                    dict.fromkeys(["simulation_data"] + prev))
                incomplete.append(rec)
            else:
                valid.append(rec)
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "valid.jsonl"), "w", encoding="utf-8") as f:
        for r in valid: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if incomplete:
        with open(os.path.join(args.outdir, "valid_incomplete.jsonl"), "w", encoding="utf-8") as f:
            for r in incomplete: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(args.outdir, "rejected.jsonl"), "w", encoding="utf-8") as f:
        for r in rejected: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"valid={len(valid)} valid_incomplete={len(incomplete)} rejected={len(rejected)}")

if __name__ == "__main__":
    main()
