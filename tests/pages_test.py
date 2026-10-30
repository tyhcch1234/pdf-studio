"""Check the static build the way GitHub Pages serves it (no backend).

    python3 -m http.server 8099 --directory web
    python3 tests/pages_test.py http://127.0.0.1:8099
"""
from __future__ import annotations

import os
import sys

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


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(1500)

        check("PDF Studio" in page.title(), "page loads from a static server")
        ocr = page.inner_text("#cap-ocr")
        check("未連線" in ocr, f"backend correctly reported as offline ({ocr})")
        check(page.locator("#api-panel").evaluate("el => el.open"), "settings panel opens automatically")

        page.screenshot(path=os.path.join(OUT, "pages_offline.png"), full_page=True)

        # merge must work with no backend at all
        page.click('.tab[data-panel="merge"]')
        page.set_input_files(
            "#input-merge",
            [os.path.join(SAMPLES, "digital.pdf"), os.path.join(SAMPLES, "a3_landscape.pdf")],
        )
        page.wait_for_timeout(1500)
        page.click("#btn-merge")
        page.wait_for_selector("#results-merge .result", timeout=60000)
        with page.expect_download(timeout=30000) as dl:
            page.locator("#results-merge .result .btn").first.click()
        merged_path = os.path.join(OUT, "pages_merged.pdf")
        dl.value.save_as(merged_path)
        doc = pymupdf.open(merged_path)
        check(doc.page_count == 2, f"merge works offline ({doc.page_count} pages)")

        # split must work too
        page.click('.tab[data-panel="split"]')
        page.set_input_files("#input-split", [os.path.join(SAMPLES, "a3_landscape.pdf")])
        page.wait_for_timeout(2500)
        check(page.locator("#previews-split .pv").count() == 1, "preview works offline")
        page.click("#btn-split")
        page.wait_for_selector("#results-split .result", timeout=60000)
        with page.expect_download(timeout=30000) as dl2:
            page.locator("#results-split .result .btn").first.click()
        split_path = os.path.join(OUT, "pages_split.pdf")
        dl2.value.save_as(split_path)
        sp = pymupdf.open(split_path)
        check(sp.page_count == 2 and abs(sp[0].rect.width - 595) < 3, "split works offline")

        # conversion must fail politely, not with a JS error
        page.click('.tab[data-panel="convert"]')
        page.set_input_files("#input-convert", [os.path.join(SAMPLES, "digital.pdf")])
        page.wait_for_timeout(300)
        page.click("#btn-convert")
        page.wait_for_timeout(2500)
        toast = page.locator(".toast").first
        check(toast.count() > 0 and "未連線" in toast.inner_text(), "conversion explains the missing backend")

        if errors:
            for e in errors[:5]:
                print("   js error:", e[:160])
        check(not errors, "no javascript errors")
        browser.close()

    print("\nFAILED:" if fails else "\nALL PASSED")
    for f in fails:
        print("  -", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
