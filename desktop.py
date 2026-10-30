#!/usr/bin/env python3
"""PDF Studio - 本機桌面版（tkinter）。

三個功能都在自己的電腦上執行，不需要瀏覽器、不需要任何伺服器：
  1. PDF → Word（可編輯文字、保留圖片、數式轉 Word 方程式）
  2. 合併 PDF
  3. A3 沿中線切成 A4

執行：python3 desktop.py
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

# make `import pipeline` work no matter where the script is launched from
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pymupdf

from pipeline import ConvertOptions  # light import: no OCR models in the GUI process
from pipeline.ops import split_a3, merge_pdfs, is_a3
APP_TITLE = "PDF Studio"
PAD = 12


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def pick_font(root: tk.Tk, candidates: list[tuple[str, int]]) -> tuple[str, int]:
    families = set(tkfont.families(root))
    for name, size in candidates:
        if name in families:
            return name, size
    return candidates[-1]


def probe_capabilities() -> dict:
    """Ask a short-lived child process which engines are installed."""
    code = "import json;from pipeline import capabilities;print(json.dumps(capabilities()))"
    try:
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=180,
            env={**os.environ, "NO_ALBUMENTATIONS_UPDATE": "1"},
        )
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {"ocr": False, "formula": False, "error": str(exc)}


def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit != "B" else f"{num} B"
        num /= 1024.0
    return f"{num:.0f} GB"


class FileList(ttk.Frame):
    """A listbox plus add / remove / reorder buttons."""

    def __init__(self, master, on_change=None, height=6):
        super().__init__(master)
        self.paths: list[str] = []
        self.on_change = on_change

        self.listbox = tk.Listbox(
            self, height=height, activestyle="none", selectmode="extended",
            borderwidth=1, relief="solid", highlightthickness=0,
        )
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        side = ttk.Frame(self)
        side.grid(row=0, column=2, sticky="ns", padx=(8, 0))
        self.btn_add = ttk.Button(side, text="加入 PDF…", command=self.add)
        self.btn_add.pack(fill="x")
        self.btn_del = ttk.Button(side, text="移除", command=self.remove)
        self.btn_del.pack(fill="x", pady=(6, 0))
        self.btn_up = ttk.Button(side, text="上移", command=lambda: self.move(-1))
        self.btn_up.pack(fill="x", pady=(6, 0))
        self.btn_down = ttk.Button(side, text="下移", command=lambda: self.move(1))
        self.btn_down.pack(fill="x", pady=(6, 0))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

    # -- data ---------------------------------------------------------------
    def add(self, paths=None):
        paths = paths or filedialog.askopenfilenames(
            title="選擇 PDF", filetypes=[("PDF 檔案", "*.pdf"), ("所有檔案", "*.*")]
        )
        added = False
        for path in paths:
            path = os.path.abspath(path)
            if not path.lower().endswith(".pdf"):
                continue
            if path in self.paths:
                continue
            self.paths.append(path)
            added = True
        if added:
            self.refresh()
        return added

    def remove(self):
        for idx in sorted(self.listbox.curselection(), reverse=True):
            del self.paths[idx]
        self.refresh()

    def move(self, delta: int):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        new = idx + delta
        if 0 <= new < len(self.paths):
            self.paths[idx], self.paths[new] = self.paths[new], self.paths[idx]
            self.refresh()
            self.listbox.selection_set(new)

    def refresh(self):
        self.listbox.delete(0, tk.END)
        for i, path in enumerate(self.paths):
            try:
                size = human_size(os.path.getsize(path))
            except OSError:
                size = "?"
            self.listbox.insert(tk.END, f"{i + 1}.  {os.path.basename(path)}   ({size})")
        if self.on_change:
            self.on_change()

    def clear(self):
        self.paths.clear()
        self.refresh()


# --------------------------------------------------------------------------- #
# tab 1: PDF -> Word
# --------------------------------------------------------------------------- #
class ConvertTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=PAD)
        self.app = app
        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.done_files: list[str] = []

        ttk.Label(self, text="將 PDF 轉成可編輯的 Word", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            self,
            text="文字轉成 Word 文字、圖片原樣保留；數式會轉成 Word 方程式（可在方程式編輯器修改）。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        self.files = FileList(self, on_change=self.update_state)
        self.files.pack(fill="both", expand=True)

        opts = ttk.LabelFrame(self, text="選項", padding=10)
        opts.pack(fill="x", pady=(12, 0))
        for col in range(3):
            opts.columnconfigure(col, weight=1)

        self.ocr_mode = tk.StringVar(value="auto")
        self.formula_mode = tk.StringVar(value="on")
        self.dpi = tk.StringVar(value="200")
        self.keep_images = tk.BooleanVar(value=True)

        self._combo(opts, 0, "OCR 模式", self.ocr_mode,
                    [("自動（掃描頁才 OCR）", "auto"), ("一律 OCR", "always"), ("關閉", "never")])
        self._combo(opts, 1, "數式辨識", self.formula_mode,
                    [("開啟（轉成 Word 方程式）", "on"), ("關閉", "off")])
        self._combo(opts, 2, "圖片解析度", self.dpi,
                    [("150 dpi（小檔）", "150"), ("200 dpi（建議）", "200"), ("300 dpi（清晰）", "300")])

        ttk.Checkbutton(opts, text="保留圖片與圖表", variable=self.keep_images).grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 0))
        self.btn_run = ttk.Button(actions, text="開始轉換", style="Accent.TButton", command=self.start)
        self.btn_run.pack(side="left")
        ttk.Button(actions, text="清空", command=self.clear).pack(side="left", padx=(8, 0))
        self.status = ttk.Label(actions, text="", style="Hint.TLabel")
        self.status.pack(side="right")

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(10, 0))

        self.log = tk.Listbox(self, height=6, relief="solid", borderwidth=1,
                              highlightthickness=0, activestyle="none")
        self.log.pack(fill="both", pady=(10, 0))

        self.after(120, self.poll)

    def _combo(self, parent, col, label, var, items):
        frame = ttk.Frame(parent)
        frame.grid(row=0, column=col, sticky="ew", padx=(0, 14))
        ttk.Label(frame, text=label).pack(anchor="w")
        box = ttk.Combobox(frame, state="readonly", values=[i[0] for i in items])
        box.current(0)
        box.pack(fill="x", pady=(4, 0))
        var.trace_add("write", lambda *_: None)
        box.bind("<<ComboboxSelected>>", lambda e, v=var, i=items, b=box: v.set(i[b.current()][1]))
        var.set(items[0][1])

    # -- actions ------------------------------------------------------------
    def clear(self):
        self.files.clear()
        self.set_log("")
        self.progress["value"] = 0
        self.status.configure(text="")

    def update_state(self):
        self.status.configure(text=f"{len(self.files.paths)} 個檔案" if self.files.paths else "")
        if not self.running:
            self.btn_run.state(["!disabled"] if self.files.paths else ["disabled"])

    def set_log(self, text: str):
        self.log.delete(0, tk.END)
        for line in text.splitlines():
            if line:
                self.log.insert(tk.END, line)

    def append_log(self, line: str):
        self.log.insert(tk.END, line)
        self.log.see(tk.END)

    def start(self):
        if self.running or not self.files.paths:
            return
        out_dir = filedialog.askdirectory(title="選擇輸出資料夾（取消則存在原檔旁）")
        self.running = True
        self.btn_run.state(["disabled"])
        self.progress["value"] = 0
        self.set_log("")
        opts = ConvertOptions(
            ocr=self.ocr_mode.get(),
            formulas="auto" if self.formula_mode.get() == "on" else "off",
            dpi=int(self.dpi.get()),
            images=self.keep_images.get(),
        )
        threading.Thread(
            target=self._worker, args=(list(self.files.paths), out_dir, opts), daemon=True
        ).start()

    def _worker(self, paths, out_dir, opts):
        total = len(paths)
        env = {**os.environ, "NO_ALBUMENTATIONS_UPDATE": "1", "PYTHONUNBUFFERED": "1"}
        for i, src in enumerate(paths):
            name = os.path.basename(src)
            self.q.put(("log", f"[{i + 1}/{total}] {name}"))
            target_dir = out_dir or os.path.dirname(src)
            stem = os.path.splitext(name)[0]
            dst = os.path.join(target_dir, f"{stem}.docx")
            n = 2
            while os.path.exists(dst):
                dst = os.path.join(target_dir, f"{stem} ({n}).docx")
                n += 1

            cmd = [
                sys.executable, "-m", "pipeline.job", src, dst,
                "--ocr", opts.ocr,
                "--formulas", opts.formulas,
                "--dpi", str(opts.dpi),
            ]
            if not opts.images:
                cmd.append("--no-images")

            try:
                proc = subprocess.Popen(
                    cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1, env=env,
                )
            except Exception as exc:
                self.q.put(("log", f"   無法啟動轉檔程式：{exc}"))
                continue

            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("t") == "progress":
                    done = msg.get("done", 0)
                    count = max(1, msg.get("total", 1))
                    self.q.put(("progress", (i + done / count) / total * 100, msg.get("msg", "")))
                elif msg.get("t") == "done":
                    stats = msg["stats"]
                    bits = [f"{stats['pages']} 頁", f"{stats['text_lines']} 段文字"]
                    if stats.get("ocr_pages"):
                        bits.append(f"OCR {stats['ocr_pages']} 頁")
                    if stats.get("images"):
                        bits.append(f"{stats['images']} 張圖片")
                    if stats.get("equations"):
                        bits.append(f"{stats['equations']} 條方程式")
                    if stats.get("equations_as_image"):
                        bits.append(f"{stats['equations_as_image']} 條數式保留為圖片")
                    self.q.put(("log", "   完成：" + "、".join(bits)))
                    self.q.put(("log", f"   → {dst}"))
                    self.q.put(("done", dst))
                    for warn in (stats.get("warnings") or [])[:3]:
                        self.q.put(("log", f"   注意：{warn}"))
                elif msg.get("t") == "error":
                    self.q.put(("log", f"   失敗：{msg.get('msg')}"))

            code = proc.wait()
            err = (proc.stderr.read() or "").strip().splitlines()[-3:] if proc.stderr else []
            if code != 0:
                self.q.put(("log", f"   轉檔程式結束碼 {code}"))
                for line in err:
                    self.q.put(("log", f"   {line}"))
        self.q.put(("finish", None))

    def poll(self):
        try:
            while True:
                kind, *payload = self.q.get_nowait()
                if kind == "log":
                    self.append_log(payload[0])
                elif kind == "progress":
                    self.progress["value"] = payload[0]
                    self.status.configure(text=payload[1])
                elif kind == "done":
                    self.done_files.append(payload[0])
                elif kind == "finish":
                    self.running = False
                    self.progress["value"] = 100
                    self.status.configure(text=f"完成 {len(self.done_files)} 個檔案")
                    self.update_state()
                    if self.done_files:
                        messagebox.showinfo(
                            APP_TITLE, f"完成 {len(self.done_files)} 個檔案。\n\n"
                                       f"最後一個：\n{self.done_files[-1]}"
                        )
                    self.done_files.clear()
        except queue.Empty:
            pass
        self.after(120, self.poll)


# --------------------------------------------------------------------------- #
# tab 2: merge
# --------------------------------------------------------------------------- #
class MergeTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=PAD)
        self.app = app

        ttk.Label(self, text="合併多個 PDF", style="H1.TLabel").pack(anchor="w")
        ttk.Label(self, text="依清單順序合併，可用「上移／下移」調整。",
                  style="Hint.TLabel").pack(anchor="w", pady=(2, 10))

        self.files = FileList(self, on_change=self.update_state)
        self.files.pack(fill="both", expand=True)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(10, 0))
        ttk.Label(row, text="頁碼範圍（套用選取的檔案，例如 1-3,5；留空＝全部）").pack(anchor="w")
        self.range_var = tk.StringVar()
        self.range_entry = tk.Entry(row, textvariable=self.range_var, relief="solid", bd=1)
        self.range_entry.pack(fill="x", pady=(4, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 0))
        self.btn_run = ttk.Button(actions, text="合併並另存…", style="Accent.TButton", command=self.start)
        self.btn_run.pack(side="left")
        self.status = ttk.Label(actions, text="", style="Hint.TLabel")
        self.status.pack(side="right")

    def update_state(self):
        n = len(self.files.paths)
        self.status.configure(text=f"{n} 個檔案" if n else "")
        self.btn_run.state(["!disabled"] if n else ["disabled"])

    def start(self):
        if not self.files.paths:
            return
        out = filedialog.asksaveasfilename(
            title="合併後另存", defaultextension=".pdf",
            initialfile="merged.pdf", filetypes=[("PDF 檔案", "*.pdf")],
        )
        if not out:
            return
        ranges = ["" for _ in self.files.paths]
        sel = self.files.listbox.curselection()
        if sel and self.range_var.get().strip():
            ranges[sel[0]] = self.range_var.get().strip()
        try:
            result = merge_pdfs(self.files.paths, out, ranges)
            messagebox.showinfo(APP_TITLE, f"已合併 {result['pages']} 頁。\n\n{out}")
            self.status.configure(text=f"完成 {result['pages']} 頁")
            self.app.offer_open(out)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"合併失敗：{exc}")


# --------------------------------------------------------------------------- #
# tab 3: A3 -> A4
# --------------------------------------------------------------------------- #
class SplitTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=PAD)
        self.app = app
        self.preview_img = None

        ttk.Label(self, text="A3 沿中線切成 A4", style="H1.TLabel").pack(anchor="w")
        ttk.Label(self, text="橫向 A3 切成左右兩張 A4；直向 A3 切成上下兩張 A4。",
                  style="Hint.TLabel").pack(anchor="w", pady=(2, 10))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self.files = FileList(body, on_change=self.update_state, height=5)
        self.files.pack(side="left", fill="both", expand=True)
        self.files.listbox.bind("<<ListboxSelect>>", lambda e: self.show_preview())

        self.preview = ttk.LabelFrame(body, text="預覽", padding=8)
        self.preview.pack(side="left", fill="y", padx=(12, 0))
        self.preview_canvas = tk.Label(self.preview, text="選擇檔案後顯示", width=24, height=12,
                                       background="#f7f8fb", foreground="#8b93a7")
        self.preview_canvas.pack()
        self.preview_info = ttk.Label(self.preview, text="", style="Hint.TLabel", wraplength=180)
        self.preview_info.pack(pady=(6, 0))

        opts = ttk.LabelFrame(self, text="選項", padding=10)
        opts.pack(fill="x", pady=(12, 0))
        self.axis = tk.StringVar(value="auto")
        self.order = tk.StringVar(value="normal")
        self.only_a3 = tk.BooleanVar(value=True)

        f1 = ttk.Frame(opts); f1.grid(row=0, column=0, sticky="ew", padx=(0, 16))
        ttk.Label(f1, text="切割方向").pack(anchor="w")
        ttk.Combobox(f1, state="readonly", width=22,
                     values=["自動（依頁面方向）", "垂直切（左/右）", "水平切（上/下）"]).pack(anchor="w")
        self.axis_combo = f1.winfo_children()[-1]
        self.axis_combo.current(0)
        self.axis_combo.bind("<<ComboboxSelected>>",
                             lambda e: (self.axis.set(["auto", "vertical", "horizontal"][self.axis_combo.current()]),
                                        self.show_preview()))

        f2 = ttk.Frame(opts); f2.grid(row=0, column=1, sticky="ew", padx=(0, 16))
        ttk.Label(f2, text="輸出順序").pack(anchor="w")
        self.order_combo = ttk.Combobox(f2, state="readonly", width=20,
                                        values=["左→右 / 上→下", "右→左 / 下→上"])
        self.order_combo.current(0)
        self.order_combo.pack(anchor="w")
        self.order_combo.bind("<<ComboboxSelected>>",
                              lambda e: self.order.set(["normal", "reverse"][self.order_combo.current()]))

        ttk.Checkbutton(opts, text="只切 A3 尺寸頁面（其他頁原樣保留）",
                        variable=self.only_a3).grid(row=0, column=2, sticky="w", pady=(18, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(12, 0))
        self.btn_run = ttk.Button(actions, text="切成 A4 並另存…", style="Accent.TButton", command=self.start)
        self.btn_run.pack(side="left")
        self.status = ttk.Label(actions, text="", style="Hint.TLabel")
        self.status.pack(side="right")

    def update_state(self):
        n = len(self.files.paths)
        self.status.configure(text=f"{n} 個檔案" if n else "")
        self.btn_run.state(["!disabled"] if n else ["disabled"])
        if n and not self.files.listbox.curselection():
            self.files.listbox.selection_set(0)
            self.show_preview()

    def show_preview(self):
        sel = self.files.listbox.curselection()
        if not sel:
            return
        path = self.files.paths[sel[0]]
        try:
            doc = pymupdf.open(path)
            page = doc[0]
            w, h = page.rect.width, page.rect.height
            target = 200.0
            dpi = max(60, int(target / max(1.0, h) * 72))
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            self.preview_img = tk.PhotoImage(data=pix.tobytes("png"))
            self.preview_canvas.configure(image=self.preview_img, text="", width=0, height=0,
                                          background="#ffffff")
            a3 = is_a3(w, h)
            direction = self.axis.get()
            if direction == "auto":
                direction = "vertical" if w >= h else "horizontal"
            label = "垂直切（左/右）" if direction == "vertical" else "水平切（上/下）"
            self.preview_info.configure(
                text=f"{doc.page_count} 頁 · {w:.0f}×{h:.0f} pt\n"
                     f"{'A3' if a3 else '非 A3'}｜{label}"
                     + ("" if a3 or not self.only_a3.get() else "\n（非 A3，會被保留）")
            )
            doc.close()
        except Exception as exc:
            self.preview_info.configure(text=f"無法預覽：{exc}")

    def start(self):
        if not self.files.paths:
            return
        path = self.files.paths[0] if len(self.files.paths) == 1 else None
        if len(self.files.paths) > 1:
            if not messagebox.askyesno(APP_TITLE, f"將分別處理 {len(self.files.paths)} 個檔案，輸出到各自資料夾。繼續？"):
                return
        if len(self.files.paths) == 1:
            out = filedialog.asksaveasfilename(
                title="另存 A4 PDF", defaultextension=".pdf",
                initialfile=os.path.splitext(os.path.basename(self.files.paths[0]))[0] + "-A4.pdf",
                filetypes=[("PDF 檔案", "*.pdf")],
            )
            if not out:
                return
            targets = [(self.files.paths[0], out)]
        else:
            targets = []
            for src in self.files.paths:
                stem = os.path.splitext(os.path.basename(src))[0]
                targets.append((src, os.path.join(os.path.dirname(src), f"{stem}-A4.pdf")))

        done = []
        try:
            for src, out in targets:
                r = split_a3(src, out, axis=self.axis.get(), order=self.order.get(),
                             only_a3=self.only_a3.get())
                done.append((out, r))
            msg = "\n".join(
                f"{os.path.basename(o)}：{r['produced']} 張 A4" + (f"（{r['kept']} 頁保留）" if r["kept"] else "")
                for o, r in done
            )
            messagebox.showinfo(APP_TITLE, "完成\n\n" + msg)
            self.status.configure(text=f"完成 {sum(r['produced'] for _, r in done)} 張 A4")
            self.app.offer_open(done[0][0])
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"切割失敗：{exc}")


# --------------------------------------------------------------------------- #
# app shell
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x720")
        self.minsize(880, 640)

        family, size = pick_font(self, [
            ("Microsoft JhengHei UI", 10), ("PingFang TC", 12),
            ("Noto Sans CJK TC", 11), ("Noto Sans CJK SC", 11), ("DejaVu Sans", 10),
        ])
        self.font_family = family
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        base = (family, size)
        style.configure(".", font=base)
        style.configure("TLabel", font=base)
        style.configure("H1.TLabel", font=(family, size + 4, "bold"))
        style.configure("Hint.TLabel", foreground="#6b7385")
        style.configure("TButton", padding=(10, 5))
        style.configure("Accent.TButton", font=(family, size, "bold"))
        style.configure("TNotebook.Tab", padding=(18, 8))
        style.configure("TLabelframe.Label", foreground="#3b4256")

        header = ttk.Frame(self, padding=(PAD, 10, PAD, 4))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="H1.TLabel").pack(side="left")
        ttk.Label(header, text="PDF 轉 Word・合併・A3 轉 A4（全部在本機執行）",
                  style="Hint.TLabel").pack(side="left", padx=(12, 0))

        caps = probe_capabilities()
        bits = []
        bits.append("OCR：" + ("就緒" if caps["ocr"] else "未安裝"))
        bits.append("數式：" + ("就緒" if caps["formula"] else "未安裝"))
        self.cap_label = ttk.Label(header, text="　·　".join(bits), style="Hint.TLabel")
        self.cap_label.pack(side="right")

        self.nb = nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=PAD, pady=(4, PAD))
        self.convert = ConvertTab(nb, self)
        self.update_idletasks()
        self.merge = MergeTab(nb, self)
        self.update_idletasks()
        self.split = SplitTab(nb, self)
        self.update_idletasks()
        nb.add(self.convert, text="1　PDF 轉 Word")
        nb.add(self.merge, text="2　合併 PDF")
        nb.add(self.split, text="3　A3 轉 A4")

    def offer_open(self, path: str):
        folder = os.path.dirname(path)
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys_platform() == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}" >/dev/null 2>&1 &')
        except Exception:
            pass


def sys_platform() -> str:
    import sys

    return sys.platform


def selftest() -> int:
    """檢查環境（不會開視窗）。有問題時把輸出整段貼回來即可。"""
    print("PDF Studio 自我檢查")
    print(f"  Python      : {sys.version.split()[0]}")
    print(f"  platform    : {sys.platform}")
    print(f"  base dir    : {BASE_DIR}")
    ok = True
    for mod in ("pymupdf", "docx", "numpy", "PIL"):
        try:
            m = __import__(mod)
            print(f"  {mod:12}: OK {getattr(m, '__version__', '')}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"  {mod:12}: 缺少 -> {e}")

    try:
        caps = probe_capabilities()
        print(f"  OCR 引擎    : {'就緒' if caps.get('ocr') else '未安裝'}")
        print(f"  數式引擎    : {'就緒' if caps.get('formula') else '未安裝'}")
    except Exception as e:  # noqa: BLE001
        print(f"  引擎檢查失敗: {e}")

    # Tk 檢查放在子程序，Tk 崩潰也不會把整支程式帶走
    probe = (
        "import tkinter as tk;"
        "r=tk.Tk();r.withdraw();r.update_idletasks();"
        "print('TK_OK');r.destroy()"
    )
    try:
        res = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                             text=True, timeout=30)
        if "TK_OK" in (res.stdout or "") and res.returncode == 0:
            print("  tkinter     : 視窗可建立")
        else:
            ok = False
            print("  tkinter     : 視窗建立失敗")
            print("     " + (res.stderr or res.stdout or "").strip().replace("\n", "\n     ")[:800])
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"  tkinter     : 檢查時發生錯誤 -> {e}")

    print("\n結果：", "可以執行 python3 desktop.py" if ok else "請把上面內容貼回給我")
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())

    try:
        app = App()
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        try:
            from tkinter import messagebox as mb

            mb.showerror(
                APP_TITLE,
                "視窗無法建立。\n\n請執行：python3 desktop.py --selftest\n"
                "再把輸出貼回給我。\n\n" + detail[-600:],
            )
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)
    app.mainloop()


if __name__ == "__main__":
    main()
