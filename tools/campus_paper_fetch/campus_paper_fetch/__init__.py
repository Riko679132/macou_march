"""campus-paper-fetch: 校园网论文全文自动搜索与下载。"""

try:
    from importlib.metadata import version as _dist_version

    __version__ = _dist_version("campus-paper-fetch")
except Exception:  # 未安装（源码直跑）时回退到 pyproject 当前版本
    __version__ = "0.2.3"
