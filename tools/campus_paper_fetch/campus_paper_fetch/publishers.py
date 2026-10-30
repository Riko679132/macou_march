from __future__ import annotations

import re
from typing import Any

# DOI 前缀 → 出版社
PREFIX_MAP = {
    "10.1016": "elsevier",
    "10.1007": "springer",
    "10.1109": "ieee",
    "10.1061": "asce",
    "10.1002": "wiley",
    "10.1080": "tandf",
    "10.3390": "mdpi",
    "10.1038": "nature",
    "10.1021": "acs",
    "10.1039": "rsc",
    "10.1063": "aip",
    "10.1115": "asme",
    "10.1145": "acm",
    "10.1108": "emerald",
    "10.1177": "sage",
    "10.1680": "ice",
    "10.14359": "aci",
}

PUBLISHER_NAMES = {
    "elsevier": "ScienceDirect/Elsevier",
    "springer": "Springer",
    "ieee": "IEEE Xplore",
    "asce": "ASCE Library",
    "wiley": "Wiley Online Library",
    "tandf": "Taylor & Francis",
    "mdpi": "MDPI",
    "nature": "Nature",
    "acs": "ACS",
    "rsc": "RSC",
    "aip": "AIP",
    "asme": "ASME",
    "acm": "ACM",
    "emerald": "Emerald",
    "sage": "SAGE",
    "ice": "ICE Virtual Library",
    "aci": "ACI",
}


def detect_publisher(doi: str) -> str | None:
    if not doi:
        return None
    d = doi.lower().strip()
    prefix = d.split("/")[0].lower()
    for p, name in PREFIX_MAP.items():
        if prefix.startswith(p):
            return name
    return None


def _extract_ieee_arnumber(meta: dict[str, Any]) -> str | None:
    for url in meta.get("links", []):
        m = re.search(r"/(?:document|arnumber)/(\d+)", url)
        if m:
            return m.group(1)
    lp = meta.get("landing_page") or ""
    m = re.search(r"/(?:document|arnumber)/(\d+)", lp)
    return m.group(1) if m else None


def _extract_elsevier_pii(meta: dict[str, Any]) -> str | None:
    patterns = [r"/pii/(S\d+[A-Za-z0-9]+)", r"[?&]PII[:=](S\d+[A-Za-z0-9]+)", r"PII:?(S\d+[A-Za-z0-9]+)"]
    for url in meta.get("links", []):
        for pat in patterns:
            m = re.search(pat, url, re.IGNORECASE)
            if m:
                return m.group(1)
    lp = meta.get("landing_page") or ""
    for pat in patterns:
        m = re.search(pat, lp, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _extract_nature_id(meta: dict[str, Any]) -> str | None:
    for url in meta.get("links", []):
        m = re.search(r"/articles/([A-Za-z0-9\-]+)", url)
        if m:
            return m.group(1)
    lp = meta.get("landing_page") or ""
    m = re.search(r"/articles/([A-Za-z0-9\-]+)", lp)
    return m.group(1) if m else None


def direct_pdf_urls(doi: str, publisher: str | None, meta: dict[str, Any]) -> list[tuple[str, str]]:
    """返回 [(source, url)] 候选直连 PDF 路由。"""
    urls: list[tuple[str, str]] = []
    if not publisher:
        return urls
    if publisher == "springer":
        urls.append(("springer_direct", f"https://link.springer.com/content/pdf/{doi}.pdf"))
    elif publisher == "wiley":
        urls.append(("wiley_direct", f"https://onlinelibrary.wiley.com/doi/pdf/{doi}"))
    elif publisher == "tandf":
        urls.append(("tandf_direct", f"https://www.tandfonline.com/doi/pdf/{doi}"))
    elif publisher == "ieee":
        ar = _extract_ieee_arnumber(meta)
        if ar:
            urls.append(("ieee_direct", f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={ar}"))
    elif publisher == "elsevier":
        pii = _extract_elsevier_pii(meta)
        if pii:
            urls.append(
                (
                    "sciencedirect_direct",
                    f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft?isDTMRedir=true&download=true",
                )
            )
    elif publisher == "asce":
        urls.append(("asce_direct", f"https://ascelibrary.org/doi/pdf/{doi}"))
    elif publisher == "nature":
        aid = _extract_nature_id(meta)
        if aid:
            urls.append(("nature_direct", f"https://www.nature.com/articles/{aid}.pdf"))
    elif publisher == "acs":
        urls.append(("acs_direct", f"https://pubs.acs.org/doi/pdf/{doi}"))
    elif publisher == "rsc":
        urls.append(("rsc_direct", f"https://pubs.rsc.org/en/content/articlepdf/{doi}"))
    elif publisher == "aip":
        urls.append(("aip_direct", f"https://pubs.aip.org/doi/pdf/{doi}"))
    elif publisher == "ice":
        urls.append(("ice_direct", f"https://www.icevirtuallibrary.com/doi/pdf/{doi}"))
    elif publisher == "asme":
        urls.append(("asme_direct", f"https://asmedigitalcollection.asme.org/doi/pdf/{doi}"))
    elif publisher == "emerald":
        urls.append(("emerald_direct", f"https://www.emerald.com/insight/content/doi/{doi}/full/pdf"))
    return urls


def login_start_urls() -> dict[str, str]:
    return {
        "springer": "https://fsso.springer.com/",
        "wiley": "https://onlinelibrary.wiley.com/action/ssostart?redirectOnSSL=true",
        "elsevier": "https://www.sciencedirect.com/user/institution/login",
        "ieee": "https://ieeexplore.ieee.org/",
        "tandf": "https://www.tandfonline.com/",
        "nature": "https://www.nature.com/",
        "asce": "https://ascelibrary.org/",
        "acs": "https://pubs.acs.org/",
        "rsc": "https://pubs.rsc.org/",
        "mdpi": "https://www.mdpi.com/",
    }
