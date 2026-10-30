"""文献分档 v2：在关键词正向匹配之外加入负向过滤，输出可执行优先级队列。

输入：data/raw_pdfs/todo_{A_experimental,B_modeling_review,C_excluded_system}.csv
      （三者合并即 91 篇候选全集，由 elsevier_results*.json 初筛而来）
输出：data/raw_pdfs/triage_v2.csv

判定顺序（先命中先归属，避免正向词抢占）：
  1. 体系边界外  —— 相变/无水/非水/DES/离子液体/固体吸附/膜/催化/纳米添加/有机共溶剂
  2. 综述与二次数据 —— review / overview / advances in ...
  3. 纯物性或降解研究 —— density / viscosity / pKa / oxidation / degradation（无吸收性能）
  4. 建模与模拟 —— Aspen / CFD / DFT / MD / ML / thermodynamic model / optimization / controllability
  5. 实验研究 —— experimental / measured / VLE / kinetics / pilot / demonstration

priority 越小越优先处理：
  1 实验 + 白名单胺种      可直接抽 L1/L2
  2 实验 + 非白名单胺种    需先扩 compound_whitelist
  3 实验 + 物性/降解主题   可能只有无性能数据
  4 建模/模拟              按 simulation 入库
  5 综述                   只做引文挖掘线索
  6 边界外                 登记不入库

用法: python tools/triage_literature.py [--out data/raw_pdfs/triage_v2.csv]
"""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ["todo_A_experimental.csv", "todo_B_modeling_review.csv", "todo_C_excluded_system.csv"]

# 已处理完的 DOI（截至 batch 20261027_05）；脚本不动它们，仅在输出里标 done
DONE = {
    "10.1039/d6gc02196d": "L2 金标准 (test#3)",
    "10.1039/d5ta10129h": "综述，登记边界外",
    "10.1016/j.ces.2026.124685": "simulation 入库 (20261027_04)",
    "10.1016/j.fuel.2026.140773": "simulation 入库 (20261027_04)",
    "10.1016/j.seppur.2026.140522": "simulation 入库 (20261027_04)",
    "10.1016/j.seppur.2026.139992": "simulation 入库 (20261027_05)",
    "10.1016/j.applthermaleng.2026.132050": "simulation 入库 (20261027_05)",
    "10.1021/acs.energyfuels.5c05996": "simulation 入库 (20261027_05)",
    "10.1021/acs.energyfuels.5c04868": "边界外：TEPA/DMAE 非白名单且无密度",
    "10.1016/j.ces.2025.122487": "literature L3 入库 (20261027_05)",
    # batch 20261027_06
    "10.1016/j.jece.2024.112664": "L2 金标准 x3：MEA/AMP/MDEA (20261027_06, test#24-26)",
    "10.1016/j.jece.2025.115464": "边界外：纯 PZ 密度物性，无吸收性能",
    "10.1016/j.jgsce.2023.205135": "边界外：5 M 无密度 + VLE 仅在图中 + DEEA 非白名单",
    "10.1016/j.cej.2024.154085": "边界外：非水相 2-PE/APZ/DMF",
    "10.1016/j.ijggc.2025.104394": "边界外：在线监测方法学",
    "10.1016/j.ijggc.2023.104009": "边界外：PZ 氧化降解 + 活性炭添加",
    "10.1021/acs.iecr.4c00019": "边界外：Mongstad 降解检测方法学",
    "10.1021/acs.energyfuels.5c03737": "边界外：三组分 TETA/DMAE/EAE",
    # batch 20261027_07（引文挖掘：jece.2024.112664 的参考文献）
    "10.1016/j.apenergy.2016.03.043": "L2 金标准 x4：MEA/MDEA/AMP/PZ (20261027_07, test#27-30)",
    "10.1021/es901376a": "暂缓：76 胺均不在白名单；SI 扫描件已清理，正文保留（含白名单胺种基准速率）",
    "10.1016/j.ijggc.2016.12.018": "L2 金标准 x1：MEA 30 mass% 基准 Table A1 (batch 20261027_08)；本文明细表仅定性比较",
    "10.1016/j.jiec.2018.04.017": "边界外已清理：纯 Kent-Eisenberg 热力学建模，cyclic capacity 结果仅在 Fig.3-10，无表格数值，0 条入库",
    "10.1021/je00005a025": "L2 金标准 x27：4 配方 x 40 degC x P_CO2<200kPa (batch 20261027_08, test#31-57)，打通 binary 通路",
    "10.1021/ie400825u": "L2 金标准 x1：MDEA 30 mass% Table 2 row 24 (batch 20261027_08)；仅体积基单位，未换算",
    "10.1016/j.ces.2016.08.017": "待抽取：Du/Rochelle 2016，PZ 共混容量+速率（mol/kg 需核密度）",
    "10.1016/j.cherd.2008.07.014": "待抽取：Singh 2009，结构-活性关系 II",
    "10.1016/j.psep.2008.03.005": "待抽取：Singh&Versteeg 2008，再生结构-活性关系",
    "10.1016/j.ces.2017.02.036": "待抽取：Narku-Tetteh 2017，30 wt% 配方筛选 MEA/AMP",
    "10.1016/j.seppur.2013.10.030": "待抽取：Kim 2014，30 wt% 多氨基胺",
    "10.1016/j.seppur.2017.04.031": "待抽取：Muchan 2017，30 wt% 多胺",
    "10.1016/j.seppur.2019.115998": "待抽取：Liu 2020，AMP 动力学",
    "10.1021/acs.iecr.9b00552": "待抽取：Barzagli 2019，AMP 水相/非水相",
}

# 无需对 mono/di/triethanolamine 做保护："\bethanol\b"/"\bdea\b" 在长词内部本来就不满足词边界
NEG_SYSTEM = [
    r"phase[- ]change", r"two[- ]phase", r"biphasic", r"water[- ]lean",
    r"non[- ]?aqueous", r"anhydrous", r"deep eutectic", r"\bdes\b",
    r"ionic liquid", r"solid sorbent", r"\bsorbent", r"membrane", r"\bmof\b",
    r"zeolite", r"calcium looping", r"ammonia", r"potassium carbonate",
    r"enzymatic", r"cataly(?:st|tic|zed)", r"nanoparticle", r"nanostructure",
    r"kit-6", r"surfactant", r"foam", r"microencapsul",
    r"\bdmso\b", r"\bnmp\b", r"n-methyl-2-pyrrolidone", r"sulfolane",
    r"\bmethanol\b", r"\bethanol\b", r"adsorption performance",
    r"urea-resistant", r"indoor air",
    # 注意：不用裸 "\bso2\b" —— "SO2-resistance" 仍是水相胺体系，只是抗硫
    r"so2 removal", r"so2 capture", r"desulfurization", r"flue gas desulfur",
    r"nitrogen dioxide", r"\bnox\b", r"dissolution of sulfur",
    r"flue gas impurities", r"aerosol",
]

NEG_REVIEW = [
    r"\breview\b", r"overview", r"advances in", r"\bprogress\b",
    r"perspective", r"state of the art", r"critical review",
]

NEG_PROPERTY = [
    r"\bdensity\b", r"\bviscosity\b", r"surface tension", r"diffusivity",
    r"\bpka\b", r"physical propert", r"thermophysical", r"alkalinity",
    r"\boxidation\b", r"\bdegradation\b", r"\bcorrosion\b",
    r"solvent loss", r"\bvolatility\b",
]

NEG_MODEL = [
    r"simulation", r"simulating", r"model(?:ing|ling|s)?\b", r"aspen",
    r"\bcfd\b", r"\bdft\b", r"molecular dynamics", r"\bmd\b",
    r"machine learning", r"data[- ]driven", r"neural network",
    r"random forest", r"\bsvd\b", r"controllability", r"optimization",
    r"optimisation", r"gibbs energy", r"equation of state", r"peng-robinson",
    r"pc-?saft", r"enrtl", r"rate[- ]based", r"techno[- ]economic",
    r"correlations?\b", r"predict(?:ion|ive)?\b", r"screening of",
    r"thermodynamic model", r"robust design", r"validated model",
]

POS_EXPERIMENT = [
    r"experimental", r"experiment", r"measured", r"measurement",
    r"\bvle\b", r"vapou?r[- ]liquid equilibrium", r"kinetics",
    r"absorption[-/–]desorption", r"speciation", r"\bnmr\b", r"\bftir\b",
    r"pilot", r"demonstration", r"bench[- ]scale", r"cyclic capacity",
    r"mass transfer", r"evaluation of", r"investigation of",
    r"study of", r"performance",
]

# 标题级强信号：出现即定性，压过泛化的 "investigation of / performance / loading"
STRONG_MODEL = [
    r"^model(?:ing|ling)\b", r"\bsimulation\b", r"molecular simulation",
    r"molecular insight", r"molecular dynamics", r"data[- ]driven",
    r"artificial neural", r"machine learning", r"\bann\b",
    r"process simulation", r"thermodynamic model", r"equation of state",
    r"rate[- ]based", r"controllability", r"\bsvd\b", r"peng-robinson",
    r"pc-?saft", r"\benrtl\b", r"gibbs energy", r"optimization",
    r"optimisation", r"correlations?\b", r"predict(?:ion|ive)?\b",
    r"robust design", r"framework", r"liquid[- ]vapor interface",
]

STRONG_EXP = [
    r"experimental validation", r"experimental study", r"experimental investigation",
    r"experimental determination", r"experimental measurements?",
    r"measured", r"measurements? of", r"\bpilot[- ]plant\b", r"bench[- ]scale",
    r"\bvle\b", r"vapou?r[- ]liquid equilibrium", r"kinetics",
    r"absorption[-/–]desorption", r"speciation", r"\bnmr\b", r"\bftir\b",
    r"cyclic capacity", r"mass transfer coefficient",
]

WHITELIST_AMINE = [
    r"\bmea\b", r"monoethanolamine", r"\bpz\b", r"piperazine",
    r"\bamp\b", r"2-amino-2-methyl-1-propanol", r"\bmdea\b",
    r"methyldiethanolamine",
]

OTHER_AMINE = [
    r"\btepa\b", r"tetraethylenepentamine", r"\bdmae\b", r"dimethylaminoethanol",
    r"\bdeea\b", r"diethylethanolamine", r"\bdmea\b", r"3-dmapa",
    r"1-methylpiperazine", r"\bmipa\b", r"\bdea\b", r"diethanolamine",
    r"\btea\b", r"triethanolamine", r"triamine", r"diamine",
    r"sterically hindered", r"trimethylenedipiperidine", r"dmapa",
]


def norm(text):
    return text.lower()


def hits(pats, text):
    return [p for p in pats if re.search(p, text)]


def verdict_of(title, kw):
    text = norm(title + " " + (kw or "").replace(";", " "))
    sys_hits = hits(NEG_SYSTEM, text)
    rev_hits = hits(NEG_REVIEW, text)
    prop_hits = hits(NEG_PROPERTY, text)
    mod_hits = hits(NEG_MODEL, text)
    smod_hits = hits(STRONG_MODEL, text)
    sexp_hits = hits(STRONG_EXP, text)
    exp_hits = hits(POS_EXPERIMENT, text)
    wl = hits(WHITELIST_AMINE, text)
    other = hits(OTHER_AMINE, text)

    if sys_hits:
        return "out_of_scope", 6, "system:" + ",".join(sys_hits[:3])
    if rev_hits:
        return "review", 5, "review:" + ",".join(rev_hits[:3])

    # 强建模信号优先于泛化实验词；两者并存时交全文判定
    if smod_hits and sexp_hits:
        return "model_and_experiment", 2, "both:" + ",".join(smod_hits[:2] + sexp_hits[:2])
    if smod_hits and not sexp_hits:
        return "simulation", 4, "model:" + ",".join(smod_hits[:3])
    if prop_hits and not sexp_hits:
        return "property_or_degradation", 3, "property:" + ",".join(prop_hits[:3])

    if sexp_hits:
        if wl and not other:
            return "experiment", 1, "whitelist:" + ",".join(wl[:2])
        if other:
            return "experiment", 2, "amine_not_whitelisted:" + ",".join(other[:3])
        return "experiment", 2, "no_amine_named:" + ",".join(sexp_hits[:2])
    if exp_hits:
        if wl and not other:
            return "experiment", 2, "weak_whitelist:" + ",".join(wl[:2])
        if other:
            return "experiment", 2, "weak_amine_not_whitelisted:" + ",".join(other[:2])
        return "experiment", 2, "weak:" + ",".join(exp_hits[:2])
    if mod_hits:
        return "simulation", 4, "model:" + ",".join(mod_hits[:3])
    if prop_hits:
        return "property_or_degradation", 3, "property:" + ",".join(prop_hits[:3])
    return "unclear", 5, "no_signal"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "raw_pdfs" / "triage_v2.csv"))
    args = ap.parse_args()

    seen, rows = set(), []
    for fn in SRC:
        p = ROOT / "data" / "raw_pdfs" / fn
        if not p.exists():
            print("missing:", p, file=sys.stderr)
            continue
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                doi = (r.get("doi") or "").strip().lower()
                if not doi or doi in seen:
                    continue
                seen.add(doi)
                verdict, prio, reason = verdict_of(r.get("title", ""), r.get("matched_keywords", ""))
                rows.append({
                    "doi": doi,
                    "publication_year": r.get("publication_year", ""),
                    "title": re.sub(r"\s+", " ", r.get("title", "")).strip(),
                    "verdict": verdict,
                    "priority": prio,
                    "reason": reason,
                    "origin_bucket": fn.split("_")[1],
                    "status": DONE.get(doi, "pending"),
                })

    rows.sort(key=lambda x: (x["priority"], -int(x["publication_year"] or 0), x["doi"]))
    cols = ["priority", "verdict", "doi", "publication_year", "title", "reason", "origin_bucket", "status"]
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    print("candidates:", len(rows))
    from collections import Counter
    for v, n in Counter(r["verdict"] for r in rows).most_common():
        print("  %-24s %d" % (v, n))
    print("\n可执行的实验类队列（priority 1-3, pending）:")
    n = 0
    for r in rows:
        if r["priority"] <= 3 and r["status"] == "pending":
            n += 1
            print("  %2d | %s | %s | %s" % (n, r["doi"], r["reason"][:52], r["title"][:64]))
    print("\nwritten:", args.out)


if __name__ == "__main__":
    main()
