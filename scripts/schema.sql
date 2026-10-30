PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS compound (
  compound_id INTEGER PRIMARY KEY,
  inchikey TEXT UNIQUE,
  cas_no TEXT,
  smiles TEXT,
  standard_name TEXT NOT NULL,
  amine_class TEXT,
  salt_form TEXT DEFAULT 'parent',
  mw REAL,
  purchasable INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source (
  source_id INTEGER PRIMARY KEY,
  doi TEXT,
  url TEXT,
  title TEXT,
  authors TEXT,
  year INTEGER,
  journal TEXT,
  pdf_path TEXT,
  pdf_hash TEXT,
  source_type TEXT DEFAULT 'paper',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_location (
  location_id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES source(source_id),
  loc_type TEXT,
  loc_label TEXT,
  loc_detail TEXT,
  excerpt_path TEXT,
  extraction_method TEXT
);

CREATE TABLE IF NOT EXISTS recipe (
  recipe_id TEXT PRIMARY KEY,
  recipe_type TEXT CHECK(recipe_type IN ('mono','binary')),
  amine_a_id INTEGER NOT NULL REFERENCES compound(compound_id),
  amine_b_id INTEGER REFERENCES compound(compound_id),
  wt_a REAL NOT NULL,
  wt_b REAL,
  water_wt REAL NOT NULL,
  total_amine_wt REAL NOT NULL,
  additive_flag INTEGER DEFAULT 0,
  salt_flag INTEGER DEFAULT 0,
  phase_flag INTEGER DEFAULT 0,
  recipe_status TEXT DEFAULT 'eligible',
  exclusion_reason TEXT,
  created_from TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  CHECK (
    (recipe_type='mono' AND amine_b_id IS NULL AND wt_b IS NULL)
    OR
    (recipe_type='binary' AND amine_b_id IS NOT NULL AND wt_b IS NOT NULL)
  ),
  -- 质量守恒是物理铁律，任何来源都不放宽
  CHECK (abs(wt_a + COALESCE(wt_b,0) + water_wt - 100) <= 0.1)
  -- 总胺 20-50 wt% 上限已上移到 validate.py 按 test_origin 判定：
  --   literature 超限 → 硬拒 L4；simulation 允许超限（如 60 wt% MEA）但强制 L3。
  -- 放在表级 CHECK 里会让 60 wt% 的仿真配方根本插不进来，故此处不再约束。
);

CREATE TABLE IF NOT EXISTS method (
  method_id INTEGER PRIMARY KEY,
  reactor_type TEXT,
  analysis_method TEXT,
  loading_definition TEXT,
  rate_definition TEXT,
  sop_version TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS test (
  test_id INTEGER PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipe(recipe_id),
  source_id INTEGER REFERENCES source(source_id),
  location_id INTEGER REFERENCES source_location(location_id),
  method_id INTEGER REFERENCES method(method_id),
  test_origin TEXT,
  batch_id TEXT,
  temperature_K REAL,
  co2_vol_pct REAL,
  co2_partial_kPa REAL,
  total_pressure_kPa REAL,
  gas_flow REAL,
  liquid_volume REAL,
  gas_liquid_ratio REAL,
  initial_loading REAL,
  rich_loading REAL,
  lean_loading REAL,
  qc_level TEXT CHECK(qc_level IN ('L1','L2','L3','L4')),
  qc_reason TEXT,
  raw_data_path TEXT,
  raw_data_hash TEXT,
  candidate_id TEXT,
  model_version TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance (
  perf_id INTEGER PRIMARY KEY,
  test_id INTEGER NOT NULL REFERENCES test(test_id),
  metric_name TEXT NOT NULL,
  value REAL,
  unit TEXT,
  value_original REAL,
  unit_original TEXT,
  error_value REAL,
  repeat_count INTEGER,
  metric_definition TEXT,
  is_derived INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS qc_record (
  qc_id INTEGER PRIMARY KEY,
  test_id INTEGER REFERENCES test(test_id),
  qc_level TEXT,
  qc_reason TEXT,
  reviewer TEXT,
  review_time TEXT,
  human_verified INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS candidate (
  candidate_id TEXT PRIMARY KEY,
  round_id TEXT,
  recipe_id TEXT REFERENCES recipe(recipe_id),
  predicted_metric_json TEXT,
  prediction_interval_json TEXT,
  applicability_domain TEXT,
  recommendation_reason TEXT,
  candidate_type TEXT,
  safety_check_status TEXT,
  task_status TEXT,
  model_version TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS failure_log (
  failure_id INTEGER PRIMARY KEY,
  test_id INTEGER,
  failure_type TEXT,
  description TEXT,
  action_taken TEXT,
  retest_flag INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
  log_id INTEGER PRIMARY KEY,
  table_name TEXT,
  record_id TEXT,
  action TEXT,
  detail TEXT,
  operator TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
