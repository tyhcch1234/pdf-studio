"""Build a "hard" scan fixture: the digital page photographed badly.

Adds the things real photocopies and phone photos have and clean raster
samples do not: a slight rotation, an uneven shadow, low contrast, sensor
noise and JPEG artefacts. Used to check that the deskew / contrast steps in
web/js/ocr-engine.js actually earn their keep.

  python3 tests/make_scan_sample.py            # -> tests/samples/scan_photo.pdf
"""
from __future__ import annotations

import io
import os

import numpy as np
import pymupdf as fitz
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")
SRC = os.path.join(OUT, "math_digital.pdf")


def render(src: str, dpi: int = 200) -> Image.Image:
    doc = fitz.open(src)
    page = doc[0]
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def degrade(img: Image.Image, seed: int = 7) -> Image.Image:
    rng = np.random.default_rng(seed)
    a = np.asarray(img).astype(np.float32)

    # 1. uneven illumination: a diagonal shadow plus a weak vignette
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    shade = 1.0 - 0.28 * (xx / w) - 0.10 * (yy / h)
    a = a * shade + 18.0  # lift the blacks: washed-out photocopy

    # 2. contrast compression
    a = 120 + (a - 120) * 0.72

    # 3. sensor noise
    a = a + rng.normal(0, 6.5, a.shape)

    a = np.clip(a, 0, 255).astype(np.uint8)
    out = Image.fromarray(a, mode="L")

    # 4. a phone held slightly crooked
    out = out.rotate(-1.3, resample=Image.BICUBIC, expand=False, fillcolor=235)

    # 5. JPEG artefacts from a phone camera
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=62)
    buf.seek(0)
    return Image.open(buf).convert("L")


def main() -> None:
    img = degrade(render(SRC))
    tmp = os.path.join(OUT, "_scan_photo.png")
    img.save(tmp)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=tmp)
    target = os.path.join(OUT, "scan_photo.pdf")
    doc.save(target, deflate=True)
    doc.close()
    os.remove(tmp)
    print(f"{target}  ({os.path.getsize(target) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
