"""Text-heavy fixtures for measuring OCR accuracy (no maths, so a character
error rate is meaningful).

  text.pdf            digital original, real text layer = ground truth
  scan_text.pdf       the same page rasterised at 200 dpi, clean
  scan_text_photo.pdf the same page "photographed": skew, shadow, noise, JPEG

  python3 tests/make_text_sample.py
"""
from __future__ import annotations

import io
import os

import numpy as np
import pymupdf as fitz
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")
CJK = "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"

PARAS = [
    ("heading", "二零二五年度校務報告"),
    (
        "body",
        "本年度全校共有二十四班，學生人數一千二百三十六人。學校在課程發展方面，"
        "重點推動閱讀計劃與專題研習，並於下學期增設人工智能入門課程，反應熱烈。",
    ),
    (
        "body",
        "圖書館全年借閱量較去年上升百分之十八，其中科普類書籍最受歡迎。"
        "訓導組亦加强了班級經營的支援，學生違規個案較上學年減少。",
    ),
    (
        "body",
        "The school runs 24 classes with 1,236 students. Attendance stayed at 96.4% "
        "across the year, and the reading programme reached every form.",
    ),
    (
        "body",
        "各科測驗成績統計如下：中文科平均分 72.5，英文科 68.9，數學科 74.1。"
        "整體及格率為 93.2%，較去年提升 1.8 個百分點。",
    ),
]


def build_digital(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="cjk", fontfile=CJK)
    y = 80.0
    for style, text in PARAS:
        size = 17 if style == "heading" else 11
        rect = fitz.Rect(64, y, 531, y + 200)
        page.insert_textbox(
            rect,
            text,
            fontname="cjk",
            fontsize=size,
            lineheight=1.55,
            color=(0, 0, 0),
        )
        used = 40 if style == "heading" else (len(text) / 22 + 1) * size * 1.55
        y += used + 18
    doc.save(path)
    doc.close()


def degrade(img: Image.Image, seed: int = 11) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    a = a * (1.0 - 0.26 * (xx / w) - 0.08 * (yy / h)) + 16.0
    a = 118 + (a - 118) * 0.7
    a = a + rng.normal(0, 6.0, a.shape)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), mode="L")
    out = out.rotate(-1.25, resample=Image.BICUBIC, expand=False, fillcolor=232)
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=65)
    buf.seek(0)
    return Image.open(buf).convert("L")


def rasterise(src: str, target: str, dpi: int = 200, hard: bool = False) -> None:
    doc = fitz.open(src)
    pix = doc[0].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    doc.close()
    if hard:
        img = degrade(img)
    tmp = target + ".png"
    img.save(tmp)
    out = fitz.open()
    page = out.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=tmp)
    out.save(target, deflate=True)
    out.close()
    os.remove(tmp)
    print(f"{target}  ({os.path.getsize(target) / 1024:.0f} KB)")


def main() -> None:
    digital = os.path.join(OUT, "text.pdf")
    build_digital(digital)
    print(f"{digital}  ({os.path.getsize(digital) / 1024:.0f} KB)")
    rasterise(digital, os.path.join(OUT, "scan_text.pdf"))
    rasterise(digital, os.path.join(OUT, "scan_text_photo.pdf"), hard=True)


if __name__ == "__main__":
    main()
