"""OCR engine wrapper.

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
