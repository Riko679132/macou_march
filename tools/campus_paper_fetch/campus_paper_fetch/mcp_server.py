from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .browser import login_atrust, login_carsi, login_status
from .config import LIST_KEYS, get_config, save_config
from .download import batch_download, download_one
from .resolve import resolve
from .search import openalex_search, search_and_download

mcp = FastMCP("campus-paper-fetch")


@mcp.tool()
async def resolve_doi(identifier: str) -> dict[str, Any]:
    """解析 DOI/论文链接，返回标题、出版社、OA 链接等元数据。"""
    return resolve(identifier)


@mcp.tool()
async def download(identifier: str, output_dir: str | None = None) -> dict[str, Any]:
    """下载一篇论文全文（OA → Elsevier API → 出版社直连，含 PDF 校验）。"""
    return await download_one(identifier, output_dir=output_dir)


@mcp.tool()
async def batch_download_dois(identifiers: list[str], oa_only: bool = False, output_dir: str | None = None) -> list[dict[str, Any]]:
    """批量下载论文（OA 并发，机构直连串行）。identifiers 可为 DOI 或论文链接列表。"""
    return await batch_download(identifiers, oa_only=oa_only, output_dir=output_dir)


@mcp.tool()
async def search_papers(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """按关键词搜索论文（OpenAlex），返回标题/DOI/年份/被引等。"""
    return openalex_search(query, limit=limit)


@mcp.tool()
async def search_and_download_papers(query: str, limit: int = 5, oa_only: bool = True, output_dir: str | None = None) -> list[dict[str, Any]]:
    """搜索并按相关性下载前 N 篇（oa_only=True 只走 OA，不碰浏览器）。"""
    return await search_and_download(query, limit=limit, oa_only=oa_only, output_dir=output_dir)


@mcp.tool()
async def campus_login_carsi(publisher: str, idp_name: str | None = None) -> dict[str, Any]:
    """打开出版社 CARSI 机构登录页并选中学校，等待用户在浏览器完成统一身份认证。"""
    return await login_carsi(publisher, idp_name)


@mcp.tool()
async def campus_login_atrust(url: str | None = None) -> dict[str, Any]:
    """打开华侨大学 aTrust 零信任门户，等待用户登录（校外兜底）。"""
    return await login_atrust(url)


@mcp.tool()
async def login_status_check() -> dict[str, Any]:
    """检查各出版社域名会话 cookie 状态。"""
    return {"sessions": await login_status(), "config": get_config()}


@mcp.tool()
async def config_get(key: str | None = None) -> dict[str, Any]:
    """查看配置（省略 key 返回全部）。"""
    cfg = get_config()
    if key:
        return {key: cfg.get(key)}
    return cfg


@mcp.tool()
async def config_set(key: str, value: str) -> dict[str, Any]:
    """修改配置项（如 email / elsevier_api_key / output_dir / on_campus）。"""
    cfg = get_config()
    if key not in cfg:
        return {"error": f"unknown_key:{key}", "valid_keys": list(cfg.keys())}
    # 简单类型转换
    if key in LIST_KEYS:
        v = [x.strip() for x in str(value).split(",") if x.strip()]
    elif isinstance(cfg[key], bool):
        v = str(value).lower() in ("1", "true", "yes", "on")
    elif isinstance(cfg[key], int):
        v = int(value)
    else:
        v = str(value)
    save_config({key: v})
    return {key: v}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
