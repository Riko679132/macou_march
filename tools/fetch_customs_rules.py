#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fetch_customs_rules.py —— 海关法规原文抓取 + 清洗成 RAG 语料（JSONL）

数据流：
    官网 URL / 本地文件 → 抓 HTML → 去导航噪音 → 按条款切块 → knowledge/raw/*.txt 留痕
    → knowledge/customs_rules.jsonl（每行一条：id / text / direction / source_ref ...）

用法：
    python tools/fetch_customs_rules.py --source macau_customs_notice
    python tools/fetch_customs_rules.py --url <URL> --direction inbound_macau --authority "澳门海关"
    python tools/fetch_customs_rules.py --file knowledge/raw/dropin.html --source-id mainland_customs
    python tools/fetch_customs_rules.py --list-sources
    python tools/fetch_customs_rules.py --source macau_customs_notice --dry-run

红线（遵循 AGENTS.md）：
    1. 只入库官网抓到的**原文**，绝不凭记忆补写法条；抓不到就报错退出。
    2. 每条语料必须带 source_ref（法规名 + URL），否则不写出。
    3. 抓取留痕文件（raw/*.html + *.txt）永久保留，是后续核对的唯一凭证。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    sys.exit("[ABORT] 缺少依赖 requests / beautifulsoup4：%s" % exc)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
KNOWLEDGE_DIR = os.path.join(PROJECT_ROOT, "knowledge")
RAW_DIR = os.path.join(KNOWLEDGE_DIR, "raw")
JSONL_PATH = os.path.join(KNOWLEDGE_DIR, "customs_rules.jsonl")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,zh-TW;q=0.8",
}
SLEEP_SECONDS = 2.0
TIMEOUT = 25

# 正文容器候选（按命中率排序）；澳门海关 CMS 用 fold-content
CONTENT_SELECTORS = [
    ".fold-content", ".fold__content", ".cms-layout-column.dbcolumn-right-layout",
    "#ozContent", "#content", ".content", ".article-content", ".trs_editor_view",
    ".TRS_Editor", "article", ".main-content",
]
# 噪音容器：导航、菜单、页脚、面包屑
NOISE_SELECTORS = [
    ".left_menu", ".nav", ".navbar", "nav", "header", "footer", ".breadcrumb",
    ".menu", ".tab", ".youtube", ".share", ".copyright", ".linklist", "script",
    "style", "noscript", "iframe",
]

# 条款切分：第X条 / 第X條 / 一、 / （一） / 1.
ARTICLE_SPLIT = re.compile(
    r"(?=(?:第[一二三四五六七八九十百零〇\d]+[条條章節])|(?:[（(][一二三四五六七八九十][)）])"
    r"|(?:^[一二三四五六七八九十]+、))",
    re.M,
)

# ---------------------------------------------------------------------------
# 源站点登记表
# ---------------------------------------------------------------------------
SOURCES: Dict[str, Dict[str, Any]] = {
    "macau_customs_notice": {
        "label": "澳门海关 · 旅客须知",
        "authority": "澳门海关 (Alfândega de Macau)",
        "direction": "inbound_macau",
        "lang": "zh-Hant",
        "urls": [
            "https://www.customs.gov.mo/cn/notice_passengers_item1.html",
            "https://www.customs.gov.mo/cn/notice_passengers_item2.html",
            "https://www.customs.gov.mo/cn/notice_passengers_item3.html",
            "https://www.customs.gov.mo/cn/notice_passengers_item4.html",
        ],
        # 内地海关 www.customs.gov.cn 对本机出口返回 HTTP 412（JS 风控），
        # 需走 tools/fetch_*_cdp.py 的 CDP 浏览器通道渲染后再取文本，
        # 或用户在浏览器里另存 HTML 到 knowledge/raw/ 后用 --file 导入。
    },
    "macau_customs_declaration": {
        "label": "澳门海关 · 申报通关系列（item1-13）",
        "authority": "澳门海关 (Alfândega de Macau)",
        "direction": "inbound_macau",
        "lang": "zh-Hant",
        "urls": ["https://www.customs.gov.mo/cn/customs_declaration_item%d.html" % i
                 for i in range(1, 14)],
        # 由 notice_passengers_item1 页面的链接摸到该系列；限额/禁止类规则多集中在此
    },
    "mainland_customs_traveler": {
        "label": "中国海关总署 · 进出境旅客行李物品监管",
        "authority": "中华人民共和国海关总署",
        "direction": "inbound_mainland",
        "lang": "zh-Hans",
        "urls": [
            "http://www.customs.gov.cn/customs/302249/302266/index.html",
        ],
        "known_block": "加速乐(Jiasule) WAF 按 IP 信誉封锁（假证书+412/504），CDP 真人 Chrome 与云端均无法绕过；已改走财政部关税司 PDF / 驻外使领馆镜像，或用户另存 HTML 后用 --file 导入",
    },
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def http_get(url: str) -> "requests.Response":
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp


def follow_meta_refresh(html: str, base_url: str) -> Optional[str]:
    """澳门海关首页用 <meta http-equiv=refresh> 跳转语言版本，需手动解析。"""
    m = re.search(r'http-equiv=["\']?refresh["\']?[^>]*url=([^"\'>\s]+)', html, re.I)
    if not m:
        return None
    return urljoin(base_url, m.group(1))


# 澳门海关 CMS 把正文 HTML 写在 <script> 的反引号模板字符串里（页面是壳），
# 因此除了 DOM 容器，还必把模板串里的 HTML 拽出来一起候选。
TEMPLATE_LITERAL = re.compile(r"`([^`]{200,}?)`", re.S)
CJK = re.compile(r"[\u4e00-\u9fff]")
LEGAL_SIGNAL = re.compile(r"。|第[一二三四五六七八九十百\d]+[条條章]")


def _js_template_bodies(html: str) -> List[str]:
    """从 JS 模板字符串里取带正文的 HTML 块；导航菜单（无句号）会被过滤。"""
    out = []
    for m in TEMPLATE_LITERAL.finditer(html):
        frag = m.group(1)
        if "<" not in frag or not CJK.search(frag):
            continue
        sub = BeautifulSoup(frag, "html.parser")
        for sel in NOISE_SELECTORS:
            for el in sub.select(sel):
                el.decompose()
        body = re.sub(r"\n{2,}", "\n",
                      re.sub(r"[ \t\u3000]+", " ", sub.get_text("\n", strip=True))).strip()
        if len(body) >= 80 and LEGAL_SIGNAL.search(body):
            out.append(body)
    return out


def extract_body(html: str) -> Tuple[str, str]:
    """先去全局噪音，再在候选容器与整页中取文本最长者。返回 (标题, 正文)。

    早先按“去噪前的长度”选容器会误选导航块（去噪后只剩十几个字），
    往候选里追加 JS 模板串抽取结果：澳门海关页正文就养在那里。
    """
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""

    for sel in NOISE_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

    def clean(el_text: str) -> str:
        t = re.sub(r"[ \t\u3000]+", " ", el_text)
        return re.sub(r"\n{2,}", "\n", t).strip()

    candidates = []
    for sel in CONTENT_SELECTORS:
        for el in soup.select(sel):
            body = clean(el.get_text("\n", strip=True))
            if body:
                candidates.append((len(body), body, "dom"))
    whole = clean(soup.get_text("\n", strip=True))
    candidates.append((len(whole), whole, "dom"))

    # 溯源护栏：JS 模板块必须包含页面标题（去标点后比对），否则很可能取到了
    # 站点级共享组件 —— 实测澳门海关多个不同子页返回同一块 1448 字文本，
    # 若不拦截就会把 A 页的文字挂到 B 页的 source_ref 上。 DOM 候选不受此限制。
    def norm(s: str) -> str:
        return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s or "")

    core = norm(title)[:6]
    kept_js, dropped_js = 0, 0
    for body in _js_template_bodies(html):
        if core and core in norm(body):
            candidates.append((len(body), body, "js"))
            kept_js += 1
        else:
            dropped_js += 1
    if dropped_js:
        print("    [GUARD] 丢弃 %d 个未包含标题的 JS 块（疑为站点共享组件），采信 %d 个"
              % (dropped_js, kept_js))

    # 带“第X條/第X条”条款标志的块优先，其次纯长度
    def score(item):
        n, _txt, _org = item
        return (1 if re.search(r"第[一二三四五六七八九十百\d]+[条條]", _txt) else 0, n)
    best = max(candidates, key=score)
    return title, best[1]


def _is_code_like(text: str) -> bool:
    """识别漏网的 JS/样式碎片（实测 DOM 兼容回退会把 $('.aNav_... 带进来）。"""
    if re.search(r"\$\(|function\s*\(|=>|console\.|\.addClass|\.css\(|var\s+\w+\s*=|\belse\b|</?script", text):
        return True
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    codeish = len(re.findall(r"[{}();=<>.]", text))
    # 法条文本以中文为主；中文占比过低且符号密度过高的当为代码
    return cjk < 10 and codeish > len(text) * 0.05


def split_articles(text: str) -> List[str]:
    """按条款切块；切不出条款就按空行聚合成 <=800 字的段落块。"""
    parts = [p.strip() for p in ARTICLE_SPLIT.split(text) if p and p.strip()]
    chunks = [p for p in parts if len(p) >= 30 and not _is_code_like(p)]
    if not chunks:
        buf, out = [], 0
        for line in text.split("\n"):
            if not line.strip():
                continue
            buf.append(line)
            out += len(line)
            if out >= 600:
                chunks.append("\n".join(buf).strip())
                buf, out = [], 0
        if buf:
            chunks.append("\n".join(buf).strip())
    return [c for c in chunks if len(c) >= 30 and not _is_code_like(c)]


def infer_category(text: str) -> Optional[str]:
    kw = {"现金": ("現金", "现金", "票據", "票据"), "烟草": ("煙草", "烟", "香煙", "香烟"),
          "动植物产品": ("動植物", "肉類", "肉类", "植物", "检疫"),
          "免税额度": ("免稅", "免税", "豁免"), "申报": ("申報", "申报")}
    for cat, words in kw.items():
        if any(w in text for w in words):
            return cat
    return None


def build_records(source_id: str, meta: Dict[str, Any], url: str, title: str,
                  text: str) -> List[Dict[str, Any]]:
    chunks = split_articles(text)
    if not chunks:
        print("    [WARN] 正文过短或切块失败（%d 字），跳过" % len(text))
        return []
    base = sha(source_id + "|" + url)
    now = datetime.now().isoformat(timespec="seconds")
    out = []
    for i, chunk in enumerate(chunks):
        out.append({
            "id": "%s_c%03d" % (base[:12], i),
            "text": chunk,
            "direction": meta.get("direction", "bidirectional"),
            "item_category": infer_category(chunk),
            "authority": meta.get("authority"),
            "source_ref": "%s | %s" % (title or meta.get("label", source_id), url),
            "url": url,
            "lang": meta.get("lang"),
            "chunk_index": i,
            "text_sha256": sha(chunk),
            "fetched_at": now,
        })
    return out


def process_url(source_id: str, meta: Dict[str, Any], url: str, dry_run: bool) -> List[Dict[str, Any]]:
    print("  [GET] %s" % url)
    try:
        resp = http_get(url)
    except Exception as exc:
        print("    [FAIL] %s: %s" % (type(exc).__name__, str(exc)[:100]))
        if meta.get("known_block"):
            print("    [NOTE] %s" % meta["known_block"])
        return []

    html, final_url = resp.text, resp.url
    target = follow_meta_refresh(html[:4000], final_url)
    if target and not re.search(r"fold-content|cms-layout", html):
        print("    [META-REFRESH] -> %s" % target)
        time.sleep(SLEEP_SECONDS)
        try:
            resp = http_get(target)
            html, final_url = resp.text, resp.url
        except Exception as exc:
            print("    [FAIL] 跳转后抓取失败：%s" % exc)
            return []

    title, body = extract_body(html)
    print("    [OK] title=%s body=%d chars" % ((title or "?")[:40], len(body)))

    if not dry_run:
        os.makedirs(RAW_DIR, exist_ok=True)
        stamp = sha(final_url)[:8]
        with open(os.path.join(RAW_DIR, "%s.html" % stamp), "w", encoding="utf-8") as fh:
            fh.write(html)
        with open(os.path.join(RAW_DIR, "%s.txt" % stamp), "w", encoding="utf-8") as fh:
            fh.write("# %s\n# %s\n\n%s" % (final_url, title, body))

    return build_records(source_id, meta, final_url, title, body)


def load_existing_ids() -> Dict[str, str]:
    """已抓过的语料按 text_sha256 去重（同一页改版会作为新块追加，不覆盖历史）。"""
    seen = {}
    if os.path.exists(JSONL_PATH):
        with open(JSONL_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    seen[rec.get("text_sha256", "")] = rec.get("id", "")
                except json.JSONDecodeError:
                    continue
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取海关法规原文并清洗为 RAG 语料")
    ap.add_argument("--source", help="源标识（见 --list-sources）")
    ap.add_argument("--url", action="append", default=[], help="临时抓取单个 URL（可重复）")
    ap.add_argument("--file", action="append", default=[], help="本地 HTML 文件导入（可重复）")
    ap.add_argument("--source-id", default="adhoc", help="配合 --url/--file 的源标识")
    ap.add_argument("--authority", default=None, help="配合 --url/--file 的发布机构")
    ap.add_argument("--direction", default="bidirectional", help="配合 --url/--file 的通关方向")
    ap.add_argument("--dry-run", action="store_true", help="只解析不写文件")
    ap.add_argument("--list-sources", action="store_true", help="列出已登记的源")
    args = ap.parse_args()

    if args.list_sources:
        for k, v in SOURCES.items():
            print("%-26s %s  direction=%s  urls=%d" % (k, v["label"], v["direction"], len(v["urls"])))
            if v.get("known_block"):
                print("%-26s   ! %s" % ("", v["known_block"]))
        return 0

    if not (args.source or args.url or args.file):
        ap.print_help()
        return 1

    new_records: List[Dict[str, Any]] = []
    seen = load_existing_ids()

    def absorb(recs: List[Dict[str, Any]]) -> None:
        """运行内也要去重：同一块共享正文不得挂到多个页面的 source_ref 上。"""
        added = 0
        for r in recs:
            if r["text_sha256"] in seen:
                continue
            seen[r["text_sha256"]] = r["id"]
            new_records.append(r)
            added += 1
        dropped = len(recs) - added
        if dropped:
            print("    [DEDUP] 跳过跨页重复块 %d 个，新增 %d 个" % (dropped, added))

    if args.source:
        meta = SOURCES.get(args.source)
        if not meta:
            print("[ABORT] 未登记的 source：%s" % args.source)
            return 1
        print("[SOURCE] %s (%s)" % (args.source, meta["label"]))
        for url in meta["urls"]:
            absorb(process_url(args.source, meta, url, args.dry_run))
            time.sleep(SLEEP_SECONDS)

    adhoc_meta = {"label": args.source_id, "authority": args.authority,
                  "direction": args.direction, "lang": "zh"}
    for url in args.url:
        absorb(process_url(args.source_id, adhoc_meta, url, args.dry_run))
        time.sleep(SLEEP_SECONDS)

    for path in args.file:
        p = path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)
        if not os.path.exists(p):
            print("  [FAIL] 文件不存在：%s" % p)
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            html = fh.read()
        title, body = extract_body(html)
        url = "file://" + p.replace("\\", "/")
        print("  [FILE] %s body=%d chars" % (os.path.basename(p), len(body)))
        absorb(build_records(args.source_id, adhoc_meta, url, title, body))

    # 缺 source_ref 的记录一律丢弃（红线：无语料溯源）
    new_records = [r for r in new_records if r.get("source_ref")]

    print("\n[TOTAL] 新语料块 %d 条" % len(new_records))
    if args.dry_run:
        for r in new_records[:5]:
            print("  - [%s] %s ..." % (r["direction"], r["text"][:70]))
        print("[DRY-RUN] 未写出文件")
        return 0
    if not new_records:
        print("[WARN] 没有可写出的语料。若因 412/DNS 被拦，请走 CDP 浏览器通道或另存 HTML 后 --file 导入。")
        return 2

    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    with open(JSONL_PATH, "a", encoding="utf-8") as fh:
        for r in new_records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("[WRITE] 追加至 %s" % JSONL_PATH)
    print("[NEXT]  python tools/load_rules_to_db.py")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
