#!/usr/bin/env python3
"""把 pipeline/ 打包成單一檔案 pdf_studio.py（方便下載執行）。

    python3 build_single.py

ocr.py / formula.py 以「內嵌原始碼 + exec 成模組物件」的方式併入，
避免兩個模組的同名函式（available 等）在合併後互相覆蓋。
"""
from __future__ import annotations

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

PLAIN = [  # 直接展開的模組（彼此沒有同名衝突）
    "pipeline/layout.py",
    "pipeline/omml.py",
    "pipeline/extract.py",
    "pipeline/writer.py",
    "pipeline/ops.py",
    "pipeline/__init__.py",
]
EMBEDDED = [  # 以模組物件方式併入
    "pipeline/ocr.py",
    "pipeline/formula.py",
]

HEADER = '''#!/usr/bin/env python3
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
'''


def strip_module(path: str) -> str:
    src = open(path, encoding="utf-8").read()
    out, skip_paren = [], False
    for line in src.splitlines():
        if skip_paren:
            if line.startswith(")"):
                skip_paren = False
            continue
        if line.startswith("from .") and line.rstrip().endswith("("):
            skip_paren = True
            continue
        if line.startswith("from .") or line.startswith("from __future__"):
            continue
        out.append(line)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")
    name = os.path.basename(path)
    return f"# ==== {name} " + "=" * max(0, 58 - len(name)) + f"\n{body}\n"


def embed_module(path: str, var: str) -> str:
    src = open(path, encoding="utf-8").read()
    if "'''" in src:
        raise SystemExit(f"{path} 內含 ''' ，無法安全內嵌")
    name = os.path.basename(path)[:-3]
    return (
        f"# ==== {name}.py (embedded as a module object) " + "=" * 30 + "\n"
        f"_{var}_src = r'''{src}'''\n"
        f"{var} = _types.ModuleType({name!r})\n"
        f"exec(compile(_{var}_src, {name + '.py'!r}, 'exec'), {var}.__dict__)\n"
    )


CAPS = '''

def capabilities() -> dict:
    """這台電腦上可用的引擎。"""
    return {
        "ocr": ocr.available(),
        "ocr_error": ocr.engine_error(),
        "formula": formula.available(),
    }
'''


def build(out_path: str = "pdf_studio.py") -> str:
    parts = [HEADER, "import types as _types\n"]
    for path in PLAIN:
        parts.append(strip_module(os.path.join(HERE, path)))
        if path.endswith("extract.py"):
            for mod in EMBEDDED:
                var = os.path.basename(mod)[:-3]
                parts.append(embed_module(os.path.join(HERE, mod), var))
            pass  # capabilities() comes from pipeline/__init__.py
    tail = open(os.path.join(HERE, "single_gui.py"), encoding="utf-8").read()
    parts.append("\n# ==== GUI / CLI " + "=" * 46 + f"\n{tail}")
    text = "\n".join(parts)
    compile(text, out_path, "exec")
    with open(os.path.join(HERE, out_path), "w", encoding="utf-8") as fh:
        fh.write(text)
    return os.path.join(HERE, out_path)


if __name__ == "__main__":
    path = build()
    lines = sum(1 for _ in open(path, encoding="utf-8"))
    print(f"built {path} ({lines} lines, {os.path.getsize(path) // 1024} KB)")
