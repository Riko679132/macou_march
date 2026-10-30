import sqlite3, yaml, os
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
db_path = cfg["database"]["path"]
os.makedirs(os.path.dirname(db_path), exist_ok=True)
os.makedirs(cfg["database"]["backup_dir"], exist_ok=True)
conn = sqlite3.connect(db_path)
with open("scripts/schema.sql", encoding="utf-8") as f:
    conn.executescript(f.read())
conn.commit()
conn.close()
print(f"数据库已初始化：{db_path}")
