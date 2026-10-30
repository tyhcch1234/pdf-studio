"""PDF conversion pipeline: PDF -> editable DOCX (text + images + equations)."""
from __future__ import annotations

import os
from typing import Callable

import pymupdf

from . import formula, ocr
from .extract import ConvertOptions, extract_page
from .writer import write_docx

__all__ = ["convert_pdf", "capabilities", "ConvertOptions"]


def capabilities() -> dict:
    return {
        "ocr": ocr.available(),
        "ocr_error": ocr.engine_error(),
        "formula": formula.available(),
        "formula_error": formula.engine_error(),
    }


def convert_pdf(
    src: str,
    dst: str,
    opts: ConvertOptions | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    title: str = "",
) -> dict:
    opts = opts or ConvertOptions()
    doc = pymupdf.open(src)
    try:
        if doc.needs_pass and not doc.authenticate(""):
            raise RuntimeError("PDF is password protected")
        pages = []
        total = doc.page_count
        for i in range(total):
            if progress:
                progress(i, total, f"處理第 {i + 1}/{total} 頁")
            page = doc.load_page(i)
            pages.append(extract_page(page, i, opts))
        if not pages:
            raise RuntimeError("PDF has no pages")
        stats = write_docx(pages, dst, title=title or os.path.basename(src))
        stats["size"] = os.path.getsize(dst)
        return stats
    finally:
        doc.close()
