"""
生成《碳捕集吸收剂文献数据提取与质控 Agent 使用手册（小白版）》PDF。
- 中文字体：Windows 自带的 simhei.ttf
- 自动目录：fpdf2 的 insert_toc_placeholder
"""
from fpdf import FPDF
from fpdf.enums import XPos, YPos

FONT = "C:/Windows/Fonts/simhei.ttf"
OUT = "碳捕集Agent使用手册.pdf"

VERSION = "v2.0.0"
BUILD_DATE = "2026-10-27"


def render_toc(pdf, outline):
    pdf.set_font("hei", size=20)
    pdf.cell(0, 14, "目  录", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(6)
    pdf.set_font("hei", size=12)
    for sec in outline:
        label = f"{sec.name}   ······   第 {sec.page_number} 页"
        pdf.cell(0, 8, label, new_x=XPos.LMARGIN, new_y=YPos.NEXT,
                 link=pdf.add_link(page=sec.page_number))


pdf = FPDF(format="A4")
pdf.add_font("hei", "", FONT)
pdf.set_auto_page_break(auto=True, margin=18)
pdf.set_margins(left=18, top=18, right=18)

# ---------- 封面 ----------
pdf.add_page()
pdf.set_font("hei", size=24)
pdf.ln(56)
pdf.cell(0, 16, "碳捕集吸收剂", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
pdf.cell(0, 16, "文献数据提取与质控 Agent", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
pdf.set_font("hei", size=18)
pdf.ln(4)
pdf.cell(0, 14, "使用手册（小白版）", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
pdf.ln(18)
pdf.set_font("hei", size=12)
pdf.cell(0, 8, "版本 " + VERSION, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
pdf.cell(0, 8, "生成日期：" + BUILD_DATE, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
pdf.cell(0, 8, "工作目录：d:\\数据库", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

# ---------- 目录页（占位） ----------
pdf.add_page()
pdf.insert_toc_placeholder(render_toc, pages=1)


_first_section = [True]


def section(title):
    # insert_toc_placeholder 已自带一次分页，首个 section 不再 add_page，避免空白页
    if not _first_section[0]:
        pdf.add_page()
    _first_section[0] = False
    pdf.start_section(title, level=0)
    pdf.set_font("hei", size=20)
    pdf.cell(0, 12, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)


def sub(title):
    pdf.ln(2)
    pdf.set_font("hei", size=13)
    pdf.cell(0, 8, ">> " + title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)


def para(text):
    pdf.set_font("hei", size=11)
    pdf.multi_cell(pdf.epw, 6.5, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def code(text):
    pdf.set_font("hei", size=10)
    pdf.set_fill_color(240, 240, 240)
    pdf.multi_cell(pdf.epw, 6, text, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def bullet(items):
    pdf.set_font("hei", size=11)
    for it in items:
        pdf.multi_cell(pdf.epw, 6.5, "  -  " + it, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)


def note(text):
    pdf.set_font("hei", size=10)
    pdf.set_text_color(150, 40, 40)
    pdf.multi_cell(pdf.epw, 6, "！ " + text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


# ============================================================
section("一、这个 Agent 是干什么的")

para("它的唯一职责：把关于 CO2 捕集吸收剂（水性胺）的文献，变成可追溯的结构化数据，"
     "存进本地数据库 data\\ccus.sqlite。")

para("职责边界只有三句话：把文献/PDF 变成结构化数据 → 判定研究边界与 QC 级别 → "
     "通过冻结脚本入库并留痕。交付物到「可追溯的 L2 金标准数据集」为止。")

note("不在职责内：训练预测模型、主动学习选点、配方优化、流程仿真。"
     "这些属于下游团队。Agent 不产出模型、不做预测、不做配方推荐。")

sub("一句话流程")
code("目标 DOI  →  CDP 下载 PDF  →  解析全文  →  判边界  →  抽 JSONL  →  "
     "校验  →  入库  →  核验  →  导出金标准")

sub("当前家底（截至 " + BUILD_DATE + "）")
bullet([
    "化合物 4 种：MEA / PZ / AMP / MDEA。",
    "配方 16 个：单组分 13 个、双组分 3 个。",
    "来源文献 15 篇，实验点 59 条，性能记录 69 条。",
    "L2 金标准 37 条（文献实测），L3 参考 7 条（文献缺字段）+ 15 条（仿真）。",
])

# ============================================================
section("二、环境准备（一次性）")

para("只需要 Windows 10/11 加 Python 3.11（其他版本没测过）。所有依赖装在项目内的虚拟环境里，"
     "不污染系统。")

sub("1. 检查 Python 是否装好")
code("python --version")
para("显示 Python 3.11.x 即正常。若提示「不是内部或外部命令」，去 python.org 下载安装，"
     "安装时勾选 Add Python to PATH。")

sub("2. 打开项目目录")
code("cd d:\\数据库")

sub("3. 创建两个虚拟环境（各只需一次）")
code("python -m venv .venv\npython -m venv tools\\.venv")
para("为什么要两个：CDP 下载链依赖 playwright，PDF 复核依赖 pypdf，这套东西和 scripts/ 下的 "
     "requests/pdfplumber 混装容易在 Windows 上撞 DLL。所以主环境与工具环境严格分开。")

sub("4. 安装主环境依赖")
code(".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")
para("包含 pyyaml、pdfplumber、requests、beautifulsoup4、fpdf2。")

sub("5. 安装工具环境依赖")
code("tools\\.venv\\Scripts\\pip.exe install -r requirements-tools.txt")
para("包含 playwright、pypdf、fonttools、campus-paper-fetch。")

sub("6. 装浏览器驱动（CDP 下载必需）")
code("tools\\.venv\\Scripts\\python.exe -m playwright install chromium")

sub("7. 验证")
code(".\\.venv\\Scripts\\python.exe -c \"import yaml, pdfplumber, fpdf; print('main ok')\"\n"
     "tools\\.venv\\Scripts\\python.exe -c \"import playwright, pypdf, fontTools; print('tools ok')\"")
para("两行都打印 ok 即成功。")

# ============================================================
section("三、项目目录结构一览")

para("整个项目长这样（忽略 .venv 内部）：")
code("""d:\\数据库
├── AGENTS.md                     Agent 规则（身份、红线、流程）
├── SOP.md                        7 阶段标准作业流程（含 CDP 下载细则）
├── config.yaml                   数据库路径 + 检索 API 配置
├── requirements.txt              主环境依赖
├── requirements-tools.txt        工具环境依赖
├── gold_standard_L2.json         金标准导出（机器读）
├── gold_standard_L2.csv          金标准导出（人读）
├── 碳捕集Agent使用手册.pdf        本文件
├── .venv\\                        主虚拟环境
├── data\\
│   ├── ccus.sqlite               数据库本体（唯一写入目标）
│   ├── compound_whitelist.csv    化合物白名单（4 种胺）
│   ├── .db.lock                  文件锁占位
│   └── backups\\                  每次导入前的自动备份
├── extractions\\                  每批次抽取的全套中间文件
│   └── <batch>\\
│       ├── parsed\\               解析文本
│       ├── raw\\                  原始数据快照（L2 溯源凭证）
│       └── validated\\            校验产物
├── scripts\\                      数据库流水线（冻结）
│   ├── schema.sql                11 张表结构
│   ├── init_db.py                建库
│   ├── validate.py               校验（冻结）
│   ├── preview.py                预览（冻结）
│   ├── import_jsonl.py           导入（冻结）
│   ├── audit_log.py              审计留痕
│   ├── pdf_to_text.py            PDF 转文本
│   ├── fetch_elsevier.py         Scopus 检索
│   ├── fetch_fulltext.py         Unpaywall 下载
│   └── web_crawler.py            网页爬取
└── tools\\                        数据质控与下载工具
    ├── fetch_sd_cdp.py           ScienceDirect 付费墙下载（CDP）
    ├── fetch_pdf_cdp.py          通用付费墙下载（ACS/RSC/Wiley/Springer）
    ├── fetch_unpaywall_one.py    单个 DOI 补下载
    ├── verify_pdfs.py            下载后 PDF 完整性复核
    ├── triage_literature.py      候选池分档
    ├── export_gold_standard.py   金标准导出
    ├── postcheck.py              入库后核验（7 项硬指标）
    ├── _wordgrid.py              表格版面坐标重建
    ├── migrate_mw_fill.py        分子量补录
    ├── migrate_smiles_fix.py     SMILES 修正
    ├── migrate_recipe_check.py   recipe 约束迁移
    └── build_manual.py           本手册生成器""")

sub("看到「冻结」是什么意思")
para("validate.py、preview.py、import_jsonl.py、schema.sql 是 Agent 与数据之间的「契约」。"
     "Agent 不能自己改，必须你显式授权才能解冻。")

# ============================================================
section("四、两套虚拟环境（最容易踩的坑）")

para("这是本项目最容易出错的地方：两个 venv 装的东西不同，用错就报 ModuleNotFoundError。")

code("""用途        解释器                              装了什么
主环境      .\\.venv\\Scripts\\python.exe            pyyaml / pdfplumber /
                                                 requests / beautifulsoup4 / fpdf2
工具环境    tools\\.venv\\Scripts\\python.exe       playwright / pypdf /
                                                 fonttools / campus-paper-fetch""")

sub("谁用哪套")
bullet([
    "主环境：scripts\\ 下全部脚本、tools\\_wordgrid.py、triage_literature.py、"
    "postcheck.py、export_gold_standard.py、fetch_unpaywall_one.py、build_manual.py。",
    "工具环境：只有三个 —— fetch_sd_cdp.py、fetch_pdf_cdp.py、verify_pdfs.py。",
])

sub("两个真实事故（别重演）")
bullet([
    "verify_pdfs.py 用主环境跑 → No module named 'pypdf'。它必须用 tools\\.venv。",
    "fetch_unpaywall_one.py 用工具环境跑 → No module named 'requests'。"
    "它内部加载 scripts/fetch_fulltext.py，必须用主环境。",
])

note("pypdf 缺 fonttools 时解不了 CFF 字体，首页文本会读成空，"
     "正常 PDF 会被误判成扫描件——白白放弃一篇可抽的文献。fonttools 已列入 requirements-tools.txt。")

# ============================================================
section("五、配置文件 config.yaml 说明")

para("这是唯一需要「看懂」的配置。关键字段：")
code("""database:
  path: "./data/ccus.sqlite"
  backup_dir: "./data/backups"
  lock_file: "./data/.db.lock"

extraction:
  workdir: "./extractions"
  whitelist: "./data/compound_whitelist.csv"

scripts:
  validate: "./scripts/validate.py"
  preview:  "./scripts/preview.py"
  import:   "./scripts/import_jsonl.py"

schema_version: "v1.0.0"

elsevier:
  api_key: "<你的_Scopus_API_Key>"
  base_url: "https://api.elsevier.com/content/search/scopus"
  max_results: 100
  page_size: 25
  query: "<检索式>"

unpaywall:
  base_url: "https://api.unpaywall.org/v2"
  email: "<你的邮箱>"
""")

sub("字段含义")
bullet([
    "database.path：数据库文件在哪，默认 ./data/ccus.sqlite。",
    "database.backup_dir：每次导入前自动把老库复制一份带时间戳的快照到这里。",
    "database.lock_file：空占位文件，导入时加独占锁，防止两个进程同时写库。",
    "extraction.workdir：每批次的 JSON、解析文本、快照都放这里。",
    "extraction.whitelist：允许处理的化合物名单。",
    "elsevier.api_key：Scopus 检索必需，缺失时 fetch_elsevier.py 会直接拒绝执行。",
    "unpaywall.email：Unpaywall 要求带邮箱，用于查 OA 直链。",
])

note("config.yaml 里存的是明文 API Key 和邮箱，属于敏感文件。不要把该文件内容贴进任何对外文档、"
     "截图或提交记录。")

# ============================================================
section("六、白名单与化合物库")

para("当前允许 4 种胺，库里 7 列：")
code("standard_name,cas_no,smiles,inchikey,amine_class,purchasable,status")
code("""MEA    141-43-5    NCCO          primary_amine
PZ     110-85-0    C1CNCCN1      cyclic_amine
AMP    124-68-5    CC(C)(CO)N    hindered_amine
MDEA   105-59-9    CN(CCO)CCO    tertiary_amine""")

para("这四种之外的物质，即使论文以它为主角，也一律不抽。这是「收敛边界」的核心，不是可协商项。")

sub("结构红线")
note("SMILES 必须由 CAS 号或 InChIKey 反查得到，不得凭记忆手写。历史上曾把 AMP 写成新戊醇 "
     "CC(C)(C)CO（丢了氮）、把 MDEA 写成 DMAE CN(C)CCO（少一个羟乙基）。"
     "InChIKey 对而 SMILES 错时校验不会报错，但下游任何结构特征都会全错。")

sub("分子量从哪来")
para("compound.mw 由文献表格补录（tools/migrate_mw_fill.py），不得手写。当前："
     "MEA 61.08、PZ 86.14、AMP 89.14、MDEA 119.16，每条补录都在 audit_log 留了出处。")

# ============================================================
section("七、QC 四级体系与金标准")

para("每条数据入库时都会被打上一个质量级别。级别决定它能不能进入金标准。")
code("""级别   判据                                              用途
L1     作者提供的原始数据点（SI 表格 / 数据集文件）      金标准
L2     全文表格/正文完整单点：temperature_K、           金标准
       co2_vol_pct、initial_loading 齐全，配方闭合
L3     缺必填字段（soft 分支自动判）或 simulation        仅作参考
L4     硬违规，validate.py 拒收                          不入库""")

sub("L1/L2 的唯一凭证：原始数据快照")
para("必须在 test 里写 raw_data_path + raw_data_hash（sha256），指向 extractions/<batch>/raw/ 下的快照 JSON。"
     "快照须包含：DOI、PDF 路径、表号与表题、实验条件、该行原始文本、采用的换算/派生公式。")
note("没有快照即自动降为 L3。这是 L2 能被别人复算的唯一凭证，不是可选项。")

sub("金标准导出")
code(".\\.venv\\Scripts\\python.exe tools\\export_gold_standard.py")
para("把 qc_level='L2' AND test_origin='literature' 的记录导出到根目录 gold_standard_L2.json / .csv。"
     "L2 数量每变一次就重跑一次，保证根目录副本与库内一致。simulation 永不进入该文件。")

# ============================================================
section("八、研究边界：三条硬门槛")

para("这是整个项目最重要的判断。三条门槛任一不过，这篇文献的这张表就不抽。")

sub("门槛 1：体系")
para("水相单胺或双组分复配胺；总胺 20–50 wt%；无添加剂、无盐、无两相、无浆料。")
para("例外：纯密度/物性表可留作单位换算参考库（如 PZ 65 wt% 密度），只作参考，不入 test。")

sub("门槛 2：胺种")
para("只有 MEA / PZ / AMP / MDEA 及其复配。实测三例：")
bullet([
    "Hartono 2017 列了 15 种新溶剂，全不在白名单 → 只取 Table A1 的 MEA 基准 1 条。",
    "Chowdhury 2013 列了 24 种叔胺，只有 MDEA 入选 → 只取 Table 2 的 row 24，共 1 条。",
    "Hwang 2018 全是假想胺的热力学建模 → 判定边界外，0 条。",
])

sub("门槛 3：数据形态")
para("必须是表格或正文中的数值。只在 Fig 曲线里、需要读数才能得到的结果一律不抽，也不得像素估读。")

sub("判定顺序")
code("列表清单  →  逐表看有无数值  →  看表头工况是否在边界内  →  最后才读正文讨论")
para("反过来做会浪费大量时间在定性比较段落上。")

# ============================================================
section("九、核心流程（全自动，无确认闸门）")

para("现在是全自动流程：校验通过即导入，Agent 不再等你说 A 或 B。")
code("""1. 抽取   生成 extractions/<batch>/raw_agent.jsonl，缺失字段填 null
2. 校验   validate.py 产出三份文件（见下）
3. 预览   preview.py 输出 import_preview.md（仅记录，不阻塞）
4. 导入   自动依次导入 valid.jsonl、valid_incomplete.jsonl
5. 审计   audit_log.py 留痕
6. 核验   postcheck.py 七项硬指标必须全部 PASS
7. 摘要   报告 valid / incomplete / rejected 计数 + 六表增量""")

sub("validate.py 的三个分支")
code("""valid.jsonl             字段完整，qc_level 保持原样          → 入主库
valid_incomplete.jsonl  缺字段但配方闭合，自动 L3 + 标记     → 入主库
rejected.jsonl          硬违规                              → 永不入库""")
para("硬违规共 5 类：recipe_type / excluded_system / concentration_out_of_range / "
     "mass_balance / binary_identity。")
note("禁止跳过 validate.py 直接调用 import_jsonl.py。校验是唯一入闸门。"
     "rejected.jsonl 里的每一条都要回头改数据或改判定，不能「手动导入试试」。")

# ============================================================
section("十、VLE 与单位的四条特殊规则")

sub("规则 1：VLE 数据按单点处理")
para("VLE（给定温度与 CO2 分压下的平衡溶解度）没有「吸收终点」概念，表格里每一行 (P_CO2, alpha) "
     "都是一条 test，不是一张表一条。")

sub("规则 2：co2_vol_pct 只在原文给出总压时才允许派生")
code("co2_vol_pct = P_CO2 / P_total × 100")
para("这与作者由气相摩尔分数反算 P_CO2 互为逆运算，所以不算编造。必须在 qc_reason 记 "
     "co2_vol_pct_derived_from_stated_total_pressure_*。原文没给总压的分压点一律不抽。")

sub("规则 3：水蒸气自洽性校验")
code("P_CO2 + P_H2O(T)  <=  P_total")
para("不满足说明该点实际总压高于文中所述、原文未报，该温度整列弃用。"
     "Shen & Li 1992 的 60/80/100 °C 三列就是这么被剔除的（100 °C 时水蒸气 101.3 kPa + CO2 135.6 kPa "
     "= 236.9 > 180 kPa，装不下），只保留 40 °C，共 27 个点。")

sub("规则 4：体积基单位不换算")
para("文献只给 g-CO2/L-soln 且没给溶液密度时，直接以原单位入库，指标名加 _volumetric 后缀"
     "（如 cyclic_capacity_volumetric）。不得为了统一单位去反算 mol/mol。")
para("只有 molality（mol/kg 溶剂）可以用 wt% = m·M/(m·M+1000) 精确换算，"
     "并在 qc_reason 记 converted_from_molality。")

# ============================================================
section("十一、PDF 下载工具链（CDP）")

para("付费墙文献用普通 HTTP 请求下不到：即使拿到 S3 预签名直链，非浏览器客户端也只会收到 "
     "62 KB 的 Security verification 说明页。唯一可行路径是在真实浏览器网络栈内，"
     "于 Response 阶段用 CDP Fetch 域抢在 PDF viewer 消费掉 body 之前取走字节。")

sub("步骤 1：启动带 CDP 的浏览器（每批只做一次）")
code("powershell -NoProfile -ExecutionPolicy Bypass -File "
     "\"tools/.venv/Lib/site-packages/campus_paper_fetch/data/scripts/start-browser.ps1\"")
para("启动后监听 http://127.0.0.1:9222，是常驻会话。权限判据不是能否打开落地页，而是："
     "Elsevier 页面出现 Brought to you by: 你的机构；ACS 右上角出现机构缩写。")

sub("步骤 2：按出版社选脚本")
code("""出版社                DOI 前缀              脚本
Elsevier / SD         10.1016              tools/fetch_sd_cdp.py
ACS                   10.1021              tools/fetch_pdf_cdp.py
RSC                   10.1039              tools/fetch_pdf_cdp.py
Wiley                 10.1002              tools/fetch_pdf_cdp.py
Springer / Nature     10.1007 / 10.1038    tools/fetch_pdf_cdp.py
MDPI / Frontiers      10.3390 / 10.3389    走 Unpaywall，不用 CDP""")

sub("步骤 3：批量传入（脚本支持多 DOI）")
code("tools\\.venv\\Scripts\\python.exe tools\\fetch_sd_cdp.py ^\n"
     "  \"10.1016/j.ces.2016.08.017\" \"10.1016/j.ijggc.2016.12.018\"\n\n"
     "tools\\.venv\\Scripts\\python.exe tools\\fetch_pdf_cdp.py ^\n"
     "  \"10.1021/acs.iecr.4c03980\" \"10.1039/d6gc02196d\"")
para("一次 5–10 个为宜，别超过 20（页面超时 + 机房风控）。单进程复用同一 CDP 会话，"
     "不要为每个 DOI 重启浏览器。")

sub("步骤 4：逐篇复核（必做）")
code("tools\\.venv\\Scripts\\python.exe tools\\verify_pdfs.py\n"
     "tools\\.venv\\Scripts\\python.exe tools\\verify_pdfs.py --doi 10.1016/j.ces.2016.08.017")
para("检查四项：首字节 %PDF-、尾部含 %%EOF、页数 > 0、首页文本非空。输出 checked=24 bad=0 才算过。")

sub("失败速查")
code("""no_anchor        落地页没有 pdfft 链接 = 无机构权限   换网络/重新登录，重试 <= 2 次
not_pdf          拦到了但内容不是 PDF = 被拦        同上，不要降级去试普通 HTTP
Connection refused 9222                            浏览器没启动，回步骤 1
62 KB 小文件                                      误用了 requests 直连，回到 CDP""")

note("绝对不要为了「绕过」去写 requests / httpx 直连。历史上这样做的结果全是 62 KB 说明页。")

# ============================================================
section("十二、常用终端命令速查")

para("全部在 PowerShell 里运行，先把工作目录切到 d:\\数据库。")

sub("激活主环境（每次新开窗口都要）")
code(".\\.venv\\Scripts\\Activate.ps1")
para("激活成功后命令行开头会多出 (.venv) 字样。")

sub("PDF 转文本")
code(".\\.venv\\Scripts\\python.exe scripts\\pdf_to_text.py --pdf data\\raw_pdfs\\xxx.pdf\n"
     ".\\.venv\\Scripts\\python.exe scripts\\pdf_to_text.py --indir data\\raw_pdfs")

sub("表格版面坐标重建（核对无框线表专用）")
code(".\\.venv\\Scripts\\python.exe tools\\_wordgrid.py data\\raw_pdfs\\xxx.pdf 3 [y0 y1 x0 x1]")
para("按 round(top/4) 分带聚类、带内按 x0 排序重建列位。纯文本流会把多栏表串成一行，"
     "这个工具是唯一可靠的核对办法。")

sub("校验 + 导入")
code(".\\.venv\\Scripts\\python.exe scripts\\validate.py "
     "--input extractions\\<batch>\\raw_agent.jsonl --outdir extractions\\<batch>\\validated\n"
     ".\\.venv\\Scripts\\python.exe scripts\\import_jsonl.py "
     "--input extractions\\<batch>\\validated\\valid.jsonl")

sub("入库后核验（7 项硬指标）")
code(".\\.venv\\Scripts\\python.exe tools\\postcheck.py\n"
     ".\\.venv\\Scripts\\python.exe tools\\postcheck.py --batch 20261027_08")

sub("导出金标准")
code(".\\.venv\\Scripts\\python.exe tools\\export_gold_standard.py")

sub("查看数据库各表行数")
code(".\\.venv\\Scripts\\python.exe tools\\postcheck.py")

sub("查看目录树（排除虚拟环境）")
code("Get-ChildItem -Recurse | Where-Object FullName -notlike '*\\.venv\\*'")

# ============================================================
section("十三、数据库表结构与幂等性")

sub("11 张表")
code("""compound          化合物主数据（名称 / CAS / SMILES / InChIKey / 分子量）
recipe            配方（单组分或双组分，wt% 质量守恒）
source            文献来源（DOI / 标题 / 作者 / PDF 路径 / hash）
source_location   数据出处（表号 / 段落 / 原文片段）
method            实验方法（预留）
test              单个实验点（温度 / CO2 浓度 / 负载 / 批次）
performance       性能指标（多对多，一条 test 可含多个指标）
qc_record         QC 记录（预留）
candidate         候选配方（预留）
failure_log       失败记录（预留）
audit_log         审计留痕（谁在什么时候改了什么）""")

sub("recipe_id 是怎么算的")
code("recipe_id = sha1(胺A_id | 胺B_id | wt_a | wt_b | water_wt)")
para("只要配方一样 hash 就一样，配合 INSERT OR IGNORE 自动去重。这是设计上的幂等性："
     "同一篇文献重复导入不会产生重复配方。")

sub("两条硬约束")
bullet([
    "质量守恒：wt_a + wt_b + water_wt = 100，容差 0.1，由 CHECK 约束强制。",
    "双组分身份：recipe_type='binary' 时 amine_b 的 InChIKey 必须存在，否则 binary_identity 硬拒。",
])

# ============================================================
section("十四、入库后核验：postcheck")

para("每次导入后必须跑一次，七项硬指标全部 PASS 才算完成。")
code(".\\.venv\\Scripts\\python.exe tools\\postcheck.py --batch <批次>")

sub("七项硬指标")
code("""1. L2 缺原始数据快照            必须为 0
2. simulation 未强制 L3          必须为 0
3. simulation 混入 L1/L2         必须为 0
4. L2 配方不闭合                 必须为 0
5. binary 缺 amine_b 身份        必须为 0
6. 外键违例                      必须为 0
7. integrity_check               必须 ok""")

para("另外还会检查 L2 快照文件是否真的在磁盘上（当前 37/37 全部存在）。"
     "脚本退出码 0 表示全过、1 表示有违例，可以直接串联到批处理里拦截。")

# ============================================================
section("十五、绝对禁止事项（安全边界）")

para("这些写在 AGENTS.md 里，任何情况下都不能违反：")
bullet([
    "不得 import sqlite3 或直接读写 data\\ccus.sqlite —— 只能通过 import_jsonl.py。",
    "不得修改 validate.py、preview.py、import_jsonl.py、schema.sql —— 除非你显式授权解冻。",
    "不得编造 DOI、CAS、SMILES、InChIKey、工况数值 —— 原文没有的一律填 null。",
    "不得跳过 validate.py 直接调用 import_jsonl.py。",
    "不得删除 extractions\\ 下任何文件 —— 包括解析文本、快照、校验产物。",
    "不得删除 source 表引用的 PDF；删任何 PDF 前必须先写 removed_out_of_scope.csv 台账。",
    "不得删除或改写 CDP / API 工具链（fetch_sd_cdp、fetch_pdf_cdp、fetch_unpaywall_one、"
    "fetch_elsevier、fetch_fulltext、verify_pdfs、triage_literature、campus_paper_fetch）。",
    "无溶液密度时不得把 mol/L、g/L 换算成 mol/mol。",
    "不得从图中读数、不得像素估读；只取表格与正文数值。",
    "不得训练预测模型、不得做主动学习选点、不得做配方推荐。",
])

sub("标题与作者也要照抄")
para("曾把 Hartono 2017 的标题记成带 Part 2 副标题、作者多出 3 个人，全是凭记忆写的。"
     "正确做法是从 PDF 首页复制。这条和「不得编造工况数值」是同一个红线。")

# ============================================================
section("十六、常见问题 FAQ")

sub("Q1：为什么数据被拒？")
para("validate.py 检查 5 类硬违规。最常见的是 mass_balance ——配方 wt_a + wt_b + water_wt 没凑够 100。"
     "其次是 binary_identity —— 标了双组分却查不到 amine_b 的 InChIKey。"
     "看 rejected.jsonl 里的 reason 字段就能定位。")

sub("Q2：L2 和 L3 的关键差别是什么？")
para("只差两件事：三个必填字段是否齐全（temperature_K / co2_vol_pct / initial_loading），"
     "以及有没有原始数据快照。缺任何一个就是 L3，永远进不了金标准文件。")

sub("Q3：为什么这条 VLE 数据没抽？")
para("大概率踩了「总压未报」。原文只给 CO2 分压、没给总压时，co2_vol_pct 无法派生，"
     "只能弃抽或降 L3。也可能是水蒸气校验没过——那个温度下 P_CO2 + P_H2O 已经超过总压。")

sub("Q4：文献给的是 mol/kg 怎么办？")
para("先看能不能精确换算。molality（mol/kg 溶剂）可以：wt% = m·M/(m·M+1000)。"
     "mol/L 或 g/L 不行——那需要溶液密度，原文没给就不能编。")

sub("Q5：recipe 表为什么多个文献指向同一 recipe_id？")
para("recipe_id = sha1(胺A_id | 胺B_id | wt_a | wt_b | water_wt)。配方相同 hash 就相同，"
     "INSERT OR IGNORE 自动复用。这是幂等性设计，不是 bug。")

sub("Q6：批次号可以重复吗？")
para("可以，但会覆盖同批次目录下的 raw_agent.jsonl / valid.jsonl / rejected.jsonl。"
     "推荐用日期+序号：20261027_01、20261027_02 递增。")

sub("Q7：下载的 PDF 打不开或只有几页？")
para("先跑 verify_pdfs.py 看四项检查结果。如果 pages=0，注意 fetch_*_cdp.py 打印的 pages 是明文 "
     "/Type /Page 计数，ACS 这类对象流 PDF 会显示 0，以 verify_pdfs 的 pypdf 结果为准。")

sub("Q8：数据库文件可以手工打开看吗？")
para("可以，但必须只读。用 DB Browser for SQLite 以只读模式打开 data\\ccus.sqlite。"
     "不要用可写模式，避免与导入事务冲突。")

sub("Q9：我改错 config.yaml 了怎么办？")
para("只要 database.path 仍指向 ./data/ccus.sqlite 就不会有事。备份目录、锁文件路径改动后，"
     "建议手工建好新目录再跑。")

sub("Q10：想加新化合物怎么办？")
para("第一阶段不需要。执行指南要求只聚焦 MEA / PZ / AMP / MDEA 及其复配，"
     "扩白名单已被明确叫停。真要加，改 data\\compound_whitelist.csv 并同步 AGENTS.md。")

# ============================================================
section("十七、重置与归档")

sub("方式 A：归档式重命名（推荐，可回滚）")
para("不删文件、可回滚，Agent 可以直接执行：")
code("$ts = Get-Date -Format 'yyyyMMdd_HHmmss'\n"
     "Move-Item data\\ccus.sqlite data\\ccus_contaminated_$ts.sqlite.bak\n"
     ".\\.venv\\Scripts\\python.exe scripts\\init_db.py")
para("旧的 ccus.sqlite 被改名成 .bak，init_db.py 新建一个空的。")

sub("回滚方法")
code("Remove-Item data\\ccus.sqlite\n"
     "Rename-Item data\\ccus_contaminated_<时间戳>.sqlite.bak ccus.sqlite")

sub("方式 B：彻底重建（清空一切）")
para("由你手工完成，Agent 不参与删除。步骤：")
bullet([
    "关闭所有正在读写 d:\\数据库 的窗口。",
    "删除 data\\ccus.sqlite 与 data\\backups\\ 下的历史 .sqlite。",
    "如需清空抽取记录，再删 extractions\\ 下的批次目录（注意：这会失去全部 L2 溯源凭证）。",
    "跑 scripts\\init_db.py 重建空库。",
])
note("extractions\\ 是 L2 可复算的唯一凭证。除非确定不再需要，否则不要删批次目录，"
     "尤其不要删 raw\\ 下的快照 JSON。")

# ---------- 输出 ----------
pdf.output(OUT)
print(f"PDF 已生成：{OUT}")
