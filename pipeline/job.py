"""Convert one PDF and stream progress as JSON lines.

Run as a child process by the desktop GUI so the OCR / formula engines (and
their native thread pools) never live inside the Tk process:

    python3 -m pipeline.job in.pdf out.docx --ocr auto --formulas auto --dpi 200
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDF -> DOCX converter")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--ocr", default="auto", choices=["auto", "always", "never"])
    ap.add_argument("--formulas", default="auto", choices=["auto", "off"])
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args(argv)

    # keep the child quiet: the GUI shows its own errors
    os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")
    try:
        from . import ConvertOptions, convert_pdf
    except Exception as exc:  # pragma: no cover
        _emit({"t": "error", "msg": f"載入轉檔模組失敗：{exc}"})
        return 2

    opts = ConvertOptions(
        ocr=args.ocr,
        formulas=args.formulas,
        dpi=args.dpi,
        images=not args.no_images,
    )

    def progress(done: int, total: int, msg: str) -> None:
        _emit({"t": "progress", "done": done, "total": total, "msg": msg})

    try:
        stats = convert_pdf(
            args.src, args.dst, opts, progress=progress,
            title=os.path.splitext(os.path.basename(args.src))[0],
        )
        _emit({"t": "done", "stats": stats, "dst": args.dst})
        return 0
    except Exception as exc:  # pragma: no cover - reported to the GUI
        _emit({"t": "error", "msg": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
