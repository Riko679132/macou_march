-- ============================================================
-- 澳门绿色通关与碳足迹评估系统 · macau.sqlite DDL
-- 与父项目 ccus.sqlite 完全物理隔离，禁止跨库引用
-- ============================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- 1. 海关规则表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customs_rules (
    rule_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    direction       TEXT    NOT NULL CHECK (direction IN (
                        'inbound_macau',     -- 内地 -> 澳门，入境澳门
                        'outbound_macau',    -- 澳门 -> 内地，入境内地
                        'inbound_mainland',  -- 入境内地
                        'outbound_mainland', -- 出境内地
                        'bidirectional'      -- 双向同规则
                    )),
    item_category   TEXT    NOT NULL,              -- 物品类别（如 生鲜肉类 / 烟草 / 现金）
    item_subtype    TEXT,                          -- 细分（如 肉松、肉干、雪茄）
    rule_type       TEXT    NOT NULL CHECK (
                        rule_type IN ('prohibited', 'restricted', 'declaration', 'duty_free')
                    ),
    limit_value     REAL,                          -- 限额数值，无额度则 NULL
    limit_unit      TEXT,                          -- 单位：支 / 克 / 千克 / 澳门元 / 元
    limit_evidence  TEXT,                          -- 抽出该数值的原文片段（防误读，可核对）
    penalty_clause  TEXT,                          -- 处罚条款原文或法条编号
    authority       TEXT,                          -- 发布机构（内地海关 / 澳门海关 / 市政署…）
    effective_date  TEXT,                          -- 生效日期 YYYY-MM-DD
    source_ref      TEXT,                          -- 溯源：法规名称 + 条款号 / URL
    raw_text        TEXT,                          -- 原文片段，供 RAG 上下文使用
    dedup_hash      TEXT,                          -- sha256(source_ref|text_sha256)，幂等键
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
    -- 幂等靠唯一索引 idx_rules_dedup(dedup_hash)；
    -- 不再用旧的业务字段 UNIQUE 组合，否则不同条款会因 limit_value 相同而互相冲突被吞
);

CREATE INDEX IF NOT EXISTS idx_rules_direction  ON customs_rules(direction);
CREATE INDEX IF NOT EXISTS idx_rules_category   ON customs_rules(item_category);
CREATE INDEX IF NOT EXISTS idx_rules_type       ON customs_rules(rule_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_dedup ON customs_rules(dedup_hash);

-- ------------------------------------------------------------
-- 2. 碳排因子表（LCA）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS carbon_factors (
    factor_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    material        TEXT    NOT NULL,              -- 物品成分（饼干 / 猪肉 / 玻璃）
    material_en     TEXT,                          -- 英文名，便于对齐国际因子库
    category        TEXT,                          -- 大类：food / packaging / textile / electronics
    emission_factor REAL    NOT NULL CHECK (emission_factor >= 0),
    unit            TEXT    NOT NULL DEFAULT 'kg CO2e/kg',
    system_boundary TEXT    NOT NULL DEFAULT 'cradle-to-gate' CHECK (
                        system_boundary IN ('cradle-to-gate', 'cradle-to-grave', 'gate-to-gate')
                    ),
    source_ref      TEXT    NOT NULL,              -- 无来源的因子不得入库
    data_quality    TEXT    NOT NULL DEFAULT 'unknown' CHECK (
                        data_quality IN ('measured', 'literature', 'database', 'estimate', 'unknown')
                    ),
    valid_year      INTEGER,                       -- 因子数据年份
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (material, unit, source_ref)
);

CREATE INDEX IF NOT EXISTS idx_factors_material ON carbon_factors(material);
CREATE INDEX IF NOT EXISTS idx_factors_category ON carbon_factors(category);

-- ------------------------------------------------------------
-- 3. 用户查询日志表（后续优化用）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_logs (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_input           TEXT    NOT NULL,          -- 用户原始输入
    direction           TEXT,                      -- 判定使用的通关方向
    identified_items    TEXT,                      -- 拆解出的物品/成分（JSON 数组）
    return_status       TEXT    NOT NULL CHECK (
                            return_status IN ('red', 'yellow', 'green', 'error')
                        ),
    carbon_footprint_kg REAL,                      -- 行程碳足迹，失败为 NULL
    matched_rules       TEXT,                      -- 命中的 rule_id 列表（逗号分隔）
    matched_factors     TEXT,                      -- 命中的 factor_id 列表
    llm_model           TEXT,                      -- 实际调用的模型名
    llm_raw_response    TEXT,                      -- 原始返回，便于排查解析问题
    latency_ms          INTEGER,
    error_msg           TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_logs_status ON search_logs(return_status);
CREATE INDEX IF NOT EXISTS idx_logs_time   ON search_logs(created_at);

-- ------------------------------------------------------------
-- 4. 本地商品资料库（平替建议的唯一数据源，模型不得虚构商品名）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS local_products (
    product_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL UNIQUE,      -- 商品名称（如 钜记手工杏仁饼）
    category          TEXT    NOT NULL,             -- 类别：糕点 / 海味 / 肉干 / 文创
    carbon_footprint  REAL    NOT NULL CHECK (carbon_footprint >= 0),  -- kg CO2e/kg
    compliance_risk   TEXT    NOT NULL DEFAULT '绿码/安全',   -- 合规风险
    purchase_location TEXT,                         -- 购买地点（如 大三巴街、官也街）
    description       TEXT,                         -- 卖点描述
    created_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_products_category ON local_products(category);

-- ------------------------------------------------------------
-- 5. 用户众包暂存区（前端提交，24h 后由 AI 审核）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name  TEXT    NOT NULL,
    description   TEXT,
    user_status   TEXT    NOT NULL CHECK (user_status IN ('green', 'yellow', 'red')),
    submit_time   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    review_status TEXT    NOT NULL DEFAULT 'pending'
                          CHECK (review_status IN ('pending', 'approved', 'rejected'))
);
CREATE INDEX IF NOT EXISTS idx_pending_review ON pending_products(review_status, submit_time);

-- ------------------------------------------------------------
-- 6. 已审核商品库（UGC 审核通过后入库，供 RAG 读取）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS known_products (
    product_name    TEXT    PRIMARY KEY,
    ingredients     TEXT,
    approved_status TEXT    NOT NULL CHECK (approved_status IN ('green', 'yellow', 'red')),
    source          TEXT    NOT NULL DEFAULT 'UGC',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at      TEXT
);
