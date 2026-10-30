"""Formula image -> LaTeX engine wrapper (pix2tex / LaTeX-OCR).

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
