"""Layout helpers: reading order (XY-cut) and small geometry utilities."""
from __future__ import annotations

from typing import Iterable, Sequence

BIN = 2.0  # coverage bin width in points


def _sorted_xy(items: list) -> list:
    return sorted(items, key=lambda it: (round(it.bbox[1], 1), round(it.bbox[0], 1)))


def _content_box(items: list) -> tuple[float, float, float, float]:
    return (
        min(it.bbox[0] for it in items),
        min(it.bbox[1] for it in items),
        max(it.bbox[2] for it in items),
        max(it.bbox[3] for it in items),
    )


def _gutters(items: list, bin_w: float = BIN) -> list[tuple[float, float]]:
    """Empty vertical bands that could separate columns.

    Coverage is "how much page height is occupied at this x", so a centred
    equation crossing the gutter (only a few points of height) does not
    destroy column detection.
    """
    if not items:
        return []
    x0, y0, x1, y1 = _content_box(items)
    width = x1 - x0
    if width < 40:
        return []
    n = int(width / bin_w) + 1
    cover = [0.0] * n
    for it in items:
        h = max(1.0, it.bbox[3] - it.bbox[1])
        lo = int((it.bbox[0] - x0) / bin_w)
        hi = int((it.bbox[2] - x0) / bin_w)
        for b in range(max(0, lo), min(n, hi + 1)):
            cover[b] += h
    total_h = max(1.0, y1 - y0)
    threshold = 0.14 * total_h

    runs: list[tuple[int, int]] = []
    start = None
    for b, c in enumerate(cover):
        if c < threshold:
            if start is None:
                start = b
        else:
            if start is not None:
                runs.append((start, b))
                start = None
    if start is not None:
        runs.append((start, n))

    out: list[tuple[float, float]] = []
    for a, b in runs:
        if b - a < 3:  # narrower than ~6pt: not a gutter
            continue
        gx0 = x0 + a * bin_w
        gx1 = x0 + b * bin_w
        left = [it for it in items if it.bbox[2] <= gx0 + 1]
        right = [it for it in items if it.bbox[0] >= gx1 - 1]
        if len(left) >= 2 and len(right) >= 2:
            out.append((gx0, gx1))
    return out


def _row_gaps(items: list) -> list[tuple[float, float]]:
    """Horizontal empty bands that separate rows of blocks."""
    if len(items) < 2:
        return []
    ordered = sorted(items, key=lambda it: it.bbox[1])
    heights = sorted(it.bbox[3] - it.bbox[1] for it in ordered)
    min_gap = max(6.0, heights[len(heights) // 2] * 0.6)
    out: list[tuple[float, float]] = []
    reach = ordered[0].bbox[3]
    for it in ordered[1:]:
        if it.bbox[1] - reach >= min_gap:
            out.append((reach, it.bbox[1]))
        reach = max(reach, it.bbox[3])
    return out


def _split_rows(items: list, gaps: list[tuple[float, float]]) -> list[list]:
    cuts = sorted(g[0] for g in gaps)
    bands: list[list] = []
    current: list = []
    for it in sorted(items, key=lambda x: x.bbox[1]):
        while cuts and it.bbox[1] > cuts[0] + 0.5:
            if current:
                bands.append(current)
                current = []
            cuts.pop(0)
        current.append(it)
    if current:
        bands.append(current)
    return bands


def xy_cut(items: list, depth: int = 0) -> list:
    """Order elements the way a reader would traverse them.

    Priority: full width separators -> column gutters -> row bands -> raw sort.
    """
    if len(items) <= 1 or depth > 14:
        return _sorted_xy(items)

    x0, y0, x1, y1 = _content_box(items)
    content_w = max(1.0, x1 - x0)
    gutters = _gutters(items)

    # 1. full width blocks (headings, wide paragraphs, full width figures)
    separators = [
        it
        for it in items
        if (it.bbox[2] - it.bbox[0]) > content_w * 0.72
        and (not gutters or any(it.bbox[0] < g[0] and it.bbox[2] > g[1] for g in gutters))
    ]
    if depth == 0 and separators and len(separators) < len(items):
        seps = sorted(separators, key=lambda it: it.bbox[1])
        out: list = []
        pool = [it for it in items if it not in seps]
        cursor = y0
        for sep in seps:
            band = [it for it in pool if cursor <= it.bbox[1] < sep.bbox[1] - 0.5]
            if band:
                out.extend(xy_cut(band, depth + 1))
            out.append(sep)
            pool = [it for it in pool if it.bbox[1] >= sep.bbox[1] - 0.5]
            cursor = sep.bbox[1]
        if pool:
            out.extend(xy_cut(pool, depth + 1))
        if len(out) == len(items):
            return out

    # 2. columns
    if gutters:
        gx0, gx1 = max(gutters, key=lambda g: g[1] - g[0])
        gmid = (gx0 + gx1) / 2
        middle = [
            it for it in items if it.bbox[0] < gx0 - 1 and it.bbox[2] > gx1 + 1
        ]
        left, right = [], []
        for it in items:
            if it in middle:
                continue
            cx = (it.bbox[0] + it.bbox[2]) / 2
            (left if cx <= gmid else right).append(it)
        out = []
        if left:
            out.extend(xy_cut(left, depth + 1))
        if middle:
            out.extend(xy_cut(middle, depth + 1))
        if right:
            out.extend(xy_cut(right, depth + 1))
        if len(out) == len(items):
            return out

    # 3. rows
    gaps = _row_gaps(items)
    if gaps:
        bands = _split_rows(items, gaps)
        if len(bands) > 1:
            out = []
            for band in bands:
                out.extend(xy_cut(band, depth + 1))
            if len(out) == len(items):
                return out

    return _sorted_xy(items)


def union(boxes: Iterable[tuple[float, float, float, float]]):
    boxes = list(boxes)
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def overlaps(a, b, tol: float = 1.0) -> bool:
    return not (
        a[2] <= b[0] + tol or b[2] <= a[0] + tol or a[3] <= b[1] + tol or b[3] <= a[1] + tol
    )
