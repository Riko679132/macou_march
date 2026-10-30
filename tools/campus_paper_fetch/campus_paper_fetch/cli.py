from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .bootstrap import default_scripts_dir, ensure_default_config, write_browser_scripts
from .browser import close_browser, login_atrust, login_carsi, login_status
from .config import ensure_output_dir, get_config, save_config
from .download import batch_download, download_one
from .resolve import resolve
from .search import openalex_search, search_and_download


def _print(obj: Any, json_lines: bool = False) -> None:
    if json_lines:
        print(json.dumps(obj, ensure_ascii=False, default=str))
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_config(args: argparse.Namespace) -> None:
    if args.op == "set" and args.key:
        save_config({args.key: args.value})
        print(f"config.{args.key} = {args.value}")
        return
    cfg = get_config()
    if args.op == "get" and args.key:
        print(json.dumps({k: cfg[k] for k in args.key.split(",") if k in cfg}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(cfg, ensure_ascii=False, indent=2))


def cmd_setup(args: argparse.Namespace) -> None:
    """一键初始化（PyPI 安装后自举，无需 git/GitHub）：
    写默认配置（browser_mode=cdp / on_campus=True）+ 生成浏览器启动脚本。"""
    cfg = ensure_default_config()
    print("配置就绪：")
    for k in ("email", "browser_mode", "on_campus", "output_dir"):
        if k in cfg:
            print(f"  {k} = {cfg[k]}")
    if not cfg.get("email"):
        print("  （提示：可设 CPF_EMAIL 环境变量后重跑，或 config set email 补上 Unpaywall 邮箱）")

    dest = default_scripts_dir()
    wrote = write_browser_scripts(dest)
    print("启动脚本已生成：")
    for w in wrote:
        print(f"  {w}")
    print("下一步：")
    if any(str(w).endswith(".sh") for w in wrote):
        print(f"  启动浏览器: {dest / 'start-browser.sh'}   （Windows 用 start-browser.ps1）")
    print("  下载论文:   uvx campus-paper-fetch download <DOI>")
    print("  批量下载:   uvx campus-paper-fetch batch doi_list.txt")


def cmd_resolve(args: argparse.Namespace) -> None:
    for ident in args.identifiers:
        _print(resolve(ident), args.json_lines)


def cmd_download(args: argparse.Namespace) -> None:
    cfg = get_config()
    out = ensure_output_dir(cfg) if not args.output_dir else Path(args.output_dir)
    results = asyncio.run(
        batch_download(
            args.identifiers,
            cfg=cfg,
            oa_only=args.oa_only,
            output_dir=out,
        )
    )
    for r in results:
        _print(r, args.json_lines)
    if not args.json_lines:
        ok = sum(1 for r in results if r.get("status") == "ok")
        print(f"\n成功 {ok}/{len(results)}")


def cmd_batch(args: argparse.Namespace) -> None:
    lines = [ln.strip() for ln in Path(args.file).read_text(encoding="utf-8").splitlines() if ln.strip()]
    args.identifiers = lines
    cmd_download(args)


def cmd_search(args: argparse.Namespace) -> None:
    hits = openalex_search(args.query, limit=args.limit)
    if args.download:
        results = asyncio.run(search_and_download(args.query, limit=args.limit, oa_only=args.oa_only, output_dir=args.output_dir))
        for r in results:
            _print(r, args.json_lines)
        return
    _print(hits, args.json_lines)


def cmd_status(args: argparse.Namespace) -> None:
    cfg = get_config()
    st = asyncio.run(login_status())
    _print({"on_campus": cfg.get("on_campus"), "output_dir": cfg.get("output_dir"), "sessions": st}, args.json_lines)


def cmd_login_carsi(args: argparse.Namespace) -> None:
    result = asyncio.run(login_carsi(args.publisher, args.idp))
    _print(result, args.json_lines)
    input("\n请在浏览器中完成统一身份认证，完成后按回车确认…")
    st = asyncio.run(login_status())
    _print(st, args.json_lines)


def cmd_login_atrust(args: argparse.Namespace) -> None:
    result = asyncio.run(login_atrust(args.url))
    _print(result, args.json_lines)
    input("\n请在浏览器中完成登录，完成后按回车确认…")
    st = asyncio.run(login_status())
    _print(st, args.json_lines)


def main() -> None:
    p = argparse.ArgumentParser(prog="campus-paper-fetch", description="校园网论文全文自动搜索与下载")
    p.add_argument("--version", action="version", version=f"campus-paper-fetch {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("config", help="查看/设置配置")
    pc.add_argument("op", nargs="?", choices=["get", "set"], help="get / set")
    pc.add_argument("key", nargs="?", help="配置项 key")
    pc.add_argument("value", nargs="?", help="配置项值")
    pc.set_defaults(func=cmd_config)

    ps_ = sub.add_parser("setup", help="一键初始化：写默认配置 + 生成启动脚本（PyPI 自举）")
    ps_.set_defaults(func=cmd_setup)

    pr = sub.add_parser("resolve", help="解析 DOI 元数据")
    pr.add_argument("identifiers", nargs="+")
    pr.add_argument("--json-lines", action="store_true")
    pr.set_defaults(func=cmd_resolve)

    pd = sub.add_parser("download", help="下载论文")
    pd.add_argument("identifiers", nargs="+")
    pd.add_argument("--output-dir")
    pd.add_argument("--oa-only", action="store_true", help="只走 OA，不碰浏览器")
    pd.add_argument("--json-lines", action="store_true")
    pd.set_defaults(func=cmd_download)

    pb = sub.add_parser("batch", help="批量下载（每行一个 DOI/链接）")
    pb.add_argument("file")
    pb.add_argument("--output-dir")
    pb.add_argument("--oa-only", action="store_true")
    pb.add_argument("--json-lines", action="store_true")
    pb.set_defaults(func=cmd_batch)

    ps = sub.add_parser("search", help="OpenAlex 关键词搜索")
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=10)
    ps.add_argument("--download", action="store_true")
    ps.add_argument("--oa-only", action="store_true")
    ps.add_argument("--output-dir")
    ps.add_argument("--json-lines", action="store_true")
    ps.set_defaults(func=cmd_search)

    pl = sub.add_parser("login-carsi", help="CARSI 机构登录（校外兜底）")
    pl.add_argument("--publisher", default="springer", choices=["springer", "wiley", "elsevier", "ieee", "tandf", "nature", "asce", "acs", "rsc"])
    pl.add_argument("--idp", help="机构名称，默认华侨大学")
    pl.add_argument("--json-lines", action="store_true")
    pl.set_defaults(func=cmd_login_carsi)

    pa = sub.add_parser("login-atrust", help="aTrust 零信任门户登录（校外兜底）")
    pa.add_argument("--url", help="门户地址，默认 https://atrust.hqu.edu.cn")
    pa.add_argument("--json-lines", action="store_true")
    pa.set_defaults(func=cmd_login_atrust)

    pst = sub.add_parser("status", help="会话与配置状态")
    pst.add_argument("--json-lines", action="store_true")
    pst.set_defaults(func=cmd_status)

    args = p.parse_args()
    try:
        args.func(args)
    finally:
        asyncio.run(close_browser())


if __name__ == "__main__":
    main()
