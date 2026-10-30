from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path.home() / ".campus-paper-fetch"  # 仅用于 DEFAULT_CONFIG 默认字符串；读写路径请用 config_dir()/config_file()

DEFAULT_CONFIG: dict[str, Any] = {
    # 用于 Unpaywall API 的邮箱（建议学校邮箱）
    "email": "",
    # Elsevier Developer Portal 免费申请的 API Key（可选）
    "elsevier_api_key": "",
    # PDF 输出目录
    "output_dir": str(Path.home() / "Downloads" / "campus-papers"),
    # Playwright 持久化浏览器 profile（保存会话 cookie，等同凭证，勿外传）
    "profile_dir": str(DEFAULT_DIR / "browser-profile"),
    # CARSI 机构名（按学校统一身份认证页显示名称填写）
    "idp_name": "华侨大学",
    # 华侨大学 aTrust 零信任门户（校外兜底）
    "atrust_url": "https://atrust.hqu.edu.cn",
    # 浏览器通道：chrome / msedge / chromium
    "browser_channel": "chrome",
    # 浏览器接入模式（推荐 cdp，v0.2.1 起为默认）：
    #   cdp        — 附加到"真人启动"的 Chrome（--remote-debugging-port=9222）。
    #                真实浏览器指纹，规避 Cloudflare 对 Playwright 自动化 Chrome 的
    #                反复人机验证（2026-08-23 实测）。下载前先跑 scripts/start-browser.sh。
    #   persistent — 独立持久化 Chrome profile，由 Playwright 直接拉起（可能触发 CF 指纹识别）。
    "browser_mode": "cdp",
    # 启动浏览器时禁用系统代理（True：校园网直连；本机若开了全局/规则代理且出版社走代理出口被风控时用）
    "browser_no_proxy": False,
    # CDP 调试端口地址（Chrome 需以 --remote-debugging-port=9222 启动）
    "cdp_url": "http://127.0.0.1:9222",
    # 是否显示浏览器窗口（推荐 True：真实浏览器指纹 + 用户可见登录过程）
    "headless": False,
    # 浏览器单次抓取超时（秒）
    "browser_timeout": 90,
    # 机构直连下载间隔（秒），避免限流
    "publisher_delay": 3,
    # OA 并发数
    "oa_workers": 4,
    # 是否做 PDF 校验
    "verify_pdf": True,
    # 校园网直连优先（True 时出版社直连走在机构登录前面）
    "on_campus": True,
    # 排除的出版社（如 MDPI=10.3390），搜索与下载都跳过
    "exclude_publishers": ["mdpi"],
}

LIST_KEYS = {"exclude_publishers"}


def ensure_dir() -> None:
    config_dir().mkdir(parents=True, exist_ok=True)


def config_dir() -> Path:
    """配置目录（惰性计算，HOME 变化时生效，便于测试隔离）。"""
    return Path(os.path.expanduser("~")) / ".campus-paper-fetch"


def config_file() -> Path:
    return config_dir() / "config.json"


def get_config() -> dict[str, Any]:
    ensure_dir()
    cfg = dict(DEFAULT_CONFIG)
    cf = config_file()
    if cf.exists():
        try:
            user = json.loads(cf.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update({k: v for k, v in user.items() if k in DEFAULT_CONFIG})
        except Exception:
            pass
    # 展开 ~
    for key in ("output_dir", "profile_dir"):
        cfg[key] = str(Path(cfg[key]).expanduser())
    return cfg


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    ensure_dir()
    cfg = get_config()
    for k, v in patch.items():
        if k in DEFAULT_CONFIG:
            if k in LIST_KEYS and isinstance(v, str):
                v = [x.strip() for x in v.split(",") if x.strip()]
            cfg[k] = v
    config_file().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def ensure_output_dir(cfg: dict[str, Any]) -> Path:
    p = Path(cfg["output_dir"]).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
