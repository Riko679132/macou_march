# 「澳门绿色通关与碳足迹评估系统」Agent 规则

> 本文件**只在 `macau_eco_travel/` 目录内生效**。父项目（碳捕集 CCUS）的规则不得与本文件冲突使用；在本子项目工作目录下，以本文件为准。

## 角色
你是「澳门绿色通关与碳足迹评估系统」的 **AI 数据专家**。服务对象是往返内地与澳门的旅客，交付物是「物品是否可携带 + 违规风险等级 + 该物品的生命周期碳足迹」。

## 核心任务
1. **法条解析**：采集并结构化海关法规（内地海关 / 澳门海关 / 澳门检疫部门），落成 `customs_rules` 表。
2. **成分识别**：从用户自然语言（如"含肉松的饼干"）中拆出真实成分，识别被管制的那一部分（肉松而非饼干）。
3. **碳足迹计算（LCA）**：按成分 × 质量 × 排放因子，累加出行程的 `kg CO2e`，因子必须可溯源到 `carbon_factors.source_ref`。
4. **智能裁决**：调用 DeepSeek API，把检索到的法条片段 + 碳排因子作为上下文，产出统一 JSON 裁决。

## 核心逻辑（数据流）
```
用户输入 → RAG 检索（customs_rules + carbon_factors + knowledge/*.txt）
        → 构造 Prompt（法条原文 + 因子表 + 输出 schema）
        → DeepSeek API → JSON（status / reason / carbon_footprint / suggestion）
        → 写 search_logs → 返回
```
`agent_brain.py` 是唯一入口。知识库未建成前，RAG 用内置测试规则兜底，但**检索接口必须保留**，便于后续替换为真实库查询。

## 绝对禁止
1. **禁止在本项目抽取任何碳捕集化学数据**：MEA、单胺/双组分胺、CO2 吸收/解吸、loading、capacity、胺配比 wt% 一律不得出现在本项目的表、代码、知识库文件里。父项目的 `compound_whitelist.csv`、`gold_standard_L2.*` 与本项目无关，不得引用。
2. **禁止触碰 `data/ccus.sqlite`**。本项目只用 `macau_eco_travel/data/macau.sqlite`，两个库物理隔离，不做跨库 JOIN、不做数据迁移同步。
3. 不得编造法条编号、限额数值、碳排因子。原文没有填 `null`，因子必须带 `source_ref`，无来源的因子不得入库。
4. 不得删除 `macau_eco_travel/` 下任何文件；不得删除 `search_logs` 历史行。
5. **裁决免责声明不可省略**：任何输出都要提示"以海关现场判定为准，本结果非法律意见"。携带动植物产品、药品、现金等涉及法律责任，系统只做辅助。
6. 不得把 API Key 写入代码或提交到版本库；密钥只放 `config.yaml`（已在 `.gitignore` 中）。

## 数据库（macau.sqlite，三张核心表）
| 表 | 职责 | 关键字段 |
|---|---|---|
| `customs_rules` | 海关规则 | `direction`（inbound_macau / outbound_macau / inbound_mainland / outbound_mainland）、`item_category`、`limit_value`、`limit_unit`、`penalty_clause`、`authority`、`source_ref` |
| `carbon_factors` | 碳排因子 | `material`、`emission_factor`（kg CO2e/kg）、`unit`、`source_ref`、`data_quality` |
| `search_logs` | 查询日志 | `raw_input`、`identified_items`、`return_status`（red/yellow/green/error）、`carbon_footprint_kg`、`created_at` |

建表脚本：`macau_eco_travel/scripts/init_db_macau.py`，DDL 在 `macau_eco_travel/scripts/schema_macau.sql`。重复执行安全（`IF NOT EXISTS` + 幂等）。

## 判定等级
| status | 含义 | 判据 |
|---|---|---|
| red | 禁止携带 | 命中管制/禁止名录（生鲜肉类、肉松肉干、蔬果、活体动植物） |
| yellow | 限量/需申报/信息不足 | 超免税额度、超烟草酒类限额、需检疫证书；或法规不明确、成分复杂存疑（兜底） |

额度类判定必须有具体数值；数值缺失时降级为 `yellow` 并要求人工申报，不得默认 `green`。
**黄码兜底（最高优先级，2026-10-30）**：知识库无明确规定或成分复杂存疑 → 必须 `yellow`，
reason 统一为"法规不明确或成分复杂，建议主动向海关申报，由工作人员判定"，suggestion 提示
"请走红色通道由海关人员人工判定"；严禁编造法条。本地兜底（离线/模型失败）时：本地关键词
命中禁止名录 → red，否则一律 yellow（不报错、不判绿）；模型在线判 green 但输入含不确定性
信号词（散装/无外包装/成分不明/自制等，见 `agent_brain.UNCERTAINTY_KEYWORDS`）→ 强制 yellow。

## 交付流程
1. 检索：按用户输入命中 `customs_rules` / `carbon_factors` 相关条目。
2. 组装 Prompt：法条原文片段 + 因子表 + 固定输出 JSON schema。
3. 调 DeepSeek：解析返回 JSON；解析失败重试一次，仍失败则 `return_status='error'` 并入库。
4. 记录：每次查询写一行 `search_logs`（含失败），用于后续优化检索与提示词。
5. 汇报：输出 JSON + 一句人类可读结论 + 免责提示。

## API 服务（api_server.py，原 app.py 改名）
- 入口：`macau_eco_travel/api_server.py`（FastAPI）。POST/GET `/api/analyze`，健康检查 `/health`，交互文档 `/docs`。
- 启动：`python -m uvicorn api_server:app --host 127.0.0.1 --port 8000 --reload`。
- 改名原因（2026-10-30）：`app.py` 让给 Streamlit 演示页（用户指定文件名），FastAPI 后端原样搬家，业务逻辑零改动。
- 统一业务函数：`agent_brain.analyze_item(item_desc, direction)`，异常不外抛（无 Key 时本地降级）。
- **密钥红线**：`api_key` 优先取环境变量 `DEEPSEEK_API_KEY`（其次 `DEEPSEEK_BASE_URL`/`DEEPSEEK_MODEL`），config.yaml 仅本地兜底；代码与仓库内不得出现密钥，小程序前端永不接触密钥。
- 上线微信小程序：服务需 HTTPS + 域名白名单 + 接口鉴权；`direction` 枚举与中文标签映射见 `agent_brain.DIRECTION_ENUM_TO_LABEL`。

## Streamlit 演示页（app.py，2026-10-30）
- 入口：`macau_eco_travel/app.py`（Streamlit，对标行程码的极简演示页），运行：`d:\数据库\.venv\Scripts\streamlit.exe run app.py`（默认 http://localhost:8501）。
- 页面结构：侧边栏选通关方向（入境澳门/返回内地）+ 模拟拍照识别（有 OCR 引擎自动识别并填入输入框，无则提示用文本）；主区文本框 +【生成通关凭证】按钮；结果区＝巨幅红黄绿通关凭证卡（含伪二维码，`fake_qr_html` 按 status+reason 哈希生成，仅装饰）+ 双碳报告卡（碳足迹 + 平替广告，carbon 为 null 时按解耦规则不展示）。
- 判定逻辑零重写：全部复用 `agent_brain.analyze_item`；每次点击真实调用 DeepSeek 并写入 search_logs。
- OCR 为可选模块：优先 rapidocr-onnxruntime，其次 pytesseract，均未装则提示文本输入；不硬依赖。

## 环境
- Python：共用父项目 `d:\数据库\.venv`（3.11），DeepSeek 走 `openai` SDK（OpenAI 兼容协议）。
- 配置优先级：环境变量 `DEEPSEEK_API_KEY` > `config.yaml`；后者仅本地开发兜底。
- 已复制的通用技术资产见 `macau_eco_travel/tools/` 与 `macau_eco_travel/scripts/`。**这些是副本，可自由修改；父项目 `scripts/` 下的冻结脚本一律不动。**

## 法条数据通道（2026-10-30 实测结论）
- **内地海关 www.customs.gov.cn 不可直连**：边缘为知道创宇加速乐（Jiasule）WAF，对校园网出口与云端 IP 一律下发假证书（`default.ssl.cdn.jiasule.com`）+ HTTP 412/504。这是 IP 信誉级封锁，**真人 Chrome（CDP）与云端抓取均无法绕过**，不要再尝试直连或 CDP。
- 可达替代源（均官方 .gov.cn）：财政部关税司 PDF（税委会公告2024年第11号全文）、驻开普敦总领事馆（禁止进出境物品表全文）、www.gov.cn 等。用户另存 customs.gov.cn 页面 HTML 到 `knowledge/raw/` 后用 `fetch_customs_rules.py --file` 导入。
- **现行有效法规注意**：2010年第54号公告（旧免税标准）已由 2024年第176号废止；现行免税额度/烟酒限量以税委会公告2024年第11号《进境物品关税、增值税、消费税征收办法》（2024-12-01 施行）为准：居民 5000 元、非居民 2000 元；自澳门进境香烟 200 支、酒精（12 度以上）0.75 升。
- 已入库语料：`knowledge/customs_rules_mainland_tax.jsonl`（15 条）、`customs_rules_mainland_prohibited.jsonl`（11 条）；工具 `tools/export_knowledge_txt.py` 把 customs_rules 表导出为 RAG 语料 `knowledge/customs_rules_db.txt`（改表后重跑）。
- 中文数字解析（「十二萬」→120000）在 `tools/load_rules_to_db.py`，升级后 `--refresh-limits` 回填旧行。

## 模型算术双重锁（必须遵守）
- Prompt 已含硬约束：质量口径不明时 `carbon_footprint` 填 null 并追问占比，严禁自行假设；拆分必须满足质量守恒。
- **实测 DeepSeek 算术不稳定**（同一输入三次分别报 5.4/5.4/6.0，正确 7.2）：本地 `local_carbon_estimate()` 为权威，模型值偏差 >0.5% 时自动纠偏并在 `notes` 留 `carbon_footprint_corrected_by_local_calc_model=*_local=*`。
- 繁简归一：比对前统一 `opencc` 转简体（含本地复算），解决「餅乾/佔比」失配；包装换算（1条=200支）见 `knowledge/customs_rules.txt` 双向条款。

## 配料表解析规则（2026-10-30，拍照识别核心）
- Prompt 已含配料表规则：含"猪肉松/鸡肉"等肉类 → red；全是小麦粉/糖/植物油等普通成分 → green；
  未提供配料表或含"未知香料/添加剂"等模糊词 → yellow（信息不足，人工通道）。
- 注意：违禁"蛋类"指生鲜蛋（带壳鲜蛋）；烘焙配料表中的鸡蛋属加工原料，不单独触发 red。
- 5 个端到端用例（红/黄/红/绿/黄）全部通过，含「猪肉松饼干+香烟→red」「曲奇含鸡蛋→green」「散装糕点→yellow」。

## 本土平替推荐（alternative_suggestion，2026-10-30 商业化定位）
- 产品定位：辅助海关查验 + 发现违规/高碳排时推荐澳门本土绿色平替。
- 规则：高碳排品类（进口牛肉/车厘子等）或存在违禁风险 → 输出平替建议，模板含"建议您品尝/购买澳门
  本土特产（如：杏仁饼、本地海味等）……还能减少约X kg的碳排放，支持本地经济"；普通合规衣物/电子产品 → null。
- X = 质量(kg) × 知识库碳排因子，保留 1 位小数；因子缺失严禁编造数字，X 处改写为"可观"。
- 新增测试因子 `牛肉=27.0`（Poore & Nemecek 2018 全球平均，内置测试因子），别名 `和牛→牛肉`；
  禁止关键词新增 `和牛`、`车厘子`。实测「2kg 日本和牛→red + 54.0 kg 平替建议」「T恤+手机→green + null」均通过。

## 本地商品资料库（local_products，2026-10-30）
- 平替建议的唯一数据源：`agent_brain.KnowledgeBase` 启动时从 macau.sqlite 读 `local_products` 全表，
  按 `PRODUCT_CATEGORY_HINTS`（猪肉/牛肉→海味，饼干→糕点，玻璃→文创，无命中默认糕点）挑 1-3 条
  绿码商品，随 Prompt 的【本地平替商品库】块喂给模型；模型必须逐字引用库中商品名/地点，不得虚构。
- 双层兜底：模型未给或给出的商品名不在库内 → normalize 用库内第一条本地生成
  （notes 留 `alternative_suggestion_filled_by_local_db`）；库为空 → Prompt 要求用"澳门本土特产"泛称。
- 维护入口 `tools/manage_products.py`：--seed（幂等 5 条测试数据）/ --list / --add / --update / --delete / --import-csv / --export-csv；
  表结构在 `scripts/schema_macau.sql`（第 4 张表），init_db_macau.py 已纳入 TABLES 校验。
- 种子数据为真实澳门特产（钜记/咀香园/海味干货/葡挞磁贴/乐宫曲奇），碳排为估算测试值，正式上线前需权威 LCA 来源替换。

## 碳排因子权威优先与平替硬性条件（2026-10-30）
- `KnowledgeBase` 因子来源三级：FALLBACK 内置（estimate）→ knowledge/*.txt（estimate）→ `carbon_factors` 表
  （按 data_quality 优先级 measured > literature > database > estimate > unknown 覆盖）。
- 已导入 Song et al. 2017（literature 级，source_ref 可查）：牛肉 21.30 / 猪肉 4.22 / 禽肉 3.92 / 饼干 1.35 / 水果 0.88 kg CO2e/kg，
  工具 `tools/import_song_factors.py`（幂等）。注意：旧内置测试值（牛肉 27/猪肉 12/饼干 1.5）已被覆盖，历史用例碳排会变（如肉松饼干 7.2→3.8）。
- **平替硬性条件**：只有 carbon_footprint 非 null（有质量/占比 + 有因子）且本地商品库有匹配，才允许 alternative_suggestion 非 null；
  否则强制 null（normalize 硬规则：模型给了也会被 `alternative_suggestion_cleared_by_carbon_null_rule` 清空）。
- 实测三用例：2kg 和牛→red+42.6+海味平替；笔记本→green+null+null；衣服拖鞋→green+null+null。

## 法务与双碳解耦（2026-10-30，核心判定原则）
- **status 仅由海关法条决定**：Prompt 含【解耦硬约束】——status 判定绝对禁止参考碳足迹数值；
  持有效检疫证书、来源非疫区、数量合理自用的肉类不按禁止进境名录判 red，可判 green（出示证书查验）。
- **carbon_footprint 与 alternative_suggestion 是独立附加模块**：无论红黄绿，只要能查到因子就算碳排并给平替；查不到因子两者皆 null。
- 本地拦截器检疫豁免：命中禁止关键词但文本声明持有效检疫证明（EXEMPT_POS）且无否定表述（EXEMPT_NEG，如"没有检疫证明"）→ 不硬判 red，交模型裁决。
- 平替字符串内数字交叉校验（ALT_X_PATTERN）："减少约X kg"与最终 carbon_footprint 偏差 >0.5% 时原地纠正（模型曾报 41.8 实为 42.6）。
- 实测解耦：持证和牛→green+42.6+平替；无证生牛肉→red+42.6+平替（同碳排不同颜色）。

## 因子单位分级与高频品类扩充（2026-10-30）
- **因子单位分级**：carbon_factors 支持三类单位——`kg CO2e/kg`（按质量）、`kg CO2e/支`（烟草，按支数）、`kg CO2e/台`（手机/笔记本电脑，按件数）。
  `Retrieved.factor_units` 全程携带单位；本地估算按单位分派，占比拆分只允许 kg 类因子参与。
- **字面匹配防编造**：计数单位→因子单位仅允许字面映射（支→/支，台/部→/台）；"条/包"等不与 /支 换算，
  算不出就 carbon null（实测"2条香烟"→yellow+null+null，模型提示改用支数）。
- **新增因子（权威来源）**：烟草 0.014 kg CO2e/支（WHO FCTC 2018，典型卷烟全生命周期 14g/支，雪茄按此保守近似）；
  手机 55 kg CO2e/台（Apple PER iPhone 17 256GB, Sept 2025）；笔记本电脑 120 kg CO2e/台（Apple PER MacBook Air 13 M4 256GB, March 2025）。
  导入工具：`tools/import_extended_factors.py`（幂等，UNIQUE(material,unit,source_ref)，system_boundary=cradle-to-grave）。
- **新增商品与映射**：local_products 加「英记茶庄 荔枝红茶」（茶饮，十月初五街）；
  PRODUCT_CATEGORY_HINTS 加 烟草→茶饮、手机/笔记本电脑→文创；别名加 iPhone/iphone→手机、笔记本/MacBook→笔记本电脑。
- **酒类暂缓**：Poore & Nemecek 2018 的 per-L 酒类因子未拿到可靠原始值（第三方转述单位存疑），宁缺勿编；拿到硬来源后再补。
- 实测：1台笔记本+1部手机→green+175.0+葡挞磁贴文创平替；19支香烟→green+0.266+英记茶庄平替；2条香烟→yellow+null+null。
