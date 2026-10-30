#!/usr/bin/env python3
"""PDF Studio - 單一檔案版本（不需要網頁、不需要伺服器）。

  1. PDF -> Word：文字可編輯、圖片保留、數式轉成 Word 方程式
  2. 合併 PDF
  3. A3 沿中線切成 A4

用法：
  python3 pdf_studio.py               開視窗
  python3 pdf_studio.py a.pdf b.pdf   直接把同資料夾的 PDF 轉成 Word
  python3 pdf_studio.py --check       只檢查環境

第一次使用先安裝：
  pip install pymupdf python-docx numpy pillow rapidocr-onnxruntime onnxruntime
  pip install torch --index-url https://download.pytorch.org/whl/cpu
  pip install pix2tex latex2mathml mathml2omml
"""

import types as _types

# ==== layout.py =================================================
"""Layout helpers: reading order (XY-cut) and small geometry utilities."""

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

# ==== omml.py ===================================================
"""LaTeX -> OMML (Office Math Markup Language) conversion.

Produces real Word equation objects (`m:oMath`) so the result stays fully
editable inside Word / LibreOffice instead of being a static picture.
"""

import re

NS_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_WS = re.compile(r"\s+")

def clean_latex(latex: str) -> str:
    """Normalise the noisy output that formula OCR models usually produce."""
    if not latex:
        return ""
    s = latex.strip()
    # strip math delimiters
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            s = s[len(a) : len(s) - len(b)]
    s = s.strip()
    s = s.replace("\\displaystyle", "").replace("\\limits", "")
    # OCR models love to emit stray spacing macros
    s = re.sub(r"\\(?:left|right)\s*\.", "", s)
    s = re.sub(r"\\(?:quad|qquad|,|;|:|!)", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    # unsupported environments -> plain rows
    s = re.sub(r"\\begin\{(?:array|align\*?|gathered|split)\}(\{[^}]*\})?", "", s)
    s = re.sub(r"\\end\{(?:array|align\*?|gathered|split)\}", "", s)
    s = s.replace("\\\\", " \\\\ ")
    return s.strip()

def _wrap_namespaces(omml: str) -> str:
    """`mathml2omml` emits unqualified prefixes; declare them locally."""
    decls = f' xmlns:m="{NS_M}" xmlns:w="{NS_W}"'
    m = re.match(r"<(m:oMathPara|m:oMath)\b", omml)
    if not m:
        return ""
    head = m.group(1)
    return omml.replace(f"<{head}", f"<{head}{decls}", 1)

def latex_to_omml(latex: str, display: bool = True) -> str | None:
    """Convert a LaTeX snippet into an OMML string, or None if impossible."""
    src = clean_latex(latex)
    if not src or len(src) > 4000:
        return None
    try:
        from latex2mathml.converter import convert as latex_to_mathml
        import mathml2omml

        mathml = latex_to_mathml(src)
        omml = mathml2omml.convert(mathml)
    except Exception:
        return None
    if not omml:
        return None
    omml = _wrap_namespaces(omml)
    if not omml:
        return None
    if display and omml.startswith("<m:oMath"):
        # wrap in a display paragraph container
        inner = omml
        inner = re.sub(r'^<m:oMath[^>]*>', "", inner)
        inner = re.sub(r"</m:oMath>$", "", inner)
        omml = f'<m:oMathPara xmlns:m="{NS_M}" xmlns:w="{NS_W}"><m:oMath>{inner}</m:oMath></m:oMathPara>'
    return omml

def omml_from_latex_fallback(latex: str) -> str | None:
    """Very small converter used when the MathML route fails.

    Handles a single flat expression (no fractions/roots) so simple inline
    formulas still become native equations.
    """
    src = clean_latex(latex)
    if not src or "\\" in src:
        return None
    runs = []
    for chunk in re.findall(r"[A-Za-z0-9\u0370-\u03ff+\-*/=<>()\[\]|.,']+|\s+", src):
        if not chunk:
            continue
        style = "p" if not chunk.strip().isalpha() else "i"
        esc = (
            chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        runs.append(
            f'<m:r><m:rPr><m:sty m:val="{style}"/></m:rPr><m:t xml:space="preserve">{esc}</m:t></m:r>'
        )
    if not runs:
        return None
    return (
        f'<m:oMath xmlns:m="{NS_M}" xmlns:w="{NS_W}">' + "".join(runs) + "</m:oMath>"
    )

# ==== extract.py ================================================
"""PDF -> intermediate layout model.

Every PDF page is turned into a list of ordered *elements*:

* ``text``    - a visual line with styled runs (real text layer or OCR text)
* ``image``   - a picture / vector figure, rasterised at high resolution
* ``formula`` - a math region, with LaTeX when a formula model is available
"""

import io
import re
from dataclasses import dataclass, field

import pymupdf
import numpy as np

@dataclass
class Run:
    text: str
    size: float
    bold: bool = False
    italic: bool = False
    superscript: bool = False
    subscript: bool = False
    color: tuple[int, int, int] = (0, 0, 0)
    math: bool = False

@dataclass
class Element:
    kind: str  # text | image | formula
    bbox: tuple[float, float, float, float]
    runs: list[Run] = field(default_factory=list)
    png: bytes | None = None
    latex: str | None = None
    display: bool = True
    conf: float = 1.0
    source_text: str = ""
    ink: float = 0.0

@dataclass
class PageData:
    index: int
    width: float
    height: float
    elements: list[Element]
    ocr_used: bool = False
    wide_ok: bool = True

@dataclass
class ConvertOptions:
    ocr: str = "auto"  # auto | always | never
    formulas: str = "auto"  # auto | off
    dpi: int = 220
    images: bool = True

MATH_FONT_HINTS = (
    "cmmi",
    "cmsy",
    "cmex",
    "cmext",
    "msam",
    "msbm",
    "symbol",
    "mtmi",
    "mtsy",
    "mtm",
    "math",
    "stix",
    "xits",
    "euclid",
    "cambria math",
    "asana",
    "lmroman",
    "lmmath",
)

MATH_CHARS = set(
    "=+-*/<>^_±×÷∑∏∫∮√≤≥≠≈≡∞∂∇→←↔⇒∈∉⊂⊃∪∩−·∙∕αβγδεζηθικλμνξπρστυφχψω"
    "ΓΔΘΛΞΠΣΦΨΩ"
)

def _is_math_font(font: str) -> bool:
    f = (font or "").lower()
    return any(h in f for h in MATH_FONT_HINTS)

def _color_of(span) -> tuple[int, int, int]:
    c = int(span.get("color", 0))
    return ((c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF)

def _mathish(text: str) -> bool:
    """Weaker signal: a short centred line that looks like an equation."""
    t = text.strip()
    if len(t) > 80:
        return False
    ops = {ch for ch in t if ch in MATH_CHARS}
    if len(ops) >= 2:
        return True
    # "E = m c²" style: a single relation sign, short and few words
    return "=" in t and len(t) <= 40 and len(t.split()) <= 6

def _looks_like_formula(text: str, math_ratio: float) -> bool:
    t = text.strip()
    if not t or len(t) > 220:
        return False
    ops = sum(1 for ch in t if ch in MATH_CHARS)
    has_op = any(ch in t for ch in MATH_CHARS)
    digits = sum(ch.isdigit() for ch in t)
    if not has_op:
        # math font but the operators did not survive extraction
        return math_ratio >= 0.8 and len(t) >= 4
    if math_ratio >= 0.55:
        return True
    return ops >= 2 and math_ratio >= 0.3 and (digits or len(t) < 40)

def _render(page, clip, dpi: int) -> bytes:
    pix = page.get_pixmap(clip=pymupdf.Rect(*clip), dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return pix.tobytes("png")

def _cap_dpi(page, clip, dpi, max_px=2400) -> int:
    w = max(1.0, clip[2] - clip[0])
    limit = int(max_px / (w / 72.0)) if w else dpi
    return max(150, min(dpi, max(limit, 150)))

def _page_has_text(page) -> int:
    total = 0
    for block in page.get_text("blocks"):
        if len(block) >= 7 and block[6] == 0:
            total += len(block[4].strip())
    return total

def _text_elements(page, opts: ConvertOptions) -> list[Element]:
    elements: list[Element] = []
    data = page.get_text("dict")
    page_w = page.rect.width
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        isolated = len(block.get("lines", [])) == 1

        # a block whose glyphs are largely unmapped is handled as a whole, so a
        # multi-line formula (e.g. one with superscripts) is not torn apart
        block_spans = [
            sp
            for ln in block.get("lines", [])
            for sp in ln.get("spans", [])
            if sp.get("text", "").strip()
        ]
        block_raw = "".join(sp["text"] for sp in block_spans)
        if block_spans and _bad_ratio(block_raw) > 0.2:
            bbox = tuple(block.get("bbox") or union([sp["bbox"] for sp in block_spans]))
            mr = sum(
                len(sp["text"]) for sp in block_spans if _is_math_font(sp.get("font", ""))
            ) / max(1, len(block_raw))
            if opts.formulas != "off" and mr >= 0.5:
                elements.append(Element(kind="formula", bbox=bbox, display=True))
            else:
                size = max(6.0, (bbox[3] - bbox[1]) * 0.72)
                elements.append(
                    Element(
                        kind="text",
                        bbox=bbox,
                        runs=[Run(text=block_raw, size=size)],
                    )
                )
            continue

        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans)
            math_chars = sum(len(s["text"]) for s in spans if _is_math_font(s.get("font", "")))
            ratio = math_chars / max(1, len(text))
            line_bbox = tuple(line.get("bbox") or union([s["bbox"] for s in spans]))
            centered = False
            if isolated:
                centred_delta = abs(line_bbox[0] - (page_w - line_bbox[2]))
                centered = centred_delta < page_w * 0.08 and line_bbox[0] > page_w * 0.12
            if opts.formulas != "off" and (
                _looks_like_formula(text, ratio)
                or (centered and _mathish(text))
            ):
                elements.append(
                    Element(
                        kind="formula",
                        bbox=line_bbox,
                        latex=None,
                        display=True,
                        source_text=text,
                    )
                )
                continue
            runs: list[Run] = []
            base_size = max((s.get("size", 10) for s in spans), default=10)
            prev_x1 = None
            for s in spans:
                size = float(s.get("size", base_size))
                flags = int(s.get("flags", 0))
                sb = s["bbox"]
                run_text = s["text"]
                if prev_x1 is not None:
                    gap = sb[0] - prev_x1
                    if gap > size * 1.6:
                        run_text = " " * min(12, max(1, round(gap / (size * 0.5)))) + run_text
                prev_x1 = sb[2]
                sup = bool(flags & 1) or (size < base_size * 0.78 and sb[1] < line_bbox[1] + base_size * 0.25)
                sub = size < base_size * 0.78 and sb[3] > line_bbox[3] - base_size * 0.15
                runs.append(
                    Run(
                        text=run_text,
                        size=size,
                        bold=bool(flags & 16),
                        italic=bool(flags & 2),
                        superscript=sup and not sub,
                        subscript=sub,
                        color=_color_of(s),
                        math=_is_math_font(s.get("font", "")),
                    )
                )
            elements.append(Element(kind="text", bbox=line_bbox, runs=runs))
    return elements

def _ocr_elements(page, opts: ConvertOptions) -> list[Element]:
    dpi = max(200, opts.dpi)
    page_png = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False).tobytes("png")
    result = ocr.ocr_png(page_png)
    k = 72.0 / dpi
    lines: list[Element] = []
    for item in result:
        x0, y0, x1, y1 = item["box"]
        bbox = (x0 * k, y0 * k, x1 * k, y1 * k)
        size = max(6.0, (bbox[3] - bbox[1]) * 0.82)
        lines.append(
            Element(
                kind="text",
                bbox=bbox,
                runs=[Run(text=item["text"], size=size)],
                conf=item["conf"],
            )
        )
    # merge boxes that sit on the same baseline (OCR may split a line)
    merged: list[Element] = []
    for el in sorted(lines, key=lambda e: (round(e.bbox[1], 1), e.bbox[0])):
        if merged:
            prev = merged[-1]
            h = prev.bbox[3] - prev.bbox[1]
            same_row = abs(prev.bbox[1] - el.bbox[1]) < h * 0.5
            gap = el.bbox[0] - prev.bbox[2]
            if same_row and -2 < gap < h * 0.9:
                prev.runs[0].text += " " + el.runs[0].text
                prev.bbox = union([prev.bbox, el.bbox])
                prev.conf = min(prev.conf, el.conf)
                continue
        merged.append(el)

    if opts.formulas != "off":
        for el in merged:
            text = el.runs[0].text
            ops = sum(1 for ch in text if ch in MATH_CHARS)
            density = ops / max(1, len(text))
            if len(text) <= 90 and ops >= 1 and (density > 0.06 or el.conf < 0.72):
                el.kind = "formula"
                el.display = True
                el.source_text = text
    return merged

def _image_elements(
    page, opts: ConvertOptions, dpi: int, max_area_ratio: float = 0.85
) -> list[Element]:
    if not opts.images:
        return []
    out: list[Element] = []
    seen: list[tuple] = []
    page_area = max(1.0, page.rect.width * page.rect.height)
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = []
    for info in infos:
        bbox = tuple(info.get("bbox") or ())
        if len(bbox) != 4:
            continue
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w < 12 or h < 12:
            continue
        if (w * h) / page_area > max_area_ratio:
            # a full page scan / background: the text is already recovered
            continue
        if any(overlaps(bbox, s) for s in seen):
            continue
        seen.append(bbox)
        clip = _clamp(bbox, page.rect)
        if clip is None:
            continue
        out.append(
            Element(
                kind="image",
                bbox=clip,
                png=_render(page, clip, _cap_dpi(page, clip, max(dpi, 300))),
            )
        )
    return out

def _clamp(bbox, rect):
    x0 = max(bbox[0], rect.x0)
    y0 = max(bbox[1], rect.y0)
    x1 = min(bbox[2], rect.x1)
    y1 = min(bbox[3], rect.y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return (x0, y0, x1, y1)

def _residual_figures(page, covered: list[tuple], dpi: int = 72) -> list[Element]:
    """Find ink that is neither text nor an embedded image (vector charts)."""
    scale = 72.0 / dpi
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
    ink = arr < 210
    if not ink.any():
        return []
    mask = ink.copy()
    pad = 3
    for b in covered:
        x0 = int(b[0] * scale) - pad
        y0 = int(b[1] * scale) - pad
        x1 = int(b[2] * scale) + pad
        y1 = int(b[3] * scale) + pad
        mask[max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)] = False
    if mask.sum() < 120:
        return []
    grown = mask.copy()
    for _ in range(8):
        grown |= np.roll(grown, 1, 0) | np.roll(grown, -1, 0)
        grown |= np.roll(grown, 1, 1) | np.roll(grown, -1, 1)
    grown &= ~_covered_mask(mask.shape, covered, scale, pad=0)
    boxes = _components(grown, min_px=90)
    page_area = max(1.0, page.rect.width * page.rect.height)
    out: list[Element] = []
    for (x0, y0, x1, y1) in boxes:
        bb = (x0 / scale, y0 / scale, x1 / scale, y1 / scale)
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        if w < 18 or h < 14:
            continue
        area = w * h
        if area / page_area < 0.004:
            continue
        if max(w, h) / max(1.0, min(w, h)) > 26:
            continue
        fill = mask[int(y0) : int(y1), int(x0) : int(x1)].mean()
        if fill < 0.012:
            continue
        clip = _clamp(bb, page.rect)
        if clip is None:
            continue
        if any(overlaps(clip, e.bbox) for e in out):
            continue
        out.append(
            Element(
                kind="image",
                bbox=clip,
                png=_render(page, clip, _cap_dpi(page, clip, 260)),
                ink=float(fill),
            )
        )
    return out

def _covered_mask(shape, covered, scale, pad=0):
    m = np.zeros(shape, dtype=bool)
    for b in covered:
        x0 = max(0, int(b[0] * scale) - pad)
        y0 = max(0, int(b[1] * scale) - pad)
        x1 = min(shape[1], int(b[2] * scale) + pad)
        y1 = min(shape[0], int(b[3] * scale) + pad)
        m[y0:y1, x0:x1] = True
    return m

def _components(mask: np.ndarray, min_px: int = 60) -> list[tuple[int, int, int, int]]:
    """Coarse connected components (4-neighbour flood fill on a boolean grid)."""
    h, w = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    boxes: list[tuple[int, int, int, int]] = []
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys[:: max(1, len(ys) // 4000 or 1)], xs[:: max(1, len(xs) // 4000 or 1)]):
        if visited[sy, sx]:
            continue
        stack = [(sy, sx)]
        visited[sy, sx] = True
        x0 = x1 = sx
        y0 = y1 = sy
        count = 0
        while stack:
            cy, cx = stack.pop()
            count += 1
            if cx < x0:
                x0 = cx
            elif cx > x1:
                x1 = cx
            if cy < y0:
                y0 = cy
            elif cy > y1:
                y1 = cy
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
                if count > 400000:
                    break
        if count >= min_px:
            boxes.append((x0, y0, x1, y1))
    return boxes

def _plain(latex: str) -> str:
    """Rough text content of a LaTeX snippet, for sanity checking."""
    s = re.sub(r"\\[a-zA-Z]+", " ", latex)
    s = re.sub(r"[{}^_$]", " ", s)
    return re.sub(r"[^0-9A-Za-z\u0370-\u03ff+\-*/=<>().,]", "", s).lower()

def _consistent(source: str, latex: str) -> bool:
    """Reject recognitions that lose most of the original characters.

    Digital PDFs already give us the characters, so a formula model that
    returns something unrelated is almost always a misread.
    """
    from collections import Counter

    src = re.sub(r"[^0-9A-Za-z\u0370-\u03ff]", "", source or "").lower()
    got = _plain(latex)
    if len(src) < 3:
        return True  # nothing reliable to compare against
    if not got:
        return False
    src_count = Counter(src)
    got_count = Counter(got)
    common = sum(min(n, got_count[ch]) for ch, n in src_count.items())
    return common / len(src) >= 0.65

def _resolve_figures(figures: list[Element], elements: list[Element]):
    """Decide between "graphic that contains text" and "ruled table".

    A region made of thin lines (a ruled table or a frame) with text inside is
    dropped in favour of the text, so the words stay editable and are not
    duplicated.  A dense graphic (photo / chart) keeps its picture and the
    labels inside it are removed to avoid duplication.
    """
    keep: list[Element] = []
    drop_text: list[Element] = []
    for fig in figures:
        inside = [
            el
            for el in elements
            if el.kind == "text"
            and fig.bbox[0] <= (el.bbox[0] + el.bbox[2]) / 2 <= fig.bbox[2]
            and fig.bbox[1] <= (el.bbox[1] + el.bbox[3]) / 2 <= fig.bbox[3]
        ]
        if not inside:
            keep.append(fig)
            continue
        if fig.ink >= 0.10:
            keep.append(fig)
            drop_text.extend(inside)
        # else: ruled lines / frame -> keep the text, forget the picture
    if drop_text:
        elements = [el for el in elements if el not in drop_text]
    return keep, elements

def _bad_ratio(text: str) -> float:
    """Share of characters the PDF failed to map (\x00 / replacement chars)."""
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ch in "\x00\ufffd")
    return bad / len(text)

def _math_ratio(runs: list[Run]) -> float:
    total = sum(len(r.text) for r in runs) or 1
    return sum(len(r.text) for r in runs if r.math) / total

def _repair_broken_lines(page, elements: list[Element], opts: ConvertOptions) -> None:
    """Recover lines whose text layer has no usable ToUnicode mapping.

    Some PDFs (especially old math typesetting) expose \x00 for every glyph.
    Those lines are re-read from a rendered crop, either with the formula model
    or with the OCR engine.
    """
    if not ocr.available():
        return
    for el in elements:
        if el.kind != "text":
            continue
        raw = "".join(r.text for r in el.runs)
        if _bad_ratio(raw) < 0.2:
            continue
        clip = _clamp(_expand(el.bbox, 1.5, page.rect), page.rect)
        if clip is None:
            continue
        png = _render(page, clip, _cap_dpi(page, clip, max(300, opts.dpi * 2)))
        mathy = _math_ratio(el.runs) >= 0.5
        if mathy and opts.formulas != "off" and formula.available():
            latex = formula.image_to_latex(png)
            if latex and _consistent("", latex):
                el.kind = "formula"
                el.latex = latex
                el.png = png
                el.source_text = ""
                continue
        found = ocr.ocr_png(png)
        text = " ".join(r["text"] for r in found).strip()
        if text:
            size = max(7.0, (el.bbox[3] - el.bbox[1]) * 0.75)
            el.runs = [Run(text=text, size=size)]
            el.bbox = (el.bbox[0], el.bbox[1], max(el.bbox[2], el.bbox[0] + 2), el.bbox[3])

def _apply_formula_ocr(page, elements: list[Element], dpi: int) -> None:
    for el in elements:
        if el.kind != "formula":
            continue
        clip = _clamp(_expand(el.bbox, 2.0, page.rect), page.rect)
        if clip is None:
            el.kind = "text"
            el.runs = [Run(text="", size=10)]
            continue
        el.png = _render(page, clip, _cap_dpi(page, clip, max(320, dpi * 2)))
        latex = formula.image_to_latex(el.png)
        if latex and _consistent(el.source_text, latex):
            el.latex = latex
        elif el.source_text:
            # keep the characters we already had instead of a garbled equation
            el.kind = "text"
            el.runs = [Run(text=el.source_text, size=max(8.0, (el.bbox[3] - el.bbox[1]) * 0.72))]
        elif latex:
            el.kind = "image"

def _expand(bbox, pad: float, rect):
    return (
        max(rect.x0, bbox[0] - pad),
        max(rect.y0, bbox[1] - pad),
        min(rect.x1, bbox[2] + pad),
        min(rect.y1, bbox[3] + pad),
    )

def extract_page(page, index: int, opts: ConvertOptions) -> PageData:
    rect = page.rect
    chars = _page_has_text(page)
    use_ocr = opts.ocr == "always" or (opts.ocr == "auto" and chars < 25)

    if use_ocr and ocr.available():
        elements = _ocr_elements(page, opts)
        if not elements:  # OCR produced nothing -> fall back to text layer
            elements = _text_elements(page, opts)
            use_ocr = False
    else:
        elements = _text_elements(page, opts)
        use_ocr = False

    _repair_broken_lines(page, elements, opts)

    covered = [e.bbox for e in elements]
    if opts.images:
        elements.extend(
            _image_elements(page, opts, opts.dpi, 0.6 if use_ocr else 0.85)
        )
        figures = _residual_figures(page, [e.bbox for e in elements])
        figures, elements = _resolve_figures(figures, elements)
        elements.extend(figures)

    if opts.formulas != "off" and formula.available():
        _apply_formula_ocr(page, elements, opts.dpi)

    elements = xy_cut(elements)
    return PageData(
        index=index,
        width=rect.width,
        height=rect.height,
        elements=elements,
        ocr_used=use_ocr,
    )

# ==== ocr.py (embedded as a module object) ==============================
_ocr_src = r'''"""OCR engine wrapper.

Primary engine: RapidOCR (PP-OCR models running on onnxruntime, fully local,
no network needed at inference time, supports Chinese + English + digits).
"""
from __future__ import annotations

import io
import threading
from typing import Any

_LOCK = threading.Lock()
_ENGINE: Any = None
_ENGINE_ERROR: str | None = None


def available() -> bool:
    _ensure()
    return _ENGINE is not None


def engine_error() -> str | None:
    return _ENGINE_ERROR


def _ensure() -> None:
    global _ENGINE, _ENGINE_ERROR
    if _ENGINE is not None or _ENGINE_ERROR is not None:
        return
    with _LOCK:
        if _ENGINE is not None or _ENGINE_ERROR is not None:
            return
        try:
            from rapidocr_onnxruntime import RapidOCR

            _ENGINE = RapidOCR()
        except Exception as exc:  # pragma: no cover - depends on install
            _ENGINE_ERROR = f"{type(exc).__name__}: {exc}"


def ocr_png(png_bytes: bytes) -> list[dict]:
    """Run OCR on a PNG/JPEG byte string.

    Returns a list of ``{"box": (x0, y0, x1, y1), "text": str, "conf": float}``
    in pixel coordinates of the supplied image.
    """
    _ensure()
    if _ENGINE is None:
        return []
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    arr = np.array(img)
    try:
        result, _ = _ENGINE(arr)
    except Exception:
        return []
    out: list[dict] = []
    for item in result or []:
        try:
            pts, text, conf = item[0], item[1], float(item[2])
        except Exception:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if not text or not text.strip():
            continue
        out.append(
            {
                "box": (min(xs), min(ys), max(xs), max(ys)),
                "text": text.strip(),
                "conf": conf,
            }
        )
    out.sort(key=lambda r: (round(r["box"][1], 1), r["box"][0]))
    return out
'''
ocr = _types.ModuleType('ocr')
exec(compile(_ocr_src, 'ocr.py', 'exec'), ocr.__dict__)

# ==== formula.py (embedded as a module object) ==============================
_formula_src = r'''"""Formula image -> LaTeX engine wrapper (pix2tex / LaTeX-OCR).

The model is optional: when it is missing, formula regions are still preserved
as high resolution inline images and the caller is told about the downgrade.
"""
from __future__ import annotations

import io
import re
import threading
from typing import Any

_LOCK = threading.Lock()
_MODEL: Any = None
_STATE: str = "unset"  # unset | ready | missing
_ERROR: str | None = None


def _ensure() -> None:
    global _MODEL, _STATE, _ERROR
    if _STATE != "unset":
        return
    with _LOCK:
        if _STATE != "unset":
            return
        try:
            from pix2tex.cli import LatexOCR

            _MODEL = LatexOCR()
            _STATE = "ready"
        except Exception as exc:  # pragma: no cover - optional dependency
            _STATE = "missing"
            _ERROR = f"{type(exc).__name__}: {exc}"


def available() -> bool:
    _ensure()
    return _STATE == "ready"


def engine_error() -> str | None:
    _ensure()
    return _ERROR


def _looks_like_math(latex: str) -> bool:
    if not latex:
        return False
    if len(latex) > 320:
        return False
    # reject pure OCR noise: no math structure and no operators
    if not re.search(r"[=\+\-\*/^_\\<>≤≥≠∑∫√±]", latex) and len(latex) < 2:
        return False
    letters = len(re.findall(r"[A-Za-z]", latex))
    if letters > 60:
        return False
    return True


def image_to_latex(png_bytes: bytes) -> str | None:
    """Run formula recognition on a cropped formula image."""
    _ensure()
    if _STATE != "ready":
        return None
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    # pix2tex works best with a white background and a few pixels of padding
    w, h = img.size
    pad = max(4, int(h * 0.12))
    canvas = Image.new("RGB", (w + 2 * pad, h + 2 * pad), "white")
    canvas.paste(img, (pad, pad))
    try:
        latex = _MODEL(canvas)
    except Exception:
        return None
    if not latex:
        return None
    latex = latex.strip()
    return latex if _looks_like_math(latex) else None
'''
formula = _types.ModuleType('formula')
exec(compile(_formula_src, 'formula.py', 'exec'), formula.__dict__)

# ==== writer.py =================================================
"""Layout model -> .docx writer (text, pictures and native equations)."""

import io
import re

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.shared import Pt, RGBColor

MARGIN = 14.0  # points

# XML 1.0 forbids most control characters; PDF fonts can yield them.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

def _xml_safe(text: str) -> str:
    text = _ILLEGAL.sub("", text)
    return text.encode("utf-8", "ignore").decode("utf-8")

def _setup_section(section, page: PageData) -> None:
    section.page_width = Pt(page.width)
    section.page_height = Pt(page.height)
    section.left_margin = Pt(MARGIN)
    section.right_margin = Pt(MARGIN)
    section.top_margin = Pt(MARGIN)
    section.bottom_margin = Pt(MARGIN)
    section.header_distance = Pt(6)
    section.footer_distance = Pt(6)

def _new_paragraph(doc, gap_before: float, indent: float):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(max(0.0, min(gap_before, 26.0)))
    pf.space_after = Pt(0)
    pf.line_spacing = 1.0
    pf.left_indent = Pt(max(0.0, indent))
    pf.widow_control = False
    return p

def _add_runs(p, el: Element) -> None:
    for run in el.runs:
        if not run.text:
            continue
        safe = _xml_safe(run.text)
        if not safe:
            continue
        r = p.add_run(safe)
        r.font.size = Pt(max(5.0, round(run.size, 1)))
        r.bold = run.bold
        r.italic = run.italic
        if run.superscript:
            r.font.superscript = True
        if run.subscript:
            r.font.subscript = True
        if run.color != (0, 0, 0):
            r.font.color.rgb = RGBColor(*run.color)

def _add_equation(p, latex: str, display: bool = True) -> bool:
    xml = latex_to_omml(latex, display=display) or omml_from_latex_fallback(latex)
    if not xml:
        return False
    try:
        p._p.append(parse_xml(xml))
        return True
    except Exception:
        return False

def _add_picture(p, png: bytes, width_pt: float) -> bool:
    try:
        p.add_run().add_picture(io.BytesIO(png), width=Pt(max(4.0, width_pt)))
        return True
    except Exception:
        return False

def write_docx(pages: list[PageData], out_path: str, title: str = "") -> dict:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(0)

    stats = {
        "pages": len(pages),
        "ocr_pages": 0,
        "text_lines": 0,
        "images": 0,
        "equations": 0,
        "equations_as_image": 0,
        "warnings": [],
    }

    _setup_section(doc.sections[0], pages[0])
    prev_w = pages[0].width
    prev_h = pages[0].height

    for pi, page in enumerate(pages):
        if pi > 0:
            if abs(page.width - prev_w) > 1 or abs(page.height - prev_h) > 1:
                sec = doc.add_section(WD_SECTION.NEW_PAGE)
                _setup_section(sec, page)
                prev_w, prev_h = page.width, page.height
            else:
                doc.add_page_break()
        if page.ocr_used:
            stats["ocr_pages"] += 1

        prev_bottom = MARGIN
        for el in page.elements:
            gap = el.bbox[1] - prev_bottom
            indent = el.bbox[0] - MARGIN
            width_pt = el.bbox[2] - el.bbox[0]

            if el.kind == "text":
                if not any(run.text.strip() for run in el.runs):
                    continue
                p = _new_paragraph(doc, gap, indent)
                _add_runs(p, el)
                stats["text_lines"] += 1

            elif el.kind == "formula":
                p = _new_paragraph(doc, gap, indent)
                if el.latex and _add_equation(p, el.latex, display=True):
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    p.paragraph_format.left_indent = Pt(0)
                    stats["equations"] += 1
                elif el.png and _add_picture(p, el.png, width_pt):
                    stats["equations_as_image"] += 1
                    stats["warnings"].append(
                        f"page {page.index + 1}: formula kept as picture (no LaTeX)"
                    )
                else:
                    stats["warnings"].append(
                        f"page {page.index + 1}: formula region could not be recovered"
                    )

            elif el.kind == "image" and el.png:
                p = _new_paragraph(doc, gap, indent)
                if _add_picture(p, el.png, width_pt):
                    stats["images"] += 1

            prev_bottom = el.bbox[3]

    if title:
        doc.core_properties.title = title
    doc.save(out_path)

    # de-duplicate warnings, keep the report readable
    seen = set()
    unique = []
    for w in stats["warnings"]:
        key = w.split(":", 1)[-1]
        if key in seen:
            continue
        seen.add(key)
        unique.append(w)
    stats["warnings"] = unique[:20]
    return stats

# ==== ops.py ====================================================
"""Page level operations: merge and split A3 sheets into A4 pages.

Uses PyMuPDF only, so the result stays vector (no rasterising) and works
offline on a laptop.
"""

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

# ==== __init__.py ===============================================
"""PDF conversion pipeline: PDF -> editable DOCX (text + images + equations)."""

import os
from typing import Callable

import pymupdf

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


# ==== GUI / CLI ==============================================
# GUI + CLI，合併進 pdf_studio.py 的最後一段（也可單獨閱讀）
import argparse
import os
import queue
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog

APP_TITLE = "PDF Studio"
PAD = 10
ACCENT = "#4f46e5"


def _open_pdf(path: str):
    return pymupdf.open(path)


# --------------------------------------------------------------------------- #
# 桌面視窗：只用基本 tk 控件（Label / Button / Listbox / OptionMenu / Canvas），
# 避免在部分 Linux 的 Tk 上建立複雜控件時中斷。
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x640")
        self.minsize(760, 560)
        self.configure(bg="#f5f6fa")
        self.files: list[str] = []
        self.q: queue.Queue = queue.Queue()
        self.busy = False

        self.outdir = tk.StringVar(value=os.path.expanduser("~"))

        head = tk.Frame(self, bg="#ffffff", height=58)
        head.pack(fill="x")
        tk.Label(head, text=APP_TITLE, bg="#ffffff", fg="#0f172a",
                 font=("", 16, "bold")).place(x=16, y=14)
        self.engine_lbl = tk.Label(head, text="檢查環境中…", bg="#ffffff", fg="#667085",
                                   font=("", 10))
        self.engine_lbl.place(relx=1.0, x=-16, y=20, anchor="ne")

        bar = tk.Frame(self, bg="#eef0f6", height=44)
        bar.pack(fill="x")
        self.tabs = {}
        for i, (key, label) in enumerate(
            [("word", "PDF 轉 Word"), ("merge", "合併 PDF"), ("split", "A3 轉 A4")]
        ):
            b = tk.Button(bar, text=label, bd=0, relief="flat", bg="#eef0f6", fg="#334155",
                          activebackground="#ffffff", font=("", 11),
                          command=lambda k=key: self.show(k))
            b.place(x=8 + i * 150, y=6, width=142, height=32)
            self.tabs[key] = b

        self.body = tk.Frame(self, bg="#f5f6fa")
        self.body.pack(fill="both", expand=True)
        self.pages = {k: tk.Frame(self.body, bg="#f5f6fa") for k in ("word", "merge", "split")}
        for f in self.pages.values():
            f.place(relwidth=1, relheight=1)

        self._build_word()
        self._build_merge()
        self._build_split()
        self.show("word")

        self.progress = tk.Canvas(self, height=6, bg="#e9ecf5", highlightthickness=0)
        self.progress.pack(fill="x", side="bottom")
        self.bar = self.progress.create_rectangle(0, 0, 0, 6, fill=ACCENT, width=0)
        self.status = tk.Label(self, text="就緒", bg="#ffffff", fg="#475069", anchor="w",
                               font=("", 9))
        self.status.pack(fill="x", side="bottom")

        self.after(200, self._poll)
        threading.Thread(target=self._probe, daemon=True).start()

    # ---------------- helpers ----------------
    def show(self, key: str) -> None:
        self.pages[key].lift()
        for k, b in self.tabs.items():
            b.configure(bg="#ffffff" if k == key else "#eef0f6",
                        fg=ACCENT if k == key else "#334155")

    def _probe(self) -> None:
        try:
            caps = capabilities()
            self.q.put(("engines", caps))
        except Exception as e:  # noqa: BLE001
            self.q.put(("engines", {"error": str(e)}))

    def _poll(self) -> None:
        try:
            while True:
                kind, *payload = self.q.get_nowait()
                if kind == "engines":
                    caps = payload[0]
                    ocr = "OCR 就緒" if caps.get("ocr") else "OCR 未安裝"
                    fml = "數式就緒" if caps.get("formula") else "數式未安裝"
                    self.engine_lbl.configure(text=f"{ocr} · {fml}")
                elif kind == "status":
                    self.status.configure(text=payload[0])
                elif kind == "progress":
                    self._set_progress(payload[0])
                elif kind == "done":
                    self._finish(payload[0])
                elif kind == "error":
                    self._finish(payload[0], error=True)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _set_progress(self, ratio: float) -> None:
        w = self.progress.winfo_width() or 1
        self.progress.coords(self.bar, 0, 0, max(0.0, min(1.0, ratio)) * w, 6)

    def _finish(self, message: str, error: bool = False) -> None:
        self.busy = False
        self.status.configure(text=message, fg="#d33d43" if error else "#0f9d63")
        self._set_progress(1.0 if not error else 0.0)
        for b in self.action_buttons:
            b.configure(state="normal")

    def _start(self) -> None:
        if self.busy:
            return
        self.busy = True
        for b in self.action_buttons:
            b.configure(state="disabled")
        self._set_progress(0.0)

    # ---------------- files list ----------------
    def _make_list(self, parent) -> tk.Listbox:
        box = tk.Listbox(parent, relief="flat", highlightthickness=1,
                         highlightbackground="#e2e6f0", bg="#ffffff", font=("", 10),
                         selectmode="extended", activestyle="none")
        box.pack(fill="both", expand=True, padx=PAD, pady=(0, 6))
        return box

    def _buttons_row(self, parent, add_cmd, remove_cmd, out_cmd=None):
        row = tk.Frame(parent, bg="#f5f6fa")
        row.pack(fill="x", padx=PAD, pady=(0, 8))
        tk.Button(row, text="加入 PDF…", command=add_cmd, bg="#ffffff", relief="solid",
                  bd=1, padx=12, pady=4).pack(side="left")
        tk.Button(row, text="移除", command=remove_cmd, bg="#ffffff", relief="solid",
                  bd=1, padx=12, pady=4).pack(side="left", padx=6)
        if out_cmd:
            tk.Button(row, text="輸出資料夾…", command=out_cmd, bg="#ffffff",
                      relief="solid", bd=1, padx=12, pady=4).pack(side="left", padx=6)
        return row

    # ---------------- tab 1 ----------------
    def _build_word(self) -> None:
        f = self.pages["word"]
        top = tk.Frame(f, bg="#f5f6fa")
        top.pack(fill="both", expand=True)
        tk.Label(top, text="要轉換的 PDF（可多選，依序處理）", bg="#f5f6fa", fg="#475069",
                 font=("", 10)).pack(anchor="w", padx=PAD, pady=(12, 4))
        self.list_word = self._make_list(top)
        self._buttons_row(top, self._add_word, self._remove_word,
                          lambda: self._pick_out(self.outdir))
        self.out_lbl = tk.Label(top, text="", bg="#f5f6fa", fg="#7b849b", font=("", 9),
                                anchor="w")
        self.out_lbl.pack(fill="x", padx=PAD)

        opts = tk.Frame(top, bg="#f5f6fa")
        opts.pack(fill="x", padx=PAD, pady=10)
        tk.Label(opts, text="OCR", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.ocr_mode = tk.StringVar(value="自動（掃描頁才 OCR）")
        tk.OptionMenu(opts, self.ocr_mode, "自動（掃描頁才 OCR）", "一律 OCR", "關閉").pack(
            side="left", padx=(6, 18))
        tk.Label(opts, text="數式", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.fml_mode = tk.StringVar(value="開啟")
        tk.OptionMenu(opts, self.fml_mode, "開啟", "關閉").pack(side="left", padx=(6, 18))
        tk.Label(opts, text="解析度", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.dpi_var = tk.StringVar(value="200")
        tk.OptionMenu(opts, self.dpi_var, "150", "200", "250", "300").pack(side="left", padx=6)

        act = tk.Frame(top, bg="#f5f6fa")
        act.pack(fill="x", padx=PAD, pady=(4, 12))
        b = tk.Button(act, text="開始轉換", command=self._run_word, bg=ACCENT, fg="white",
                      relief="flat", padx=22, pady=8, font=("", 11, "bold"),
                      activebackground="#4338ca", activeforeground="white")
        b.pack(side="left")
        self.action_buttons = [b]
        self.out_lbl.configure(text=f"輸出到：{self.outdir.get()}")

    def _add_word(self) -> None:
        paths = filedialog.askopenfilenames(title="選擇 PDF", filetypes=[("PDF", "*.pdf")])
        for p in paths:
            self.files.append(p)
            self.list_word.insert("end", os.path.basename(p))

    def _remove_word(self) -> None:
        for i in reversed(self.list_word.curselection()):
            self.list_word.delete(i)
            self.files.pop(i)

    def _pick_out(self, var) -> None:
        d = filedialog.askdirectory(title="選擇輸出資料夾", initialdir=var.get())
        if d:
            var.set(d)
            self.out_lbl.configure(text=f"輸出到：{d}")

    def _run_word(self) -> None:
        if not self.files:
            self.status.configure(text="請先加入 PDF", fg="#d33d43")
            return
        self._start()
        outdir = self.outdir.get()
        ocr = {"自動（掃描頁才 OCR）": "auto", "一律 OCR": "always", "關閉": "never"}[
            self.ocr_mode.get()]
        opts = ConvertOptions(ocr=ocr, formulas="auto" if self.fml_mode.get() == "開啟" else "off",
                              dpi=int(self.dpi_var.get()))
        paths = list(self.files)
        threading.Thread(target=self._word_worker, args=(paths, outdir, opts),
                         daemon=True).start()

    def _word_worker(self, paths, outdir, opts) -> None:
        try:
            for n, src in enumerate(paths, 1):
                name = os.path.splitext(os.path.basename(src))[0] + ".docx"
                dst = os.path.join(outdir, name)
                base, i = dst, 2
                while os.path.exists(dst):
                    dst = f"{os.path.splitext(base)[0]}({i}).docx"
                    i += 1

                def progress(done, total, msg, n=n):
                    frac = (n - 1 + min(1.0, done / max(1, total))) / len(paths)
                    self.q.put(("progress", frac))
                    self.q.put(("status", f"[{n}/{len(paths)}] {os.path.basename(src)} · {msg}"))

                stats = convert_pdf(src, dst, opts, progress=progress)
                self.q.put(("status",
                            f"完成 {name}：{stats['pages']} 頁、{stats['text_lines']} 段、"
                            f"{stats['images']} 圖、{stats['equations']} 條方程式"))
            self.q.put(("done", f"完成 {len(paths)} 個檔案"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.q.put(("error", f"失敗：{e}"))

    # ---------------- tab 2 ----------------
    def _build_merge(self) -> None:
        f = self.pages["merge"]
        tk.Label(f, text="依清單順序合併（用 ↑↓ 調整）", bg="#f5f6fa", fg="#475069",
                 font=("", 10)).pack(anchor="w", padx=PAD, pady=(12, 4))
        self.list_merge = self._make_list(f)
        row = self._buttons_row(f, self._add_merge, self._remove_merge)
        tk.Button(row, text="↑", command=lambda: self._move(-1), bg="#ffffff",
                  relief="solid", bd=1, padx=10, pady=4).pack(side="left", padx=6)
        tk.Button(row, text="↓", command=lambda: self._move(1), bg="#ffffff",
                  relief="solid", bd=1, padx=10, pady=4).pack(side="left")
        act = tk.Frame(f, bg="#f5f6fa")
        act.pack(fill="x", padx=PAD, pady=8)
        b = tk.Button(act, text="合併並存檔…", command=self._run_merge, bg=ACCENT, fg="white",
                      relief="flat", padx=22, pady=8, font=("", 11, "bold"))
        b.pack(side="left")
        self.action_buttons.append(b)

    def _add_merge(self) -> None:
        for p in filedialog.askopenfilenames(title="選擇 PDF", filetypes=[("PDF", "*.pdf")]):
            self.merge_files = getattr(self, "merge_files", [])
            self.merge_files.append(p)
            self.list_merge.insert("end", os.path.basename(p))

    def _remove_merge(self) -> None:
        self.merge_files = getattr(self, "merge_files", [])
        for i in reversed(self.list_merge.curselection()):
            self.list_merge.delete(i)
            self.merge_files.pop(i)

    def _move(self, delta: int) -> None:
        self.merge_files = getattr(self, "merge_files", [])
        sel = self.list_merge.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if not (0 <= j < len(self.merge_files)):
            return
        self.merge_files[i], self.merge_files[j] = self.merge_files[j], self.merge_files[i]
        self.list_merge.delete(i, i)
        self.list_merge.insert(j, os.path.basename(self.merge_files[j]))
        self.list_merge.itemconfig(i, selectbackground="#c7d2fe")
        self.list_merge.selection_clear(0, "end")
        self.list_merge.selection_set(j)

    def _run_merge(self) -> None:
        files = getattr(self, "merge_files", [])
        if len(files) < 2:
            self.status.configure(text="至少需要兩個 PDF", fg="#d33d43")
            return
        dst = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           filetypes=[("PDF", "*.pdf")],
                                           initialfile="merged.pdf")
        if not dst:
            return
        self._start()
        threading.Thread(target=self._merge_worker, args=(files, dst), daemon=True).start()

    def _merge_worker(self, files, dst) -> None:
        try:
            stats = merge_pdfs(files, dst)
            self.q.put(("done", f"已合併 {stats['files']} 個檔案、共 {stats['pages']} 頁 → {os.path.basename(dst)}"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.q.put(("error", f"合併失敗：{e}"))

    # ---------------- tab 3 ----------------
    def _build_split(self) -> None:
        f = self.pages["split"]
        tk.Label(f, text="A3 沿中線切成兩張 A4（向量保留，不會變模糊）", bg="#f5f6fa",
                 fg="#475069", font=("", 10)).pack(anchor="w", padx=PAD, pady=(12, 4))
        self.list_split = self._make_list(f)
        self._buttons_row(f, self._add_split, self._remove_split)
        opts = tk.Frame(f, bg="#f5f6fa")
        opts.pack(fill="x", padx=PAD, pady=8)
        tk.Label(opts, text="切割方向", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.axis_var = tk.StringVar(value="自動")
        tk.OptionMenu(opts, self.axis_var, "自動", "垂直（左右）", "水平（上下）").pack(
            side="left", padx=(6, 18))
        tk.Label(opts, text="輸出順序", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.order_var = tk.StringVar(value="左→右 / 上→下")
        tk.OptionMenu(opts, self.order_var, "左→右 / 上→下", "右→左 / 下→上").pack(side="left", padx=6)
        act = tk.Frame(f, bg="#f5f6fa")
        act.pack(fill="x", padx=PAD, pady=8)
        b = tk.Button(act, text="切成 A4…", command=self._run_split, bg=ACCENT, fg="white",
                      relief="flat", padx=22, pady=8, font=("", 11, "bold"))
        b.pack(side="left")
        self.action_buttons.append(b)
        self.split_files = []

    def _add_split(self) -> None:
        for p in filedialog.askopenfilenames(title="選擇 A3 PDF", filetypes=[("PDF", "*.pdf")]):
            self.split_files.append(p)
            self.list_split.insert("end", os.path.basename(p))

    def _remove_split(self) -> None:
        for i in reversed(self.list_split.curselection()):
            self.list_split.delete(i)
            self.split_files.pop(i)

    def _run_split(self) -> None:
        if not self.split_files:
            self.status.configure(text="請先加入 A3 PDF", fg="#d33d43")
            return
        dst = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           filetypes=[("PDF", "*.pdf")],
                                           initialfile="a4.pdf")
        if not dst:
            return
        axis = {"自動": "auto", "垂直（左右）": "vertical", "水平（上下）": "horizontal"}[
            self.axis_var.get()]
        order = "reverse" if self.order_var.get().startswith("右") else "normal"
        self._start()
        threading.Thread(target=self._split_worker,
                         args=(list(self.split_files), dst, axis, order), daemon=True).start()

    def _split_worker(self, files, dst, axis, order) -> None:
        try:
            tmp = []
            for i, src in enumerate(files):
                part = dst if len(files) == 1 else f"{os.path.splitext(dst)[0]}_{i + 1}.pdf"
                tmp.append(part)
                self.q.put(("status", f"切割 {os.path.basename(src)}…"))
                split_a3(src, part, axis=axis, order=order)
            total = sum(pymupdf.open(p).page_count for p in tmp)
            self.q.put(("done", f"完成，共 {total} 頁 → {os.path.basename(dst)}"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.q.put(("error", f"切割失敗：{e}"))


# --------------------------------------------------------------------------- #
# 命令列
# --------------------------------------------------------------------------- #
def run_cli(argv: list[str]) -> int:
    if not argv or argv[0] == "--check":
        caps = capabilities()
        print(f"OCR 引擎 : {'就緒' if caps['ocr'] else '未安裝'}")
        print(f"數式引擎 : {'就緒' if caps['formula'] else '未安裝'}")
        return 0
    outdir = os.path.dirname(os.path.abspath(argv[0])) or "."
    for src in argv:
        if not os.path.exists(src):
            print(f"找不到 {src}")
            continue
        dst = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".docx")
        print(f"轉換 {os.path.basename(src)} …")
        stats = convert_pdf(src, dst, ConvertOptions(),
                            progress=lambda d, t, m: print(f"  {m}", end="\r"))
        print(f"\n完成 {dst}")
        print(f"  {stats['pages']} 頁、{stats['text_lines']} 段文字、"
              f"{stats['images']} 張圖、{stats['equations']} 條方程式")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-"):
        return run_cli(argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv and argv[0] == "--check":
        return run_cli(["--check"])
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
