# -*- coding: utf-8 -*-
"""轻量联网搜索：Bing(CN) 主 + 百度兜底，返回标题/链接/摘要，带 TTL 缓存。

不依赖 API Key；任何异常都静默返回 []，绝不影响主流程。
结果仅作「非权威补充线索」，最终判定仍以本地法条库为准。
"""
from __future__ import annotations

import base64
import re
import time
from typing import Dict, List
from urllib.parse import parse_qs, quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
AUTHORITATIVE = ("customs.gov.mo", "gov.mo", "gov.cn", "customs.gov.cn",
                 "mof.gov.cn", "mofcom.gov.cn", "nhc.gov.cn", "samr.gov.cn")
_CACHE: Dict[str, tuple] = {}
_TTL = 1800


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _is_auth(url: str) -> bool:
    d = _domain(url)
    return any(d == a or d.endswith("." + a) for a in AUTHORITATIVE)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _unwrap_bing(href: str) -> str:
    """解出 bing 跳转链接里的真实目标 URL，便于域名判断。"""
    if "bing.com/ck/a" not in href:
        return href
    try:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            raw = u[2:]
            raw += "=" * (-len(raw) % 4)
            target = base64.urlsafe_b64decode(raw).decode("utf-8", "ignore")
            if target.startswith("http"):
                return target
    except Exception:
        pass
    return href


def _so360(query: str, max_results: int, timeout: float) -> List[dict]:
    url = "https://www.so.com/s?q=%s" % quote_plus(query)
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                                   "Referer": "https://www.so.com/"}, timeout=timeout)
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out: List[dict] = []
    for li in soup.select("li.res-list"):
        a = li.select_one("h3.res-title a") or li.select_one("h3 a")
        if not a:
            continue
        href = (a.get("data-mdurl") or a.get("href") or "").strip()
        if not href:
            continue
        title = _clean(a.get_text(" ", strip=True))
        d = (li.select_one(".res-desc") or li.select_one(".res-comm-con")
             or li.select_one("[class*=desc]"))
        snippet = _clean(d.get_text(" ", strip=True)) if d else ""
        cite = li.select_one("cite")
        domain = _domain(href) or (_clean(cite.get_text(" ", strip=True)).split()[0]
                                   if cite else "")
        out.append({"title": title, "url": href, "snippet": snippet,
                    "domain": domain, "authoritative": _is_auth(href)})
        if len(out) >= max_results:
            break
    return out


def _bing(query: str, max_results: int, timeout: float) -> List[dict]:
    url = "https://cn.bing.com/search?q=%s&setlang=zh-Hans&count=%d" % (
        quote_plus(query), max_results)
    r = requests.get(url, headers={"User-Agent": UA,
                                   "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=timeout)
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out: List[dict] = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        href = _unwrap_bing(a["href"])
        title = _clean(a.get_text(" ", strip=True))
        p = li.select_one(".b_caption p") or li.select_one("p")
        snippet = _clean(p.get_text(" ", strip=True)) if p else ""
        out.append({"title": title, "url": href, "snippet": snippet,
                    "domain": _domain(href), "authoritative": _is_auth(href)})
        if len(out) >= max_results:
            break
    return out


def _baidu(query: str, max_results: int, timeout: float) -> List[dict]:
    url = "https://www.baidu.com/s?wd=%s&rn=%d" % (quote_plus(query), max_results)
    r = requests.get(url, headers={"User-Agent": UA,
                                   "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=timeout)
    if r.status_code != 200:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out: List[dict] = []
    for div in soup.select("div.result, div.result-op"):
        a = div.select_one("h3 a")
        if not a or not a.get("href"):
            continue
        # 百度结果 div 的 mu 属性是真实目标 URL；否则回退跳转链接
        href = (div.get("mu") or a["href"]).strip()
        if href.startswith("/"):
            href = "https://www.baidu.com" + href
        title = _clean(a.get_text(" ", strip=True))
        ab = (div.select_one(".c-abstract") or div.select_one("[class*=content-right]")
              or div.select_one("[class*=summary]"))
        snippet = _clean(ab.get_text(" ", strip=True)) if ab else _clean(div.get_text(" ", strip=True))[:180]
        out.append({"title": title, "url": href, "snippet": snippet,
                    "domain": _domain(href), "authoritative": _is_auth(href)})
        if len(out) >= max_results:
            break
    return out


def search_web(query: str, max_results: int = 5, timeout: float = 8.0) -> List[dict]:
    """返回 [{title,url,snippet,domain,authoritative}]；失败返回 []。"""
    query = _clean(query)
    if not query:
        return []
    key = "%s|%s" % (query, max_results)
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]

    results: List[dict] = []
    for fn in (_so360, _bing, _baidu):
        try:
            results = fn(query, max_results, timeout)
        except Exception:
            results = []
        if results:
            break
    results = [r for r in results if r.get("url")]
    results.sort(key=lambda x: 0 if x.get("authoritative") else 1)
    _CACHE[key] = (now, results)
    return results


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "澳门海关 携带 水果 限量"
    for r in search_web(q, 5):
        print(("[权威] " if r["authoritative"] else "      ") + r["title"][:60])
        print("       ", r["url"])
        print("       ", r["snippet"][:100])
