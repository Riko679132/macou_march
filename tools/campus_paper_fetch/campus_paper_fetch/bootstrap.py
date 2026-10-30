"""PyPI 自举：写默认配置 + 生成浏览器启动脚本（无需 git / GitHub 凭证）。

供 `campus-paper-fetch setup` 子命令与 `scripts/setup.sh` / `setup.ps1` 调用。
"""
from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path

SCRIPT_NAMES = ("start-browser.sh", "start-browser.ps1")


def default_scripts_dir() -> Path:
    """本机脚本输出目录（与仓库 scripts/ 对齐）。"""
    return Path.home() / "campus-paper-fetch" / "scripts"


def write_browser_scripts(dest_dir: Path | None = None) -> list[Path]:
    """把随包分发的浏览器启动脚本写到 dest_dir（默认 ~/campus-paper-fetch/scripts/）。"""
    dest = dest_dir or default_scripts_dir()
    dest.mkdir(parents=True, exist_ok=True)
    data = files("campus_paper_fetch").joinpath("data/scripts")
    wrote: list[Path] = []
    for name in SCRIPT_NAMES:
        try:
            src = data.joinpath(name)
            if not src.is_file():
                continue
            content = src.read_text(encoding="utf-8")
            out = dest / name
            out.write_text(content, encoding="utf-8")
            if name.endswith(".sh"):
                out.chmod(0o755)
            wrote.append(out)
        except Exception:
            pass
    return wrote


def ensure_default_config() -> dict:
    """写默认配置：browser_mode=cdp、on_campus=True；CPF_EMAIL / CPF_ELSEVIER_KEY 环境变量可选填写。"""
    from .config import get_config, save_config

    cfg = get_config()
    changes: dict = {}
    email = os.environ.get("CPF_EMAIL") or ""
    api_key = os.environ.get("CPF_ELSEVIER_KEY") or ""
    if not cfg.get("email") and email:
        changes["email"] = email
    if not cfg.get("elsevier_api_key") and api_key:
        changes["elsevier_api_key"] = api_key
    if cfg.get("browser_mode") != "cdp":
        changes["browser_mode"] = "cdp"
    if changes:
        save_config(changes)
    return get_config()
