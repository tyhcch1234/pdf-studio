"""PDF -> intermediate layout model.

Every PDF page is turned into a list of ordered *elements*:

* ``text``    - a visual line with styled runs (real text layer or OCR text)
* ``image``   - a picture / vector figure, rasterised at high resolution
* ``formula`` - a math region, with LaTeX when a formula model is available
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pymupdf
import numpy as np

from . import formula, ocr
from .layout import overlaps, union, xy_cut


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
