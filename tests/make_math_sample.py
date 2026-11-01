"""Build the maths fixtures used by the browser tests.

  tests/samples/math_digital.pdf  – real text layer, real math font, drawn
                                    fraction bars (what a book PDF looks like)
  tests/samples/math_scan.pdf     – the same page rasterised, no text layer
                                    (what a scanned handout looks like)

  python3 tests/make_math_sample.py
"""
from __future__ import annotations

import os

import pymupdf as fitz

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")
MATH_FONT = "/usr/share/fonts/truetype/noto/NotoSansMath-Regular.ttf"


class Pen:
    """Types runs of text with the baseline / size a formula would have."""

    def __init__(self, page: fitz.Page, fontfile: str):
        self.page = page
        self.font = fitz.Font(fontfile=fontfile)
        page.insert_font(fontname="math", fontfile=fontfile)

    def width(self, text: str, size: float) -> float:
        return self.font.text_length(text, fontsize=size)

    def run(self, x: float, y: float, text: str, size: float) -> float:
        self.page.insert_text((x, y), text, fontname="math", fontsize=size, color=(0, 0, 0))
        return x + self.width(text, size)

    def line(self, x: float, y: float, parts: list[tuple[str, float, float, float]]) -> float:
        """parts: (text, dy relative to baseline, size, extra advance)"""
        cx = x
        for text, dy, size, pad in parts:
            cx = self.run(cx, y + dy, text, size) + pad
        return cx

    def bar(self, x0: float, x1: float, y: float, weight: float = 0.7) -> None:
        self.page.draw_line(fitz.Point(x0, y), fitz.Point(x1, y), color=(0, 0, 0), width=weight)


def build_digital(path: str, fontfile: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    pen = Pen(page, fontfile)

    page.insert_text((70, 70), "Mathematics Sample / 數學範例", fontsize=16, fontname="helv")
    page.insert_text((70, 95), "Digital PDF with a real text layer (test fixture)", fontsize=9, fontname="helv")

    # ---- 1. plain power form:  E = mc² and x² + 2x + 1 = (x + 1)²
    base = 150
    pen.line(150, base, [
        ("x", 0, 13, 0), ("2", -6.5, 8.5, 3), ("+2x+1=(x+1)", 0, 13, 0), ("2", -6.5, 8.5, 0),
    ])

    # ---- 2. quadratic formula with a real fraction bar
    base = 230
    cx = pen.line(110, base, [("x=", 0, 13, 4)])
    num_w = pen.width("−b±√(b", 12) + 5 + pen.width("2", 8)
    num_w += pen.width("−4ac)", 12)
    frac_w = max(num_w, pen.width("2a", 12))
    num_x = cx + (frac_w - num_w) / 2
    pen.line(num_x, base - 9, [
        ("−b±√(b", 0, 12, 0), ("2", -5, 8, 2.5), ("−4ac)", 0, 12, 0),
    ])
    pen.bar(cx - 2, cx + frac_w + 2, base + 1.5)
    den_x = cx + (frac_w - pen.width("2a", 12)) / 2
    pen.line(den_x, base + 20, [("2a", 0, 12, 0)])

    # ---- 3. sum with limits drawn above and below
    base = 330
    cx = 150
    sum_w = pen.width("∑", 20)
    pen.run(cx, base, "∑", 20)
    pen.run(cx + 1, base + 15, "i=1", 8)
    pen.run(cx + 4, base - 12, "n", 8)
    cx += sum_w + 6
    cx = pen.line(cx, base, [("i", 0, 13, 0), ("2", -6.5, 8.5, 6), ("=", 0, 13, 6)])
    # right hand side: n(n+1)(2n+1)/6 as a stacked fraction
    rhs_x = cx
    pen.line(rhs_x, base - 9, [("n(n+1)(2n+1)", 0, 12, 0)])
    pen.bar(rhs_x - 2, rhs_x + pen.width("n(n+1)(2n+1)", 12) + 2, base + 1.5)
    pen.run(rhs_x + pen.width("n(n+1)(2n+1)", 12) / 2 - 3, base + 20, "6", 12)

    # ---- 4. integral
    base = 420
    cx = 150
    pen.run(cx, base, "∫", 22)
    pen.run(cx + 2, base + 16, "0", 8)
    pen.run(cx + 4, base - 14, "1", 8)
    cx += pen.width("∫", 22) + 6
    pen.line(cx, base, [
        ("x", 0, 13, 0), ("2", -6.5, 8.5, 4), ("dx", 0, 13, 6), ("=", 0, 13, 6),
        ("1", 0, 13, 0), ("/3", 0, 13, 0),
    ])

    # ---- 5. prose that must stay prose
    page.insert_text(
        (70, 500),
        "The area under the curve is large, and the students measure it with a ruler.",
        fontsize=11,
        fontname="helv",
    )
    page.insert_text((70, 520), "這一行是普通中文文字，不應該被當成數式。", fontsize=11, fontname="china-t")

    # ---- 6. a picture so figure cropping is still exercised
    page.draw_rect(fitz.Rect(70, 560, 250, 660), color=(0, 0, 0), width=1)
    page.draw_line(fitz.Point(70, 660), fitz.Point(250, 560), color=(0, 0, 0), width=1)
    page.insert_text((260, 610), "Figure 1", fontsize=9, fontname="helv")

    doc.save(path)
    doc.close()


def build_scan(src: str, path: str) -> None:
    """rasterise page 1 so the PDF has no text layer at all"""
    src_doc = fitz.open(src)
    out = fitz.open()
    pix = src_doc[0].get_pixmap(dpi=200)
    page = out.new_page(width=src_doc[0].rect.width, height=src_doc[0].rect.height)
    page.insert_image(page.rect, pixmap=pix)
    out.save(path)
    out.close()
    src_doc.close()


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    digital = os.path.join(OUT, "math_digital.pdf")
    scan = os.path.join(OUT, "math_scan.pdf")
    build_digital(digital, MATH_FONT)
    build_scan(digital, scan)
    for f in (digital, scan):
        print(f, os.path.getsize(f), "bytes")
