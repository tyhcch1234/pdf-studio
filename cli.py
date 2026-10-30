#!/usr/bin/env python3
"""PDF Studio 命令列版（與桌面版共用同一套轉檔程式）。

用法：
  python3 cli.py info
  python3 cli.py word  a.pdf [b.pdf ...] [-o 輸出資料夾] [--ocr auto|always|never]
                        [--formulas on|off] [--dpi 200] [--no-images]
  python3 cli.py merge 輸出.pdf a.pdf b.pdf ... [--pages 1-3,5]
  python3 cli.py split a3.pdf [more.pdf ...] [-o 輸出資料夾]
                        [--axis auto|vertical|horizontal] [--order normal|reverse]
                        [--all-pages]

範例：
  python3 cli.py word report.pdf --ocr auto --formulas on
  python3 cli.py merge all.pdf 一.pdf 二.pdf 三.pdf --pages 1-5
  python3 cli.py split 海報.pdf --axis vertical
"""
from __future__ import annotations

import argparse
import os
import sys

from pipeline import ConvertOptions, capabilities, convert_pdf
from pipeline.ops import merge_pdfs, split_a3


def cmd_info(_args) -> int:
    caps = capabilities()
    print("OCR 引擎：", "就緒" if caps["ocr"] else f"未安裝（{caps.get('ocr_error')}）")
    print("數式引擎：", "就緒" if caps["formula"] else f"未安裝（{caps.get('formula_error')}）")
    return 0


def cmd_word(args) -> int:
    out_dir = args.out or None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    opts = ConvertOptions(
        ocr=args.ocr,
        formulas="auto" if args.formulas == "on" else "off",
        dpi=args.dpi,
        images=not args.no_images,
    )
    failed = 0
    for i, src in enumerate(args.files, 1):
        name = os.path.basename(src)
        target = out_dir or os.path.dirname(os.path.abspath(src))
        stem = os.path.splitext(name)[0]
        dst = os.path.join(target, f"{stem}.docx")
        n = 2
        while os.path.exists(dst):
            dst = os.path.join(target, f"{stem} ({n}).docx")
            n += 1
        print(f"[{i}/{len(args.files)}] {name}", flush=True)
        try:
            stats = convert_pdf(
                src, dst, opts,
                progress=lambda d, t, m: print(f"    {m}", flush=True),
                title=stem,
            )
        except Exception as exc:
            failed += 1
            print(f"    失敗：{type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        bits = [f"{stats['pages']} 頁", f"{stats['text_lines']} 段文字"]
        if stats["ocr_pages"]:
            bits.append(f"OCR {stats['ocr_pages']} 頁")
        if stats["images"]:
            bits.append(f"{stats['images']} 張圖片")
        if stats["equations"]:
            bits.append(f"{stats['equations']} 條方程式")
        if stats["equations_as_image"]:
            bits.append(f"{stats['equations_as_image']} 條數式保留為圖片")
        print("    → " + "、".join(bits))
        print(f"    → {dst}")
    return 1 if failed else 0


def cmd_merge(args) -> int:
    ranges = [args.pages or "" for _ in args.files]
    try:
        result = merge_pdfs(args.files, args.out, ranges)
    except Exception as exc:
        print(f"合併失敗：{exc}", file=sys.stderr)
        return 1
    for path, pages in result["files"]:
        print(f"    {os.path.basename(path)}：{pages} 頁")
    print(f"→ {args.out}（共 {result['pages']} 頁）")
    return 0


def cmd_split(args) -> int:
    out_dir = args.out or None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    failed = 0
    for src in args.files:
        target = out_dir or os.path.dirname(os.path.abspath(src))
        stem = os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(target, f"{stem}-A4.pdf")
        try:
            r = split_a3(src, dst, axis=args.axis, order=args.order,
                         only_a3=not args.all_pages)
        except Exception as exc:
            failed += 1
            print(f"{os.path.basename(src)} 失敗：{exc}", file=sys.stderr)
            continue
        extra = f"，{r['kept']} 頁保留原樣" if r["kept"] else ""
        print(f"{os.path.basename(src)} → {r['produced']} 張 A4{extra}\n    → {dst}")
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pdf-studio", description="PDF 轉 Word／合併／A3 轉 A4（本機執行）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="顯示 OCR／數式引擎狀態").set_defaults(func=cmd_info)

    p = sub.add_parser("word", help="PDF → Word")
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--out", help="輸出資料夾（預設與原檔同目錄）")
    p.add_argument("--ocr", default="auto", choices=["auto", "always", "never"])
    p.add_argument("--formulas", default="on", choices=["on", "off"])
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--no-images", action="store_true")
    p.set_defaults(func=cmd_word)

    p = sub.add_parser("merge", help="合併 PDF")
    p.add_argument("out", help="輸出檔名")
    p.add_argument("files", nargs="+")
    p.add_argument("--pages", help="頁碼範圍，例如 1-3,5（套用每個輸入檔）")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("split", help="A3 → A4")
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--out")
    p.add_argument("--axis", default="auto", choices=["auto", "vertical", "horizontal"])
    p.add_argument("--order", default="normal", choices=["normal", "reverse"])
    p.add_argument("--all-pages", action="store_true", help="非 A3 頁面也照切")
    p.set_defaults(func=cmd_split)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
