"""Exercise the no-server pipeline: OCR + Word generation inside the browser.

    python3 -m http.server 8099 --directory web
    python3 tests/browser_engine_test.py http://127.0.0.1:8099
"""
from __future__ import annotations

import os
import sys

import docx
import pymupdf
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099"
HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def convert(page, path: str, out_name: str) -> str:
    if page.locator("#btn-clear-convert").is_enabled():
        page.click("#btn-clear-convert")
        page.wait_for_timeout(200)
    page.set_input_files("#input-convert", [path])
    page.wait_for_timeout(300)
    page.click("#btn-convert")
    page.wait_for_selector("#results-convert .result", timeout=300000)
    page.wait_for_timeout(600)
    card = page.locator("#results-convert .result").first
    with page.expect_download(timeout=60000) as dl:
        card.locator(".btn").first.click()
    dest = os.path.join(OUT, out_name)
    dl.value.save_as(dest)
    return dest


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)

        note = page.inner_text("#engine-note")
        check("瀏覽器內建" in note, f"engine switched to in-browser mode ({note[:40]})")

        # ---- digital PDF: text layer + figures ----
        p = convert(page, os.path.join(SAMPLES, "digital.pdf"), "be_digital.docx")
        d = docx.Document(p)
        text = "\n".join(x.text for x in d.paragraphs)
        check("Measurement Report" in text, "digital text extracted")
        check("tolerance band" in text, "second paragraph extracted")
        check(len(d.inline_shapes) >= 1, f"figure kept ({len(d.inline_shapes)} pictures)")
        check(elems_ordered(d), "paragraph order follows the page")

        # ---- scanned PDF: OCR ----
        p2 = convert(page, os.path.join(SAMPLES, "scanned.pdf"), "be_scanned.docx")
        d2 = docx.Document(p2)
        text2 = "\n".join(x.text for x in d2.paragraphs)
        print("   OCR text:", repr(text2[:120]))
        check("Invoice" in text2, "OCR recognised the heading")
        check("2026" in text2, "OCR recognised numbers")
        check(len(text2) > 30, "OCR produced a usable amount of text")

        # ---- two column Chinese: order ----
        p3 = convert(page, os.path.join(SAMPLES, "chinese_2col.pdf"), "be_cn.docx")
        d3 = docx.Document(p3)
        text3 = "\n".join(x.text for x in d3.paragraphs)
        left = text3.find("本實驗針對")
        right = text3.find("第二階段")
        check(left >= 0 and right >= 0, "Chinese text extracted")
        check(0 <= left < right, "left column comes before right column")

        page.screenshot(path=os.path.join(OUT, "browser_engine.png"), full_page=True)

        if errors:
            for e in errors[:5]:
                print("   js error:", e[:200])
        check(not errors, "no javascript errors")
        browser.close()

    print("\nFAILED:" if fails else "\nALL PASSED")
    for f in fails:
        print("  -", f)
    return 1 if fails else 0


def elems_ordered(d) -> bool:
    """First body paragraph must come before the figure caption."""
    texts = [p.text for p in d.paragraphs if p.text.strip()]
    if not texts:
        return False
    return texts[0].startswith("Measurement")


if __name__ == "__main__":
    raise SystemExit(main())
