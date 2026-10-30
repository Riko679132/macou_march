import argparse, json, os, yaml, sqlite3

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--valid", required=True)
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    db_path = cfg["database"]["path"]
    
    count = 0
    records = []
    with open(args.valid, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
                count += 1
                
    out = os.path.join(os.path.dirname(args.valid), "import_preview.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# 导入预览\n\n")
        f.write(f"- 待导入记录总数：{count}\n")
        f.write("- 脚本版本：validate.py / import_jsonl.py\n\n")
        f.write("## 样例记录 (前3条)\n```json\n")
        for r in records[:3]:
            f.write(json.dumps(r, ensure_ascii=False, indent=2) + "\n")
        f.write("```\n")
    print(f"preview written: {out} (total={count})")

if __name__ == "__main__":
    main()
