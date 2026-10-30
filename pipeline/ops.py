"""Page level operations: merge and split A3 sheets into A4 pages.

Uses PyMuPDF only, so the result stays vector (no rasterising) and works
offline on a laptop.
"""
from __future__ import annotations

import os

import pymupdf

A3 = (841.89, 1190.55)
TOL = 9.0


def is_a3(width: float, height: float) -> bool:
    return (abs(width - A3[0]) < TOL and abs(height - A3[1]) < TOL) or (
        abs(width - A3[1]) < TOL and abs(height - A3[0]) < TOL
    )


def parse_pages(text: str, count: int) -> list[int] | None:
    """'1-3,5' -> [0,1,2,4]; empty input means "all pages"."""
    text = (text or "").strip()
    if not text:
        return None
    out: list[int] = []
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            lo, hi = max(1, min(lo, hi)), min(count, max(lo, hi))
            out.extend(range(lo - 1, hi))
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= count:
                out.append(n - 1)
    return sorted(set(out)) or None


def merge_pdfs(paths: list[str], out_path: str, ranges: list[str] | None = None) -> dict:
    """Merge PDFs in order; ``ranges`` optionally limits pages per file."""
    out = pymupdf.open()
    used: list[tuple[str, int]] = []
    try:
        for i, path in enumerate(paths):
            src = pymupdf.open(path)
            try:
                if src.needs_pass and not src.authenticate(""):
                    raise RuntimeError(f"{os.path.basename(path)} 有密碼保護")
                wanted = parse_pages(ranges[i] if ranges else "", src.page_count)
                before = out.page_count
                if wanted is None:
                    out.insert_pdf(src)
                else:
                    for page_no in wanted:
                        out.insert_pdf(src, from_page=page_no, to_page=page_no)
                used.append((path, out.page_count - before))
            finally:
                src.close()
        if out.page_count == 0:
            raise RuntimeError("沒有任何頁面可合併")
        out.set_metadata({})
        out.save(out_path, garbage=3, deflate=True)
    finally:
        out.close()
    return {"pages": sum(n for _, n in used), "files": used, "out": out_path}


def _visual_to_page_rect(page, vx0, vy0, vx1, vy1) -> pymupdf.Rect:
    """Map a rectangle given in *displayed* coordinates to cropbox coordinates.

    PyMuPDF's cropbox uses the same top-left origin as ``page.rect`` for
    unrotated pages, so no vertical flip is needed there; rotated pages need
    the axes swapped back.
    """
    box = page.mediabox
    W, H = box.width, box.height
    rot = page.rotation % 360
    if rot == 0:
        return pymupdf.Rect(vx0, vy0, vx1, vy1)
    if rot == 180:
        return pymupdf.Rect(W - vx1, H - vy1, W - vx0, H - vy0)
    if rot == 90:
        return pymupdf.Rect(vy0, H - vx1, vy1, H - vx0)
    if rot == 270:
        return pymupdf.Rect(W - vy1, vx0, W - vy0, vx1)
    return pymupdf.Rect(vx0, vy0, vx1, vy1)


def split_a3(
    src_path: str,
    out_path: str,
    axis: str = "auto",
    order: str = "normal",
    only_a3: bool = True,
) -> dict:
    """Cut each page down the middle into two pages.

    ``axis``  auto | vertical (left/right) | horizontal (top/bottom)
    ``order`` normal (left→right / top→bottom) | reverse
    """
    src = pymupdf.open(src_path)
    out = pymupdf.open()
    produced = 0
    kept = 0
    pages_in = 0
    try:
        if src.needs_pass and not src.authenticate(""):
            raise RuntimeError("PDF 有密碼保護")
        pages_in = src.page_count
        for i in range(src.page_count):
            page = src.load_page(i)
            rect = page.rect  # already accounts for rotation
            VW, VH = rect.width, rect.height
            a3 = is_a3(VW, VH)
            direction = axis
            if direction == "auto":
                direction = "vertical" if VW >= VH else "horizontal"
            do_split = a3 if only_a3 else True

            if not do_split:
                out.insert_pdf(src, from_page=i, to_page=i)
                kept += 1
                continue

            halves = (
                [(0, 0, VW / 2, VH), (VW / 2, 0, VW, VH)]
                if direction == "vertical"
                else [(0, 0, VW, VH / 2), (0, VH / 2, VW, VH)]
            )
            if order == "reverse":
                halves.reverse()

            for (vx0, vy0, vx1, vy1) in halves:
                out.insert_pdf(src, from_page=i, to_page=i)
                target = out[-1]
                target.set_cropbox(_visual_to_page_rect(target, vx0, vy0, vx1, vy1))
                target.set_rotation(0)
                produced += 1

        if out.page_count == 0:
            raise RuntimeError("沒有可輸出的頁面")
        out.save(out_path, garbage=3, deflate=True)
    finally:
        src.close()
        out.close()
    return {"pages_in": pages_in, "produced": produced, "kept": kept, "out": out_path}
