#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
agent_brain.py —— 澳门绿色通关与碳足迹评估系统 · AI 核心引擎

职责：
    用户自然语言 → RAG 检索（海关规则 + 碳排因子）→ DeepSeek 裁决 → JSON + 写 search_logs

用法：
    python agent_brain.py "我带了2公斤含肉松的饼干和1条香烟，要从内地去澳门"
    python agent_brain.py "<输入>" --offline     # 不调 API，只走本地规则兜底（无 Key 时可跑通链路）
    python agent_brain.py "<输入>" --show-prompt # 打印实际发给模型的上下文
    python agent_brain.py --seed-demo            # 把内置测试规则/因子写入 macau.sqlite
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:
    from opencc import OpenCC
    _T2S = OpenCC("t2s")
except ImportError:   # 未装 opencc-python-reimplemented 时退化为原文比对
    _T2S = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.yaml")

try:   # 联网搜索为可选增强，导入失败不影响主流程
    from web_search import search_web as _web_search
except Exception:  # noqa: BLE001
    _web_search = None


def to_simplified(text: Optional[str]) -> str:
    """繁体→简体归一；opencc 缺失或转换失败时原样返回，绝不静默破坏原文。"""
    if not text or _T2S is None:
        return text or ""
    try:
        return _T2S.convert(text)
    except Exception:
        return text

# ============================================================================
# 内置测试数据（知识库尚未录入时的兜底语料 —— RAG 接口保持不变，后续换真实库查询）
# ============================================================================

FALLBACK_RULES: Dict[str, str] = {
    "内地入境澳门": (
        "水果每人每日限1公斤、蔬菜每人每日限1公斤，果蔬合计每人每日不超过5公斤；"
        "限量内个人自用免检放行，有叶蔬菜须经市政署卫生检疫。"
        "（第209/2021号行政长官批示 / customs.gov.mo）"
    ),
}

FALLBACK_FACTORS: Dict[str, float] = {
    "饼干": 1.5,     # kg CO2e/kg
    "猪肉": 12.0,
    "牛肉": 27.0,    # kg CO2e/kg，全球平均（Poore & Nemecek 2018），内置测试因子
    "玻璃": 1.2,
}

# 成分归一：命中管制的是"肉松"这一成分，而非"饼干"整体
MATERIAL_ALIASES: Dict[str, str] = {
    "肉松": "猪肉",
    "肉干": "猪肉",
    "鲜肉": "猪肉",
    "和牛": "牛肉",
    "鸡肉": "禽肉",
    "鸭肉": "禽肉",
    "车厘子": "水果",
    "樱桃": "水果",
    "苹果": "水果",
    "梨": "水果",
    "香蕉": "水果",
    "橙子": "水果",
    "橘子": "水果",
    "桔子": "水果",
    "葡萄": "水果",
    "草莓": "水果",
    "西瓜": "水果",
    "桃子": "水果",
    "水蜜桃": "水果",
    "李子": "水果",
    "芒果": "水果",
    "菠萝": "水果",
    "柚子": "水果",
    "猕猴桃": "水果",
    "荔枝": "水果",
    "龙眼": "水果",
    "枇杷": "水果",
    "哈密瓜": "水果",
    "甜瓜": "水果",
    "蓝莓": "水果",
    "桑葚": "水果",
    "雪茄": "烟草",
    "香烟": "烟草",
    "iPhone": "手机",
    "iphone": "手机",
    "笔记本": "笔记本电脑",
    "MacBook": "笔记本电脑",
    "macbook": "笔记本电脑",
}

# 技术词优先于水果别名：避免「苹果手机」被误判成水果「苹果」
TECH_PHRASE_REPLACEMENTS = (
    ("苹果牌手机", "手机"), ("苹果手机", "手机"), ("Apple手机", "手机"), ("apple手机", "手机"),
    ("苹果笔记本", "笔记本电脑"), ("苹果电脑", "笔记本电脑"), ("苹果本", "笔记本电脑"),
    ("苹果牌电脑", "笔记本电脑"), ("苹果平板", "平板电脑"),
    ("iPhone", "手机"), ("iphone", "手机"),
    ("MacBook", "笔记本电脑"), ("macbook", "笔记本电脑"),
)


def mask_tech_terms(text: str) -> str:
    """把「苹果手机」等科技产品短语替换为对应品类，防止被当成水果。"""
    out = text
    for phrase, repl in sorted(TECH_PHRASE_REPLACEMENTS, key=lambda x: -len(x[0])):
        out = out.replace(phrase, repl)
    return out

# 判定用的禁限运关键词（本地兜底 + 交叉校验模型输出）
PROHIBITED_KEYWORDS = ("肉松", "肉干", "生鲜肉类", "活动物", "活体", "蔬菜", "水果",
                      "种子", "和牛", "车厘子")

# 方向化的生鲜农产品规则（2026-10-30 业务确认）：
#   入境澳门（内地入境澳门）：个人自用的新鲜水果/蔬菜允许携带，须向市政署申报卫生检疫；
#   返回内地（澳门入境内地）：内地禁止新鲜水果、蔬菜、活体植物等进境。
EDIBLE_PRODUCE = ("苹果", "水果", "生果", "梨", "香蕉", "葡萄", "草莓", "西瓜",
                  "橙子", "橘子", "桔子", "桃子", "李子", "樱桃", "车厘子", "荔枝",
                  "龙眼", "芒果", "菠萝", "柚子", "猕猴桃", "蔬菜", "青菜", "生菜",
                  "白菜", "菠菜", "番茄", "西红柿", "黄瓜", "辣椒", "茄子", "蘑菇", "菌菇")
PLANT_PRODUCE = ("鲜花", "苗木", "种子", "植物")
FRESH_PRODUCE_KEYWORDS = EDIBLE_PRODUCE + PLANT_PRODUCE
LEAFY_VEG = ("白菜", "生菜", "菠菜", "青菜", "油菜", "芹菜", "韭菜", "卷心菜",
             "甘蓝", "芥菜", "香菜", "上海青", "空心菜", "茼蒿", "油麦菜")

PRODUCE_HINT_REASON = {
    "red": "根据内地进境动植物检疫规定，新鲜水果、蔬菜、活体植物及种子等动植物产品禁止携带进境。",
    "green": "澳门允许个人自用的新鲜水果、蔬菜携带入境，入境时须向市政署申报卫生检疫（无需准照）。",
    "yellow": "果蔬超出个人自用限量，或属有叶蔬菜，须经市政署卫生检疫后方可放行。",
}
PRODUCE_HINT_SUGGESTION = {
    "red": "请勿携带该生鲜农产品进境；如确需携带，须事先办理检疫审批。",
    "green": "可正常携带入境，按现场要求向市政署申报卫生检疫即可。",
    "yellow": "请主动走红色通道向海关/市政署申报并接受检疫查验。",
}
TECH_HINT_REASON = "电子产品（如手机/电脑）属个人自用物品，一般不属禁止或受预先许可约束的货物，可正常携带。"
TECH_HINT_SUGGESTION = "可正常携带；如数量明显超出个人自用，请主动向海关申报。"


def direction_status_hint(text: str, direction: Optional[str]) -> Optional[str]:
    """方向化的果蔬确定性判定：返回内地=禁止(red)，入境澳门=个人自用允许(green)。"""
    n = mask_tech_terms(to_simplified(text))
    if direction == "澳门入境内地":
        if any(k in n for k in FRESH_PRODUCE_KEYWORDS):
            return "red"
    elif direction == "内地入境澳门":
        if any(k in n for k in EDIBLE_PRODUCE):
            other_red = any(k in n for k in ("肉松", "肉干", "生鲜肉类", "活动物", "活体", "和牛"))
            other_limit = any(k in n for k in ("香烟", "卷烟", "雪茄", "烟草", "酒", "现金", "货币"))
            if other_red or other_limit:
                return None
            leafy = any(k in n for k in LEAFY_VEG)
            total_kg = sum((q["kg"] or 0.0) for q in parse_quantities(n))
            # 有叶蔬菜需检疫 / 明显超总量 → 黄码（申报+检疫）
            if leafy or total_kg > 5.0:
                return "yellow"
            # 个人自用、非叶菜、数量≤1kg 或未标量 → 绿码
            if total_kg <= 1.0:
                return "green"
    return None


def build_web_query(user_input: str, direction: Optional[str]) -> str:
    d = {"内地入境澳门": "澳门 入境", "澳门入境内地": "内地 进境"}.get(direction or "", "")
    q = ("%s 携带 %s 海关 规定 最新" % (d, user_input)).strip()
    return re.sub(r"\s+", " ", q)[:80]


# ---------------------------------------------------------------------------
# 高价值物品特殊审查（举证倒置）：奢侈品/高价电子/名表/珠宝/高档化妆品
# ---------------------------------------------------------------------------
HIGH_VALUE_TERMS = (
    "LV", "Louis Vuitton", "Chanel", "香奈儿", "Hermes", "Hermès", "爱马仕",
    "Gucci", "古驰", "Dior", "迪奥", "Prada", "普拉达", "Rolex", "劳力士",
    "Omega", "欧米茄", "Cartier", "卡地亚", "Tiffany", "蒂芙尼", "Bvlgari",
    "宝格丽", "Patek", "百达翡丽", "江诗丹顿", "理查德米勒", "iPhone", "苹果手机",
    "苹果电脑", "MacBook", "iPad", "奢侈品", "名牌包", "名牌手袋", "名表", "名贵手表",
    "珠宝", "首饰", "钻石", "翡翠", "和田玉", "高档化妆品", "海蓝之谜", "La Mer",
    "SK-II", "兰蔻", "雅诗兰黛", "赫莲娜", "娇兰",
)
HV_GENERIC_NOUNS = ("手提包", "手袋", "背包", "书包", "钱包", "名牌包", "手表", "名表",
                    "项链", "戒指", "手镯", "耳环", "香水", "化妆品", "首饰", "运动鞋",
                    "球鞋", "高跟鞋", "腰带")
HV_NEW_WORDS = ("全新", "未拆封", "未开封", "带吊牌", "吊牌", "刚买", "新买", "专柜",
                "包装完整", "未使用")
HV_USED_WORDS = ("二手", "用过", "用过的", "自用", "旧物", "旧包", "旧表", "使用痕迹",
                 "背了", "戴了")
HV_MULTI_WORDS = ("多个", "几个", "数个", "一批", "大量", "若干", "多只", "多块",
                  "多部", "多件", "多套")
HV_QTY_PATTERN = re.compile(
    r"(\d+|[一二两三四五六七八九十百]+)\s*(个|只|块|枚|件|套|台|部|条|支|瓶|盒|双)")

HV_YELLOW_REASON = ("【需向海关申报】该物品属于高价值物品/超出合理自用数量。如果属于以下情况"
                    "可免税放行：1. 确属个人自用旧物（需提供购买凭证或使用痕迹）；2. 总价值未超过"
                    "5000元免税额度。如无法提供证明，请主动走红色通道申报补税，否则可能面临罚款。")
HV_YELLOW_SUGGESTION = "请主动走红色通道向海关申报；如属自用旧物，请准备购买凭证或使用痕迹备查。"
HV_RED_REASON = ("【涉嫌走私/代购】该物品属于高价值物品，且为全新/带吊牌/未拆封或数量明显超出"
                 "合理自用范围，依法须向海关申报并补税；未申报携带可能被认定为走私，面临罚款或没收。")
HV_RED_SUGGESTION = "请勿携带超量或全新高价值物品；如确需携带，请主动走红色通道申报补税；自用旧物需提供购买凭证或使用痕迹。"


def high_value_qty(text: str) -> Optional[float]:
    vals = []
    for m in HV_QTY_PATTERN.finditer(to_simplified(text)):
        s = m.group(1)
        v = float(s) if s[0].isdigit() else cn_num_to_float(s)
        if v:
            vals.append(v)
    return max(vals) if vals else None


def detect_high_value(text: str) -> Optional[str]:
    raw = to_simplified(text)
    low = raw.lower()
    for term in HIGH_VALUE_TERMS:
        if term.lower() in low:
            return term
    if any(n in raw for n in HV_GENERIC_NOUNS):
        q = high_value_qty(raw)
        if (q is not None and q >= 3) or any(w in raw for w in HV_MULTI_WORDS):
            return "多件高价值商品"
    return None


def high_value_hint(text: str) -> Optional[str]:
    """高价值物品举证倒置：全新或明显超量→red；其余→yellow（绝不 green）。"""
    if not detect_high_value(text):
        return None
    n = to_simplified(text)
    qty = high_value_qty(n)
    if any(w in n for w in HV_NEW_WORDS):
        return "red"
    if any(w in n for w in HV_MULTI_WORDS):
        return "red"
    if qty is not None and qty >= 3:
        return "red"
    return "yellow"


# ---------------------------------------------------------------------------
# 加工食品兜底：有配料表且无肉类/生鲜/种子等违禁成分 → 绿（不被坚果/蛋奶/致敏提示误判）
# ---------------------------------------------------------------------------
PROCESSED_FOOD_MARKERS = (
    "配料", "成份", "成分", "ingredient", "ingredients", "净重", "净含量", "保质期",
    "生产日期", "致敏", "过敏原", "饼干", "薄脆", "曲奇", "薯片", "巧克力", "糖果",
    "面包", "蛋糕", "糕点", "派", "酥", "脆", "饮料", "罐头", "果脯", "蜜饯",
    "坚果制品", "调味", "生产此产品", "厂房",
)
PROHIBITED_INGREDIENTS = (
    "肉松", "肉干", "火腿", "香肠", "培根", "牛肉", "猪肉", "鸡肉", "鸭肉", "羊肉",
    "鱼肉", "鱼干", "鱼糜", "虾", "蟹", "活体", "活动物", "新鲜水果", "新鲜蔬菜",
    "种子", "种苗", "苗木", "燕窝",
)
_NEG_PREFIX = ("不含", "不含有", "无", "没有", "零", "未使用", "不含任何")
_FLAVOR_SUFFIX = ("味", "风味", "香精", "调味")

PROCESSED_FOOD_REASON = ("经核实配料表，该商品为普通加工食品，配料中不含肉类、生鲜果蔬、"
                         "种子等禁止或受限成分，可正常携带。")
PROCESSED_FOOD_SUGGESTION = "可正常携带；如海关查验，出示包装配料表即可。"


def has_prohibited_ingredient(text: str) -> bool:
    n = to_simplified(text)
    for kw in PROHIBITED_INGREDIENTS:
        start = 0
        while True:
            i = n.find(kw, start)
            if i < 0:
                break
            pre = n[max(0, i - 6):i]
            post = n[i + len(kw):i + len(kw) + 3]
            if any(p in pre for p in _NEG_PREFIX):
                start = i + len(kw)
                continue
            if any(post.startswith(f) for f in _FLAVOR_SUFFIX):
                start = i + len(kw)
                continue
            return True
    return False


def processed_food_hint(text: str) -> Optional[str]:
    n = to_simplified(text).lower()
    if not any(mk.lower() in n for mk in PROCESSED_FOOD_MARKERS):
        return None
    if has_prohibited_ingredient(n):
        return None
    return "green"


# 具体品牌特征词：命中且本地无配料时，尝试外部查询
BRAND_KEYWORDS = ("奥利奥", "乐事", "士力架", "德芙", "趣多多", "好丽友", "康师傅", "统一",
                  "旺旺", "徐福记", "雀巢", "费列罗", "好时", "大白兔", "卫龙", "三只松鼠",
                  "良品铺子", "百草味", "达能", "可口可乐", "百事可乐", "伊利", "蒙牛", "光明")
_PRODUCT_CACHE: Dict[str, tuple] = {}
_PRODUCT_TTL = 1800


def detect_brand(text: str) -> Optional[str]:
    n = to_simplified(text)
    return next((b for b in BRAND_KEYWORDS if b in n), None)


def lookup_product_ingredients(item_name: str) -> List[Dict[str, str]]:
    """查询商品配料：DuckDuckGo 优先，失败回退 360；取前两条摘要。"""
    name = (item_name or "").strip()
    if not name:
        return []
    now = time.time()
    hit = _PRODUCT_CACHE.get(name)
    if hit and now - hit[0] < _PRODUCT_TTL:
        return hit[1]

    out: List[Dict[str, str]] = []
    try:  # 1) DuckDuckGo（duckduckgo-search，无需 Key）
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            for r in ddgs.text("%s 配料表" % name, max_results=5, region="cn-zh"):
                out.append({"title": r.get("title", ""), "snippet": r.get("body", ""),
                            "url": r.get("href", ""), "source": "duckduckgo"})
                if len(out) >= 2:
                    break
    except Exception:
        out = []

    if not out:  # 2) 回退 360 搜索
        try:
            results = _web_search("%s 配料表" % name, max_results=3) if _web_search else []
            for r in results:
                out.append({"title": r.get("title", ""), "snippet": r.get("snippet", ""),
                            "url": r.get("url", ""), "source": r.get("domain") or "web"})
                if len(out) >= 2:
                    break
        except Exception:
            out = []

    _PRODUCT_CACHE[name] = (now, out)
    return out
# 黄码兜底（2026-10-30 业务规则）：信息不足/法规不明确时统一措辞，严禁瞎猜
YELLOW_FALLBACK_REASON = "法规不明确或成分复杂，建议主动向海关申报，由工作人员判定"
YELLOW_FALLBACK_SUGGESTION = "请走红色通道由海关人员人工判定"
# 不确定性信号词：命中 + 本地未命中禁止名录 + 模型放行绿码 → 强制黄码
UNCERTAINTY_KEYWORDS = ("成分不明", "没有外包装", "无外包装", "散装", "自制", "未知",
                        "无标签", "标识不清", "来历不明", "不明成分", "家乡特产")
# 用户物品成分 → 优先推荐的本地商品类别（合规平替映射）
PRODUCT_CATEGORY_HINTS: Dict[str, List[str]] = {
    "猪肉": ["海味"],     # 违禁肉类 → 推合规海味干货
    "牛肉": ["海味"],
    "禽肉": ["海味"],
    "饼干": ["糕点"],     # 糕点类 → 推本地饼家
    "水果": ["糕点"],
    "玻璃": ["文创"],
    "烟草": ["茶饮"],     # 烟 → 推本地茶庄（替代性消费习惯）
    "手机": ["文创"],
    "笔记本电脑": ["文创"],
}
DIRECTION_PATTERNS = [
    (r"(从内地去|到澳门|入澳|前往澳门|去澳门)", "内地入境澳门"),
    (r"(从澳门去|到内地|入粤|回内地|去珠海|入境内地)", "澳门入境内地"),
]

# API 层方向归一：接受 schema 枚举值或中文口语，统一映射到知识库中文标签
DIRECTION_ENUM_TO_LABEL = {
    "inbound_macau": "内地入境澳门",
    "outbound_macau": "澳门入境内地",
    "inbound_mainland": "澳门入境内地",
    "outbound_mainland": "内地入境澳门",
    "bidirectional": "双向",
}


def normalize_direction(direction: Optional[str]) -> Optional[str]:
    """把 API 传入的方向归一成知识库标签；无法判定时原样透传（由 KB 兜底）。"""
    if not direction:
        return None
    d = str(direction).strip()
    if d in DIRECTION_ENUM_TO_LABEL:
        return DIRECTION_ENUM_TO_LABEL[d]
    if d in ("内地入境澳门", "澳门入境内地", "双向"):
        return d
    if any(w in d for w in ("回内地", "入境内地", "到珠海", "離澳", "离澳")):
        return "澳门入境内地"
    if any(w in d for w in ("去澳门", "入澳", "入境澳门", "来澳", "赴澳", "进澳")):
        return "内地入境澳门"
    return d

DISCLAIMER = "以上结论仅供参考，最终以海关现场判定为准，本结果不构成法律意见。"


# ============================================================================
# 配置
# ============================================================================

def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    """读取 config.yaml；文件缺失时返回可用默认值。"""
    defaults = {
        "deepseek": {
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "temperature": 0.2,
            "max_tokens": 1200,
            "timeout_seconds": 60,
        },
        "database": {"path": os.path.join(HERE, "data", "macau.sqlite")},
        "knowledge": {"dir": os.path.join(HERE, "knowledge")},
        "logging": {"save_llm_raw": True},
        "web_search": {"enabled": True, "max_results": 5, "timeout_seconds": 8},
    }
    if not os.path.exists(path):
        print("[WARN] 未找到配置文件 %s，使用默认值" % path)
        return defaults
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
    except ImportError:
        print("[WARN] 未安装 PyYAML，无法解析 config.yaml，使用默认值")
        return defaults

    for k, v in loaded.items():
        if isinstance(v, dict) and isinstance(defaults.get(k), dict):
            defaults[k].update(v)
        else:
            defaults[k] = v

    # 环境变量优先（防止 API Key 泄密 + 未来迁移云端/容器）：
    # DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
    # config.yaml 仅作本地兜底；微信小程序后端应只设环境变量、不携带密钥文件。
    env_map = {
        ("deepseek", "api_key"): "DEEPSEEK_API_KEY",
        ("deepseek", "base_url"): "DEEPSEEK_BASE_URL",
        ("deepseek", "model"): "DEEPSEEK_MODEL",
    }
    for (sec, key), env in env_map.items():
        val = os.environ.get(env, "").strip()
        if val:
            defaults[sec][key] = val

    ws_enabled = os.environ.get("WEB_SEARCH_ENABLED", "").strip().lower()
    if ws_enabled in ("0", "false", "no", "off"):
        defaults["web_search"]["enabled"] = False
    ws_max = os.environ.get("WEB_SEARCH_MAX_RESULTS", "").strip()
    if ws_max.isdigit():
        defaults["web_search"]["max_results"] = int(ws_max)

    db = defaults["database"]["path"]
    if not os.path.isabs(db):
        defaults["database"]["path"] = os.path.normpath(os.path.join(HERE, db))
    kn = defaults["knowledge"].get("dir")
    if kn and not os.path.isabs(kn):
        defaults["knowledge"]["dir"] = os.path.normpath(os.path.join(HERE, kn))
    return defaults


def resolve_api_key(cfg: Dict[str, Any]) -> Tuple[str, str]:
    """API Key 优先取环境变量 DEEPSEEK_API_KEY，其次 config.yaml。"""
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env, "env:DEEPSEEK_API_KEY"
    cfg_key = (cfg.get("deepseek", {}).get("api_key") or "").strip()
    if cfg_key and not cfg_key.startswith("REPLACE_WITH"):
        return cfg_key, "config.yaml"
    return "", "none"


# ============================================================================
# RAG 检索（v0：字符串匹配 + 本地 txt；接口签名预留数据库/向量检索替换点）
# ============================================================================

@dataclass
class Retrieved:
    rules: List[Tuple[str, str]] = field(default_factory=list)      # (来源标签, 规则原文)
    factors: List[Tuple[str, float]] = field(default_factory=list)  # (成分, 因子)
    factor_units: Dict[str, str] = field(default_factory=dict)      # 成分 → 因子单位
    matched_materials: List[str] = field(default_factory=list)
    product_info: List[Tuple[str, str]] = field(default_factory=list)  # 品牌 → 配料说明
    direction: str = "内地入境澳门"


class KnowledgeBase:
    """从 knowledge/*.txt 读语料，缺失时用内置 FALLBACK 兜底。"""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.rules: Dict[str, str] = dict(FALLBACK_RULES)
        self.factors: Dict[str, float] = dict(FALLBACK_FACTORS)
        self.factor_units: Dict[str, str] = {m: "kg CO2e/kg" for m in self.factors}
        self.products: List[Dict[str, Any]] = []   # local_products 全量缓存（量小）
        self.product_info: Dict[str, str] = {}     # common_products.txt：品牌 → 配料说明
        # FALLBACK 内置因子视为 estimate 级；DB 表内更高 data_quality 的因子覆盖之
        self._factor_quality: Dict[str, int] = {m: 3 for m in self.factors}
        self._load_txt()
        self._load_known_products()
        self._load_products()
        self._load_factors_from_db()

    def _load_known_products(self) -> None:
        """加载 UGC 已审核商品配料（审核通过后供后续检索复用）。"""
        db_path = self.cfg["database"]["path"]
        if "ccus" in os.path.basename(db_path).lower():
            return
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT product_name, ingredients, approved_status FROM known_products"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return
        for name, ing, st in rows:
            key = to_simplified(str(name or "")).strip()
            if key:
                self.product_info.setdefault(
                    key, "配料：%s；审核结论：%s（来源：UGC 已审核）" % (ing or "未提供", st or "未知"))

    def _load_factors_from_db(self) -> None:
        """从 carbon_factors 表加载带 source_ref 的权威因子（覆盖 FALLBACK/txt 语料）。
        同 material 多条时按 data_quality 优先级取最优：
        measured(0) > literature(1) > database(2) > estimate(3) > unknown(4)。"""
        db_path = self.cfg["database"]["path"]
        if "ccus" in os.path.basename(db_path).lower():
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT material, emission_factor, unit, data_quality FROM carbon_factors"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return
        order = {"measured": 0, "literature": 1, "database": 2, "estimate": 3, "unknown": 4}
        for r in rows:
            m = to_simplified(str(r["material"]))
            q = order.get(str(r["data_quality"] or "unknown"), 4)
            if m not in self._factor_quality or q < self._factor_quality[m]:
                self.factors[m] = float(r["emission_factor"])
                self.factor_units[m] = str(r["unit"] or "kg CO2e/kg")
                self._factor_quality[m] = q

    def _load_products(self) -> None:
        """从 macau.sqlite 读本地商品资料库；表不存在/异常时静默降级为空。"""
        db_path = self.cfg["database"]["path"]
        if "ccus" in os.path.basename(db_path).lower():
            return
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT product_id, name, category, carbon_footprint, compliance_risk, "
                "purchase_location, description FROM local_products ORDER BY product_id"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return
        self.products = [dict(r) for r in rows]

    def query_local_products(self, matched_materials: List[str]) -> List[Dict[str, Any]]:
        """按用户物品成分挑 1-3 条绿码本地商品，供平替建议使用。
        只返回合规商品；库为空时返回 []，模型按 Prompt 泛称兜底、不得虚构商品名。"""
        if not self.products:
            return []
        safe = [p for p in self.products if "绿码" in str(p.get("compliance_risk", ""))]
        if not safe:
            return []
        wanted: List[str] = []
        for m in matched_materials:
            wanted.extend(PRODUCT_CATEGORY_HINTS.get(m, []))
        if not wanted:
            wanted = ["糕点"]   # 无命中时默认推糕点（杏仁饼是通用手信）
        picks, seen = [], set()
        for cat in wanted:
            for p in safe:
                if p["category"] == cat and p["name"] not in seen:
                    picks.append(p)
                    seen.add(p["name"])
            if len(picks) >= 3:
                break
        return picks or [safe[0]]

    def _load_txt(self) -> None:
        """语料格式：规则 `标签<TAB或==>原文`；因子 `成分<TAB或==>数值`。
        键统一转简体再入库，匹配时同样把输入转简体，解决「餅乾」对不上「饼干」。
        """
        kdir = self.cfg["knowledge"]["dir"]
        if not os.path.isdir(kdir):
            return
        for fn in sorted(os.listdir(kdir)):
            if not fn.endswith(".txt"):
                continue
            path = os.path.join(kdir, fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except OSError as exc:
                print("[WARN] 语料读取失败 %s: %s" % (path, exc))
                continue
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = re.split(r"\t|==>|[:：]", line, maxsplit=1)
                if len(parts) != 2:
                    continue
                key = to_simplified(parts[0].strip())
                val = parts[1].strip()
                if fn.startswith("carbon"):
                    try:
                        self.factors[key] = float(val.split()[0].replace("kg CO2e/kg", ""))
                        self._factor_quality[key] = 3   # txt 语料视为 estimate 级
                    except ValueError:
                        continue
                elif fn.startswith("customs"):
                    self.rules[key] = val
                elif fn.startswith("common_products"):
                    self.product_info[key] = val

    def detect_direction(self, text: str) -> str:
        ntext = to_simplified(text)
        for pat, name in DIRECTION_PATTERNS:
            if re.search(pat, ntext):
                return name
        return "内地入境澳门"

    def match_materials(self, text: str) -> List[str]:
        """命中语料中的成分名（繁简归一后比对），并展开别名（肉松→猪肉）。"""
        ntext = mask_tech_terms(to_simplified(text))
        hits: List[str] = []
        for name in self.factors:      # factors 键已是简体
            if name in ntext and name not in hits:
                hits.append(name)
        for alias, std in MATERIAL_ALIASES.items():
            if to_simplified(alias) in ntext and std not in hits and std in self.factors:
                hits.append(std)
        return hits

    def match_product_info(self, text: str) -> List[Tuple[str, str]]:
        """命中本地商品配料知识（品牌关键词 → 配料说明）。"""
        ntext = mask_tech_terms(to_simplified(text))
        return [(name, info) for name, info in self.product_info.items() if name and name in ntext]

    def retrieve(self, text: str, direction: Optional[str] = None) -> Retrieved:
        # 显式方向（API/UI 传入）优先；否则按文本自动探测
        direction = direction or self.detect_direction(text)
        r = Retrieved(direction=direction)
        for label, body in self.rules.items():
            # 方向对齐：标签以方向名开头（如「澳门入境内地-烟草制品·限200支」）或双向规则
            if label.startswith(direction) or label.startswith("双向"):
                r.rules.append((label, body))
        if not r.rules:  # 兜底：方向没匹配上就把全部语料给模型
            r.rules = list(self.rules.items())
        r.matched_materials = self.match_materials(text)
        r.product_info = self.match_product_info(text)
        for m in r.matched_materials:
            r.factors.append((m, self.factors[m]))
            r.factor_units[m] = self.factor_units.get(m, "kg CO2e/kg")
        return r


# ============================================================================
# 数量解析 + 本地碳排估算（用于交叉校验模型算数）
# ============================================================================

QTY_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?|[零一二两三四五六七八九十半]+)\s*"
    r"(公斤|千克|kg|KG|斤|克|g|G|吨|条|包|盒|支|件|瓶|台|部|毫升|ml)"
)


def cn_num_to_float(s: str) -> Optional[float]:
    """常见中文数量词 → 数值：半=0.5，一~十、两、十五、二十 等。"""
    if s == "半":
        return 0.5
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if s == "十":
        return 10.0
    if "十" in s:
        a, b = s.split("十", 1)
        tens = digits.get(a, 1) if a else 1
        ones = digits.get(b, 0) if b else 0
        return float(tens * 10 + ones)
    if len(s) == 1:
        return float(digits[s]) if s in digits else None
    return None
UNIT_TO_KG = {"公斤": 1.0, "千克": 1.0, "kg": 1.0, "KG": 1.0, "斤": 0.5,
              "克": 0.001, "g": 0.001, "G": 0.001, "吨": 1000.0}
# 计数单位：不参与质量累加，但供 per-件 因子（kg CO2e/支、kg CO2e/台）按件数计算
COUNT_UNITS = {"条", "包", "盒", "支", "件", "瓶", "台", "部"}
# 计数单位 → 因子单位映射：只有字面匹配才算件数（"条/包"等不与 /支 换算，避免编造）
COUNT_UNIT_TO_FACTOR_UNIT = {"支": "kg CO2e/支", "台": "kg CO2e/台", "部": "kg CO2e/台"}

# 单位与因子匹配校验：数量量词和碳排因子单位不一致时，提示正确量词、不出通行证
KNOWN_QTY_UNITS = set(UNIT_TO_KG) | set(COUNT_UNITS) | {"毫升", "ml"}
UNIT_ALLOWED = {
    "kg CO2e/kg": set(UNIT_TO_KG),
    "kg CO2e/支": {"支"},
    "kg CO2e/台": {"台", "部"},
}
UNIT_EXPECTED_WORD = {
    "kg CO2e/kg": "公斤/克",
    "kg CO2e/支": "支",
    "kg CO2e/台": "台/部",
}
UNIT_EXAMPLE = {
    "kg CO2e/kg": "1公斤%s",
    "kg CO2e/支": "1支%s",
    "kg CO2e/台": "1台%s",
}


def detect_unit_mismatch(retrieved: Retrieved, text: str) -> Optional[Dict[str, str]]:
    """单一成分 + 单一数量时，若量词与因子单位不兼容则返回修正提示。"""
    if len(retrieved.matched_materials) != 1:
        return None
    qtys = parse_quantities(to_simplified(text))
    if len(qtys) != 1:
        return None
    m = retrieved.matched_materials[0]
    factor_unit = retrieved.factor_units.get(m, "kg CO2e/kg")
    allowed = UNIT_ALLOWED.get(factor_unit)
    if not allowed:
        return None
    q = qtys[0]
    if q["unit"] in allowed or q["unit"] not in KNOWN_QTY_UNITS:
        return None
    expected = UNIT_EXPECTED_WORD.get(factor_unit, "正确单位")
    example = UNIT_EXAMPLE.get(factor_unit, "") % m
    return {"material": m, "given_unit": q["unit"], "expected_unit": expected,
            "factor_unit": factor_unit, "example": example}

# “肉松占比20% / 肉松占 20% / 肉松含量20%” 这类口径
PCT_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z]{1,8}?)(?:占比|占|含量)[:：]?\s*(\d+(?:\.\d+)?)\s*%")


def parse_quantities(text: str) -> List[Dict[str, Any]]:
    """粗暴但够用：抓出所有 `数字+单位`，单位可换算成 kg 的才参与碳排累加。"""
    out = []
    for m in QTY_PATTERN.finditer(text):
        raw_val, unit = m.group(1), m.group(2)
        if raw_val[0].isdigit():
            val = float(raw_val)
        else:
            val = cn_num_to_float(raw_val)
            if val is None:
                continue
        span_start = max(0, m.start() - 12)
        context = text[span_start:m.start()]
        kg = val * UNIT_TO_KG.get(unit, 0.0)
        out.append({"raw": m.group(0), "value": val, "unit": unit,
                    "kg": kg if kg > 0 else None,
                    "count": val if unit in COUNT_UNITS else None,
                    "context": context})
    return out


def local_carbon_estimate(retrieved: Retrieved, text: str) -> Optional[float]:
    """本地估算：把命中成分的质量乘因子累加。无命中返回 None。

    口径处理（与 Prompt 硬约束对齐）：
    - 用户给出“X成分占比P%”时：按 总质量×P% 拆分给该成分，剩余归主成分；
    - 无占比且同一总重对应多成分时：不猜，按总重×主成分因子保守估算。
    """
    if not retrieved.matched_materials:
        return None
    ntext = to_simplified(text)   # 繁简归一：繁体「佔比」也能命中 PCT_PATTERN
    qtys = [q for q in parse_quantities(ntext) if q["kg"] or q["count"]]
    if not qtys:
        return None
    factors = dict(retrieved.factors)
    # 总重取最大质量基准；纯计数单位（如“2台手机”）无 kg 时按 0 处理
    total_kg = max((q["kg"] for q in qtys if q["kg"]), default=0.0)

    # 1) 优先：用户给出了成分占比 → 精确拆分
    pcts: Dict[str, float] = {}
    for m in PCT_PATTERN.finditer(ntext):
        name = m.group(1).strip()
        std = MATERIAL_ALIASES.get(name, name)
        if (std in factors and std in retrieved.matched_materials
                and retrieved.factor_units.get(std, "kg CO2e/kg") == "kg CO2e/kg"):
            pcts[std] = float(m.group(2)) / 100.0
    if pcts:
        subtotal = 0.0
        accounted = 0.0
        for std, pct in pcts.items():
            subtotal += total_kg * pct * factors[std]
            accounted += pct
        # 剩余质量归主成分（占比未提及的匹配成分中排第一者）
        main = next((m for m in retrieved.matched_materials if m not in pcts), None)
        if accounted < 1 and main and main in factors:
            subtotal += total_kg * (1 - accounted) * factors[main]
        return round(subtotal, 3)

    # 2) 无占比：按因子单位分派（kg CO2e/kg 用质量；kg CO2e/支、kg CO2e/台 用件数）
    total = 0.0
    for q in qtys:
        # 取该数字前文里出现的第一个成分名
        material = next((m for m in retrieved.matched_materials if m in q["context"]
                         or any(a == m and a in q["context"] for a, s in MATERIAL_ALIASES.items())),
                        retrieved.matched_materials[0])
        factor = factors.get(material)
        unit = retrieved.factor_units.get(material, "kg CO2e/kg")
        if not factor:
            continue
        if unit == "kg CO2e/kg" and q["kg"]:
            total += q["kg"] * factor
        elif (unit != "kg CO2e/kg" and q["count"]
                and COUNT_UNIT_TO_FACTOR_UNIT.get(q["unit"]) == unit):
            total += q["count"] * factor
    return round(total, 3) if total else None


# 检疫豁免信号：命中禁止关键词但用户声明持有效检疫证明 → 不硬判 red，交模型按法条裁决
EXEMPT_NEG = re.compile(r"(没有|沒有|无|無|未|缺|不带|不帶)[^。；;，,]{0,6}(检疫|檢疫)")
EXEMPT_POS = re.compile(r"(带齐|帶齊|持有|有|附有|携带|攜帶|出具|提供)[^。；;，,]{0,10}"
                        r"(检疫证书|检疫证明|檢疫證書|檢疫證明)")


def local_status(text: str, direction: Optional[str] = None) -> str:
    """本地禁止关键词拦截。持有效检疫证明的声明可豁免（交模型按法条裁决），
    "没有检疫证明"等否定表述不豁免。入境澳门时个人自用果蔬不按禁止处理。"""
    ntext = mask_tech_terms(to_simplified(text))
    if direction == "内地入境澳门":
        for kw in EDIBLE_PRODUCE:
            ntext = ntext.replace(kw, "")
    for kw in PROHIBITED_KEYWORDS:
        if kw in ntext:
            if EXEMPT_POS.search(ntext) and not EXEMPT_NEG.search(ntext):
                # 例："带齐了检疫证书的和牛" → 不硬判 red，由模型依据法条给出 green/yellow/red
                return "unknown"
            return "red"
    return "unknown"


# ============================================================================
# Prompt 构造
# ============================================================================

SYSTEM_PROMPT_TEMPLATE = """你是「澳门绿色通关与碳足迹评估系统」的合规与碳核算专家。
你必须**以下面检索到的本地海关规则与碳排因子为准**作答；【网络补充资料】仅作线索，
可能过时或非官方，与本地法条冲突时一律以本地法条为准；不得使用记忆中的其他法规数值。

【检索到的海关规则】
{rules_block}

【网络补充资料】（公开搜索，非权威，仅作线索；不得据此编造条款，引用时注明来源域名）
{web_block}

【参考知识·商品配料表】（具体品牌优先；来自本地知识库或联网搜索摘要，用于核对成分）
{product_knowledge_block}

【检索到的碳排因子】（单位见各行：kg CO2e/kg 按质量算；kg CO2e/支、kg CO2e/台 按件数算）
{factors_block}

【本地平替商品库】（来自数据库 local_products 的真实商品，供平替建议引用）
{products_block}

【通关方向】{direction}

【最高优先级·高价值物品特殊审查（举证倒置）】
1. 触发：奢侈品、高价电子产品、名贵手表、珠宝首饰、高档化妆品（如 LV、Chanel、爱马仕、劳力士、
   iPhone 等），或明确携带多个同类高价值物品（如 8 个包、5 块表）。
2. 举证倒置：此类物品**绝对禁止直接判 green**；即使未写"全新/未拆封"，也默认存在极高偷漏税风险。
3. 判定：明确"全新/带吊牌/未拆封"或数量明显超出合理自用（如 8 个包）→ red（涉嫌走私/代购）；
   "二手/自用旧物"但无法证实且未明确申报 → yellow（需人工判定）。
4. 若判 yellow，reason 必须严格使用以下模板：
   "【需向海关申报】该物品属于高价值物品/超出合理自用数量。如果属于以下情况可免税放行：1. 确属个人自用旧物（需提供购买凭证或使用痕迹）；2. 总价值未超过5000元免税额度。如无法提供证明，请主动走红色通道申报补税，否则可能面临罚款。"

【加工食品判定】若提供了配料表/成份，且为普通加工食品（饼干、薄脆、糕点、巧克力、薯片等），
即使含坚果、蛋、奶、麸质或"致敏物质提示"，也不得据此判 red；只有配料中实际含有肉类、
生鲜果蔬、种子、活体动植物等才判 red。配料清晰且无上述违禁成分 → green，reason 写明"经核实配料表"。

【最高优先级·合规判定顺序】你是严谨的海关合规专家，判定必须遵循以下顺序：
1. 若用户输入的是具体品牌商品（如奥利奥、德芙），先检查【参考知识·商品配料表】是否提供了
   该商品的真实配料表或搜索摘要。
2. 若找到配料表，逐项核对是否含违禁成分（肉类、生鲜、蔬菜水果、种子、活体动植物等），
   据此给出准确的 green 或 red。
3. 严禁过度保守：配料表清晰且全为普通食品成分（面粉、糖、植物油、可可粉、淀粉、食盐、
   香精等）时，必须判 green，并在 reason 中写明"经核实配料表，不含违禁成分"。
4. 只有当确实没有任何该商品信息、且用户也未提供配料表时，才可判 yellow，并提示用户
   拍照上传配料表或提供成分；此时 reason 用"法规不明确或成分复杂，建议主动向海关申报，
   由工作人员判定"，suggestion 提示"请走红色通道由海关人员人工判定"。
严禁为了给出明确答案而自行编造法条或配料。

【任务】
1. 从用户描述中拆解出所有携带物品及其**真实成分**（例："含肉松的饼干"含管制成分肉松，应按肉松判定）。
2. 配料表解析：当用户输入的是商品配料表（如：小麦粉、白砂糖、猪肉松、食品添加剂...）时，
   仔细核对其中是否含有违禁成分（如肉类、生鲜蛋类等）：
   - 配料表中有"猪肉松"、"鸡肉"等肉类成分 → 直接判 red（违规禁带）；
   - 配料表全是"小麦粉、糖、植物油"等普通成分 → 判 green；
   - 未提供明确配料表，或成分含"未知香料"、"添加剂"等模糊词汇 → 判 yellow（信息不足，建议走人工通道）。
   注意：违禁"蛋类"指生鲜蛋（整枚带壳鲜蛋等）；烘焙食品配料表中的鸡蛋属加工原料，不单独触发 red。
3. 依据规则判定通关风险：
   - red    = 命中禁止名录（生鲜肉类、肉松、肉干、蔬菜、活体动植物等），不得携带。
     例外：若物品持有效检疫证书/证明、来源非疫区、数量合理自用（如"带齐了检疫证书的和牛"），
     则不按禁止进境名录判 red，可判 green（通关时出示证书接受查验即可）。
   - yellow = 触发限额或需申报（香烟超 19 支、超免税额度 5000 元、需检疫证书等）；或信息不足、法规不明确、成分复杂存疑
   - green  = 全部合规（数量合法、符合检疫要求——如持有效检疫证书、来源非疫区——即 green）
   判定从严：只要任一项命中 red，整体即为 red。
   【解耦硬约束】status 的判定**绝对禁止**参考碳足迹数值：status 只看海关法规。
   物品数量合法、符合检疫要求时即为 green，哪怕其碳排极高（如进口和牛）；
   碳足迹计算结果仅作为附加提醒，绝不能影响通关凭证的颜色。
4. 计算生命周期碳足迹：
   - 质量类因子（单位 kg CO2e/kg）：质量(kg) × 因子 累加；
   - 计数类因子（单位 kg CO2e/支 如烟草、kg CO2e/台 如手机/笔记本电脑）：件数 × 因子，不需要质量；
     只有数量单位与因子单位字面一致才可计算（如“19支香烟”按 /支 算）；
     若用户说“X条/包香烟”而因子单位是 kg CO2e/支，严禁自行换算条→支，carbon_footprint 填 null；
   - 因子单位不是 kg CO2e/kg 时，严禁把"条/包/台/支"等计数当质量算。
   【硬约束】质量口径不明确（如只知道"含肉松的饼干"总重而不知肉松占比）或因子缺失时，
   carbon_footprint 一律填 null，严禁自行假设成分占比或缺失系数；并在 suggestion 中
   要求用户补充具体成分占比后重新计算。用户已给出占比（如"肉松占比20%"）时，
   按 总质量×占比 拆分后逐项计算。
   【守恒约束】拆分必须满足各成分质量之和等于总质量：成分 X 占比 P% 时其质量为
   总质量×P%，其余成分合计 总质量×(1−P%)；同一占比严禁同时套用到多个成分上，
   拆分后各成分质量相加必须与总质量一致，否则按算错处理。
5. 给出可执行建议（例如：肉类成分需寄回/办理检疫证书/香烟拆分为19支以内）。
6. 生成本土平替建议（字段 alternative_suggestion）：
   【硬性条件】只有同时满足以下两条，alternative_suggestion 才允许非 null：
   (a) 第 4 步算出的 carbon_footprint 不为 null（用户给了明确质量/占比且知识库有对应碳排因子）；
   (b) 【本地平替商品库】中存在与物品匹配的合适商品（库非空）。
   只要 carbon_footprint 为 null、查不到因子、没给质量/占比，或商品库为空/无匹配商品，
   alternative_suggestion 一律返回 null，严禁强行编造碳排或商品。
   满足条件时输出格式：
   "提醒：您携带的XXX属于高碳排物品（status 为 red/yellow 时写"高碳排/违规物品"）。
   建议您品尝/购买{{商品名}}（{{类别}}，购买地：{{地点}}），
   不仅完全合规，还能减少约X kg的碳排放，支持本地经济。"
   {{商品名}}/{{类别}}/{{地点}} 必须严格取自【本地平替商品库】，逐字引用库中商品全名与购买地点，
   严禁虚构或编造库中不存在的商品名/品牌。
   XXX 填具体物品名；X 直接取第 4 步算出的 carbon_footprint（保留 1 位小数），不要重算。

【输出要求】
只输出一个 JSON 对象，不要任何解释性文字、不要 markdown 代码块。严格遵循：
{{"status": "red|yellow|green", "reason": "判定依据，需引用具体规则条款", "identified_items": [{{"name": "物品名", "material": "管制成分", "quantity_kg": 数值或null}}], "carbon_footprint": 数值或null, "carbon_unit": "kg CO2e", "suggestion": "给用户的行动建议", "alternative_suggestion": "平替建议字符串或null", "confidence": 0.0-1.0}}"""


def build_messages(retrieved: Retrieved, user_input: str,
                   products: Optional[List[Dict[str, Any]]] = None,
                   web_results: Optional[List[Dict[str, str]]] = None,
                   product_knowledge: Optional[List[Dict[str, str]]] = None
                   ) -> Tuple[List[Dict[str, str]], str]:
    rules_block = "\n".join("- [%s] %s" % (lbl, body) for lbl, body in retrieved.rules) or "（无检索结果）"
    if web_results:
        web_block = "\n".join(
            "- [%s] %s %s" % (r.get("domain") or "", r.get("title", "")[:70],
                              (r.get("url") or ""))
            for r in web_results[:5])
    else:
        web_block = "（本次无可用的网络补充资料）"
    if product_knowledge:
        product_knowledge_block = "\n".join(
            "- [%s] %s" % (k.get("source", ""), (k.get("text") or "")[:300])
            for k in product_knowledge[:5])
    else:
        product_knowledge_block = "（未找到该商品的配料信息）"
    if retrieved.factors:
        factors_block = "\n".join(
            "- %s: %s %s" % (m, ("%.4g" % v), retrieved.factor_units.get(m, "kg CO2e/kg"))
            for m, v in retrieved.factors)
    else:
        factors_block = "（未检索到相关碳排因子）"
    if products:
        products_block = "\n".join(
            "- [%s] 类别:%s | 碳排:%s kg CO2e/kg | 合规:%s | 购买地:%s | 卖点:%s"
            % (p["name"], p["category"], ("%.3g" % float(p["carbon_footprint"])),
               p.get("compliance_risk") or "绿码/安全",
               p.get("purchase_location") or "待补充", p.get("description") or "")
            for p in products)
    else:
        products_block = ("（本地商品资料库为空：生成平替建议时只能用\"澳门本土特产\"泛称，"
                          "严禁虚构商品名/品牌）")
    system = SYSTEM_PROMPT_TEMPLATE.format(
        rules_block=rules_block, web_block=web_block,
        product_knowledge_block=product_knowledge_block,
        factors_block=factors_block, products_block=products_block,
        direction=retrieved.direction)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user_input}]
    return messages, system


# ============================================================================
# LLM 调用
# ============================================================================

def call_deepseek(messages: List[Dict[str, str]], cfg: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (原始文本, 模型名)。未装 SDK / 无 Key / 网络异常时抛 RuntimeError。"""
    ds = cfg["deepseek"]
    api_key, source = resolve_api_key(cfg)
    if not api_key:
        raise RuntimeError("缺少 DeepSeek API Key（请填 config.yaml 的 deepseek.api_key "
                           "或设置环境变量 DEEPSEEK_API_KEY，来源判定=%s）" % source)
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("未安装 openai SDK，请执行：pip install openai") from exc

    client = OpenAI(api_key=api_key, base_url=ds.get("base_url", "https://api.deepseek.com"),
                    timeout=ds.get("timeout_seconds", 60))
    model = ds.get("model", "deepseek-chat")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=float(ds.get("temperature", 0.2)),
            max_tokens=int(ds.get("max_tokens", 1200)),
            response_format={"type": "json_object"},   # 强制 JSON 输出
        )
    except Exception as exc:  # openai.* 各种异常统一收敛
        raise RuntimeError("DeepSeek 调用失败：%s: %s" % (type(exc).__name__, exc)) from exc
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("DeepSeek 返回空内容")
    return content, model


def extract_json(raw: str) -> Dict[str, Any]:
    """容错解析：剥掉 ```json 代码块、截取首个 {...}。"""
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S).strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", txt, flags=re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("模型输出无法解析为 JSON：%s" % raw[:300])


# ============================================================================
# 结果归一 + 入库
# ============================================================================

# 平替建议字符串内的 "减少约X kg" 数字（与 carbon_footprint 交叉校验用）
ALT_X_PATTERN = re.compile(r"减少约\s*([\d.]+)\s*kg")


def normalize(result: Dict[str, Any], retrieved: Retrieved, local_cf: Optional[float],
              local_st: str, text: str = "",
              products: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    status = str(result.get("status", "")).lower().strip()
    if status not in ("red", "yellow", "green"):
        # 模型给了非法值时用本地判定兜底
        status = local_st if local_st != "unknown" else "yellow"
        result["status"] = status
        result.setdefault("notes", []).append("status_normalized_from_model_output")
    if local_st == "red" and status != "red":
        # 交叉校验：本地命中禁止名录而模型放行 → 从严改判
        result["status"] = "red"
        result.setdefault("notes", []).append("status_upgraded_to_red_by_local_keyword_check")
    if (local_st != "red" and status == "green"
            and any(kw in to_simplified(text) for kw in UNCERTAINTY_KEYWORDS)):
        # 黄码兜底：输入含不确定性信号（散装/无标签/成分不明等）而模型放行 → 强制黄码，
        # 禁止 AI 瞎猜（2026-10-30 业务规则）
        result["status"] = "yellow"
        result["reason"] = YELLOW_FALLBACK_REASON
        result["suggestion"] = YELLOW_FALLBACK_SUGGESTION
        result.setdefault("notes", []).append("status_set_yellow_by_uncertainty_guard")

    if result.get("carbon_footprint") in (None, "") and local_cf is not None:
        result["carbon_footprint"] = local_cf
        result.setdefault("notes", []).append("carbon_footprint_filled_by_local_calc")
    if local_cf is not None and isinstance(result.get("carbon_footprint"), (int, float)):
        if abs(result["carbon_footprint"] - local_cf) / max(local_cf, 1e-9) > 0.005:
            # 实测同一输入三次调用模型分别报 5.4/5.4/6.0（正确 7.2）：LLM 算术
            # 既不可靠也不稳定。本地复算是确定性公式计算、可溯源 → 只要本地能算，
            # 本地值即为权威，模型值记入 notes 备查。
            result.setdefault("notes", []).append(
                "carbon_footprint_corrected_by_local_calc_model=%.3f_local=%.3f"
                % (result["carbon_footprint"], local_cf))
            result["carbon_footprint"] = local_cf
    # ---- 平替建议硬规则（2026-10-30）：carbon_footprint 为 null 时一律不给平替 ----
    if (result.get("carbon_footprint") is None
            and result.get("alternative_suggestion") is not None):
        # 查不到因子/没给质量 → 模型仍给平替则强制清空，杜绝编造
        result["alternative_suggestion"] = None
        result.setdefault("notes", []).append("alternative_suggestion_cleared_by_carbon_null_rule")
    elif (products and result.get("carbon_footprint") is not None):
        # 平替兜底（与 status 解耦）：碳排算得出 + 库内有商品 → 无论红黄绿都生成，杜绝幻觉
        alt = str(result.get("alternative_suggestion") or "")
        brand_hit = bool(alt) and any(
            p["name"] in alt or p["name"].split()[0] in alt for p in products)
        if not brand_hit:
            p = products[0]
            x = "%.1f" % local_cf if local_cf is not None else "可观"
            label = "高碳排物品" if result.get("status") == "green" else "高碳排/违规物品"
            result["alternative_suggestion"] = (
                "提醒：您携带的物品属于%s。建议您品尝/购买%s（%s，购买地：%s），"
                "不仅完全合规，还能减少约%s kg的碳排放，支持本地经济。"
                % (label, p["name"], p["category"], p.get("purchase_location") or "待补充", x))
            result.setdefault("notes", []).append("alternative_suggestion_filled_by_local_db")
        else:
            # 字符串内数字交叉校验：模型心算漂移（如 carbon=42.6 却写"减少约41.8 kg"）→ 原地纠正
            m = ALT_X_PATTERN.search(alt)
            if m:
                alt_x = float(m.group(1))
                cf = float(result["carbon_footprint"])
                if abs(alt_x - cf) / max(cf, 1e-9) > 0.005:
                    result["alternative_suggestion"] = alt.replace(
                        m.group(0), "减少约%.1f kg" % cf)
                    result.setdefault("notes", []).append(
                        "alternative_suggestion_number_corrected_by_local_calc")
    result.setdefault("carbon_unit", "kg CO2e")
    result["disclaimer"] = DISCLAIMER
    result["retrieval"] = {
        "direction": retrieved.direction,
        "matched_rules": [lbl for lbl, _ in retrieved.rules],
        "matched_materials": retrieved.matched_materials,
        "local_carbon_estimate": local_cf,
    }
    return result


def write_log(cfg: Dict[str, Any], user_input: str, retrieved: Retrieved,
              result: Dict[str, Any], model: str, latency_ms: int,
              raw: str, error: Optional[str]) -> None:
    db_path = cfg["database"]["path"]
    if "ccus" in os.path.basename(db_path).lower():
        print("[ABORT] 配置指向 ccus 库，拒写")
        return
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO search_logs (raw_input, direction, identified_items, return_status, "
            "carbon_footprint_kg, matched_rules, matched_factors, llm_model, llm_raw_response, "
            "latency_ms, error_msg) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_input, retrieved.direction,
             json.dumps(result.get("identified_items", []), ensure_ascii=False),
             result.get("status") or ("error" if error else "yellow"),
             result.get("carbon_footprint"),
             ",".join(lbl for lbl, _ in retrieved.rules) or None,
             ",".join(retrieved.matched_materials) or None,
             model or None,
             (raw[:4000] if cfg.get("logging", {}).get("save_llm_raw", True) else None),
             latency_ms, error))
        conn.commit()
    except sqlite3.Error as exc:
        print("[WARN] 日志写入失败：%s" % exc)
    finally:
        conn.close()


# ============================================================================
# 主流程
# ============================================================================

def decide(user_input: str, cfg: Dict[str, Any], kb: KnowledgeBase,
            offline: bool = False, show_prompt: bool = False,
            direction: Optional[str] = None) -> Dict[str, Any]:
    # 显式指定的方向必须参与检索（否则规则仍按文本默认方向，出现"方向反了"）
    retrieved = kb.retrieve(user_input, direction=direction)
    local_cf = local_carbon_estimate(retrieved, user_input)
    local_st = local_status(user_input, retrieved.direction)
    products = kb.query_local_products(retrieved.matched_materials)

    # 数量单位与因子单位不匹配 → 提示正确量词，不出通行证（不调模型、不入碳排）
    mismatch = detect_unit_mismatch(retrieved, user_input)
    if mismatch:
        result = {
            "status": "error",
            "reason": "数量单位「%s」与「%s」的碳排计量单位不符（%s 按「%s」计）。"
                      % (mismatch["given_unit"], mismatch["material"],
                         mismatch["material"], mismatch["factor_unit"]),
            "identified_items": [{"name": mismatch["material"],
                                  "material": mismatch["material"], "quantity_kg": None}],
            "carbon_footprint": None,
            "suggestion": "请改用正确量词「%s」描述数量（例如「%s」）后再试。"
                          % (mismatch["expected_unit"], mismatch["example"]),
            "input_error": True,
            "expected_unit": mismatch["expected_unit"],
            "disclaimer": DISCLAIMER,
        }
        write_log(cfg, user_input, retrieved, result, "unit_mismatch", 0, "", "unit_mismatch")
        result["_meta"] = {"model": "unit_mismatch", "latency_ms": 0, "log_written": True}
        return result

    # 参考知识：本地商品配料优先；品牌且本地缺失时外部查询配料表
    product_knowledge: List[Dict[str, str]] = [
        {"source": "本地知识库", "text": info} for _, info in retrieved.product_info]
    if not product_knowledge and not offline:
        brand = detect_brand(user_input)
        if brand:
            for r in lookup_product_ingredients(brand):
                text = ((r.get("title") or "") + " " + (r.get("snippet") or "")).strip()
                if text:
                    product_knowledge.append({"source": r.get("source") or "web", "text": text})

    # 联网搜索（可选）：结果作为非权威补充线索，注入 Prompt
    web_results: List[Dict[str, Any]] = []
    ws = cfg.get("web_search", {}) or {}
    if not offline and _web_search and ws.get("enabled", True):
        try:
            web_results = _web_search(
                build_web_query(user_input, retrieved.direction),
                max_results=int(ws.get("max_results", 5)),
                timeout=float(ws.get("timeout_seconds", 8)),
            )
        except Exception:
            web_results = []

    messages, system = build_messages(retrieved, user_input, products, web_results,
                                      product_knowledge)

    if show_prompt:
        print("=" * 70)
        print("[SYSTEM PROMPT]\n%s" % system)
        print("[USER]\n%s" % user_input)
        print("=" * 70)

    t0 = time.time()
    raw, model, err = "", "", None
    if offline:
        if local_st == "red":
            result = {
                "status": "red",
                "reason": "[离线模式] 未调用大模型，本地命中禁止名录：%s"
                          % "; ".join(b for _, b in retrieved.rules),
                "identified_items": [{"name": m, "material": m, "quantity_kg": None}
                                     for m in retrieved.matched_materials],
                "carbon_footprint": local_cf,
                "suggestion": "该物品属禁止携带品类，请勿携带过关。",
            }
        else:
            # 本地拦截器无命中 → 不猜、不判绿，一律黄码走人工通道（2026-10-30 业务规则）
            result = {
                "status": "yellow",
                "reason": YELLOW_FALLBACK_REASON,
                "identified_items": [{"name": m, "material": m, "quantity_kg": None}
                                     for m in retrieved.matched_materials],
                "carbon_footprint": local_cf,
                "suggestion": YELLOW_FALLBACK_SUGGESTION,
            }
        model = "offline_fallback"
    else:
        try:
            raw, model = call_deepseek(messages, cfg)
            result = extract_json(raw)
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            err = str(exc)
            if local_st == "red":
                result = {"status": "red",
                          "reason": "大模型不可用，本地命中禁止名录：%s"
                                    % "; ".join(b for _, b in retrieved.rules),
                          "identified_items": [], "carbon_footprint": local_cf,
                          "suggestion": "该物品属禁止携带品类，请勿携带过关。"}
            else:
                # 本地拦截器无命中 + 大模型超时/失败 → 不报错、不判绿，黄码走人工通道
                result = {"status": "yellow",
                          "reason": YELLOW_FALLBACK_REASON,
                          "identified_items": [], "carbon_footprint": local_cf,
                          "suggestion": YELLOW_FALLBACK_SUGGESTION}
            model = model or "local_fallback"

    # 科技产品优先：含「苹果手机」等词时按电子设备判定，避免与水果「苹果」混淆
    raw_norm = to_simplified(user_input)
    if any(p in raw_norm for p, _ in TECH_PHRASE_REPLACEMENTS):
        masked = mask_tech_terms(raw_norm)
        other_red = any(k in masked for k in ("肉松", "肉干", "生鲜肉类", "活动物", "活体", "和牛"))
        other_limit = any(k in raw_norm for k in ("香烟", "卷烟", "雪茄", "烟草", "酒", "现金", "货币"))
        produce_outside = any(k in masked for k in FRESH_PRODUCE_KEYWORDS)
        if not other_red and not other_limit and not produce_outside:
            if result.get("status") != "green":
                result["status"] = "green"
                result["reason"] = TECH_HINT_REASON
                result["suggestion"] = TECH_HINT_SUGGESTION
                notes = result.get("notes")
                if not isinstance(notes, list):
                    notes = []
                    result["notes"] = notes
                notes.append("status_overridden_by_tech_product_rule")

    # 方向化果蔬规则：模型判反时以确定性规则纠正（返回内地=red / 入境澳门=green）
    hint = direction_status_hint(user_input, retrieved.direction)
    if hint and result.get("status") != hint:
        result["status"] = hint
        result["reason"] = PRODUCE_HINT_REASON[hint]
        result["suggestion"] = PRODUCE_HINT_SUGGESTION[hint]
        notes = result.get("notes")
        if not isinstance(notes, list):
            notes = []
            result["notes"] = notes
        notes.append("status_overridden_by_direction_produce_rule")

    # 加工食品兜底：有配料表且不含肉类/生鲜/种子等 → green（避免坚果/蛋奶/致敏提示误判）
    pf = processed_food_hint(user_input)
    if pf and result.get("status") != pf:
        result["status"] = pf
        result["reason"] = PROCESSED_FOOD_REASON
        result["suggestion"] = PROCESSED_FOOD_SUGGESTION
        notes = result.get("notes")
        if not isinstance(notes, list):
            notes = []
            result["notes"] = notes
        notes.append("status_overridden_by_processed_food_rule")

    # 高价值物品审查（举证倒置）：绝不直接 green；全新/超量→red，其余→yellow
    hv = high_value_hint(user_input)
    if hv:
        result["status"] = hv
        result["reason"] = HV_RED_REASON if hv == "red" else HV_YELLOW_REASON
        result["suggestion"] = HV_RED_SUGGESTION if hv == "red" else HV_YELLOW_SUGGESTION
        notes = result.get("notes")
        if not isinstance(notes, list):
            notes = []
            result["notes"] = notes
        notes.append("status_overridden_by_high_value_rule")

    latency = int((time.time() - t0) * 1000)
    result = normalize(result, retrieved, local_cf, local_st, user_input, products)
    if web_results:
        result["web_sources"] = [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "domain": r.get("domain", "")}
            for r in web_results[:3]
        ]
    if err:
        result["error"] = err
    write_log(cfg, user_input, retrieved, result, model, latency, raw, err)
    result["_meta"] = {"model": model, "latency_ms": latency, "log_written": True}
    return result


def analyze_item(item_desc: str, direction: Optional[str] = None) -> Dict[str, Any]:
    """对外统一入口（FastAPI / 小程序后端调用）。

    输入：物品描述 + 通关方向（可空，空则由文本探测）。
    输出：裁决 JSON（status / reason / identified_items / carbon_footprint /
          suggestion / disclaimer / retrieval）。
    不抛异常：大模型不可用时自动降级为本地规则判定并返回 status。
    """
    desc = (item_desc or "").strip()
    if not desc:
        return {"status": "error", "reason": "item_desc 为空",
                "identified_items": [], "carbon_footprint": None,
                "suggestion": None, "disclaimer": DISCLAIMER}
    cfg = load_config()
    kb = KnowledgeBase(cfg)
    result = decide(desc, cfg, kb, offline=False, show_prompt=False,
                    direction=normalize_direction(direction))
    result.pop("_meta", None)   # 调试元信息不进 API 响应
    return result


def seed_demo(cfg: Dict[str, Any]) -> None:
    """把内置测试规则/因子写入 macau.sqlite，供后续真实检索使用。"""
    db_path = cfg["database"]["path"]
    conn = sqlite3.connect(db_path)
    try:
        for label, body in FALLBACK_RULES.items():
            conn.execute(
                "INSERT OR IGNORE INTO customs_rules (direction, item_category, rule_type, "
                "penalty_clause, authority, source_ref, raw_text) VALUES (?,?,?,?,?,?,?)",
                ("inbound_macau", label, "prohibited", None, "澳门海关/市政署",
                 "内置测试语料（待替换为正式法条）", body))
        for material, ef in FALLBACK_FACTORS.items():
            conn.execute(
                "INSERT OR IGNORE INTO carbon_factors (material, category, emission_factor, "
                "unit, source_ref, data_quality, notes) VALUES (?,?,?,?,?,?,?)",
                (material, "food" if material in ("饼干", "猪肉") else "packaging",
                 ef, "kg CO2e/kg", "内置测试因子（待替换为正式 LCA 数据库来源）",
                 "estimate", "seed_demo 写入"))
        conn.commit()
        nr = conn.execute("SELECT COUNT(*) FROM customs_rules").fetchone()[0]
        nf = conn.execute("SELECT COUNT(*) FROM carbon_factors").fetchone()[0]
        print("[OK] demo 语料已入库：customs_rules=%d 行, carbon_factors=%d 行" % (nr, nf))
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="澳门绿色通关与碳足迹评估 · AI 核心引擎")
    ap.add_argument("user_input", nargs="?", help="用户携带物品描述")
    ap.add_argument("--offline", action="store_true", help="不调 API，走本地规则兜底")
    ap.add_argument("--show-prompt", action="store_true", help="打印发给模型的完整上下文")
    ap.add_argument("--pretty", action="store_true", help="缩进输出 JSON")
    ap.add_argument("--seed-demo", action="store_true", help="内置测试语料写入 macau.sqlite")
    args = ap.parse_args()

    cfg = load_config()
    if args.seed_demo:
        seed_demo(cfg)
        return 0
    if not args.user_input:
        ap.print_help()
        return 1

    kb = KnowledgeBase(cfg)
    result = decide(args.user_input, cfg, kb, offline=args.offline, show_prompt=args.show_prompt)
    indent = 2 if args.pretty else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
