"""Drive the tkinter app end to end (needs a display; Xvfb works).

    xvfb-run -a python3 tests/desktop_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import desktop  # noqa: E402
from tkinter import filedialog, messagebox  # noqa: E402

SAMPLES = os.path.join(HERE, "samples")
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def shot(app, name: str, tab: int) -> None:
    path = os.path.join(OUT, name)
    try:
        app.nb.select(tab)
        app.update()
        time.sleep(0.5)
        app.update()
        subprocess.run(["scrot", "-o", path], check=False, timeout=20)
    except Exception as exc:  # screenshots are informational only
        print("   screenshot skipped:", exc)
    return path


def pump(app, predicate, timeout=180.0):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if predicate():
            return True
        time.sleep(0.08)
    return False


def main() -> int:
    # keep the GUI headless-safe
    answers = {"dir": OUT, "save": os.path.join(OUT, "gui_merged.pdf")}
    desktop.filedialog.askdirectory = lambda **kw: answers["dir"]
    desktop.filedialog.asksaveasfilename = lambda **kw: answers["save"]
    desktop.filedialog.askopenfilenames = lambda **kw: tuple()
    desktop.messagebox.showinfo = lambda *a, **k: None
    desktop.messagebox.showerror = lambda *a, **k: print("   dialog error:", a[:2])
    desktop.messagebox.askyesno = lambda *a, **k: True

    app = desktop.App()
    app.geometry("980x760+0+0")
    app.update()
    check(app.winfo_exists() == 1, "window created")

    # ---------- 1. convert ----------
    app.convert.files.paths = [os.path.join(SAMPLES, "digital.pdf")]
    app.convert.files.refresh()
    app.update()
    shot(app, "desktop_convert.png", 0)

    app.convert.start()
    print("   converting…", flush=True)
    ok = pump(app, lambda: not app.convert.running, timeout=180)
    check(ok, "conversion finished")
    if not ok:
        print("   running flag stuck:", app.convert.running)
        print("   log:", " | ".join(app.convert.log.get(0, "end"))[:400])
    produced = os.path.join(OUT, "digital.docx")
    check(os.path.exists(produced), f"docx written ({os.path.basename(produced)})")
    if os.path.exists(produced):
        import docx

        d = docx.Document(produced)
        text = "\n".join(p.text for p in d.paragraphs)
        check("Measurement Report" in text, "text is editable in Word")
        check(len(d.inline_shapes) >= 1, "picture kept")
    shot(app, "desktop_convert_done.png", 0)

    # ---------- 2. merge ----------
    app.merge.files.paths = [
        os.path.join(SAMPLES, "digital.pdf"),
        os.path.join(SAMPLES, "a3_landscape.pdf"),
    ]
    app.merge.files.refresh()
    app.update()
    shot(app, "desktop_merge.png", 1)
    answers["save"] = os.path.join(OUT, "gui_merged.pdf")
    app.merge.start()
    app.update()
    merged = pymupdf.open(answers["save"])
    check(merged.page_count == 2, f"merged pages = {merged.page_count}")
    merged.close()

    # ---------- 3. split ----------
    app.split.files.paths = [os.path.join(SAMPLES, "a3_landscape.pdf")]
    app.split.files.refresh()
    app.update()
    app.split.show_preview()
    app.update()
    check(app.split.preview_img is not None, "preview rendered")
    shot(app, "desktop_split.png", 2)

    answers["save"] = os.path.join(OUT, "gui_split.pdf")
    app.split.start()
    app.update()
    out = pymupdf.open(answers["save"])
    check(out.page_count == 2, f"split pages = {out.page_count}")
    check(abs(out[0].rect.width - 595.28) < 3, f"A4 width {out[0].rect.width:.0f}")
    check("LEFT" in out[0].get_text(), "first page is the left half")
    out.close()

    app.destroy()

    print("\nFAILED:" if fails else "\nALL PASSED")
    for f in fails:
        print("  -", f)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
