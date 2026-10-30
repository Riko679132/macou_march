from __future__ import annotations

from pathlib import Path
from typing import Any


def is_pdf_bytes(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def is_pdf_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def verify_pdf(path: Path, min_size: int = 10240) -> dict[str, Any]:
    """校验 PDF：文件头、大小、页数。返回 {ok, size, pages, error}。"""
    result: dict[str, Any] = {"ok": False, "size": 0, "pages": None, "error": None}
    try:
        size = path.stat().st_size
        result["size"] = size
        if not is_pdf_file(path):
            result["error"] = "not_pdf_header"
            return result
        if size < min_size:
            result["error"] = "too_small"
            return result
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path), strict=False)
            result["pages"] = len(reader.pages)
        except Exception as e:
            result["error"] = f"page_read_failed: {e}"
            result["pages"] = None
        result["ok"] = True
        return result
    except OSError as e:
        result["error"] = str(e)
        return result
