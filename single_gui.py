# GUI + CLI，合併進 pdf_studio.py 的最後一段（也可單獨閱讀）
import argparse
import os
import queue
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog

APP_TITLE = "PDF Studio"
PAD = 10
ACCENT = "#4f46e5"


def _open_pdf(path: str):
    return pymupdf.open(path)


# --------------------------------------------------------------------------- #
# 桌面視窗：只用基本 tk 控件（Label / Button / Listbox / OptionMenu / Canvas），
# 避免在部分 Linux 的 Tk 上建立複雜控件時中斷。
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x640")
        self.minsize(760, 560)
        self.configure(bg="#f5f6fa")
        self.files: list[str] = []
        self.q: queue.Queue = queue.Queue()
        self.busy = False

        self.outdir = tk.StringVar(value=os.path.expanduser("~"))

        head = tk.Frame(self, bg="#ffffff", height=58)
        head.pack(fill="x")
        tk.Label(head, text=APP_TITLE, bg="#ffffff", fg="#0f172a",
                 font=("", 16, "bold")).place(x=16, y=14)
        self.engine_lbl = tk.Label(head, text="檢查環境中…", bg="#ffffff", fg="#667085",
                                   font=("", 10))
        self.engine_lbl.place(relx=1.0, x=-16, y=20, anchor="ne")

        bar = tk.Frame(self, bg="#eef0f6", height=44)
        bar.pack(fill="x")
        self.tabs = {}
        for i, (key, label) in enumerate(
            [("word", "PDF 轉 Word"), ("merge", "合併 PDF"), ("split", "A3 轉 A4")]
        ):
            b = tk.Button(bar, text=label, bd=0, relief="flat", bg="#eef0f6", fg="#334155",
                          activebackground="#ffffff", font=("", 11),
                          command=lambda k=key: self.show(k))
            b.place(x=8 + i * 150, y=6, width=142, height=32)
            self.tabs[key] = b

        self.body = tk.Frame(self, bg="#f5f6fa")
        self.body.pack(fill="both", expand=True)
        self.pages = {k: tk.Frame(self.body, bg="#f5f6fa") for k in ("word", "merge", "split")}
        for f in self.pages.values():
            f.place(relwidth=1, relheight=1)

        self._build_word()
        self._build_merge()
        self._build_split()
        self.show("word")

        self.progress = tk.Canvas(self, height=6, bg="#e9ecf5", highlightthickness=0)
        self.progress.pack(fill="x", side="bottom")
        self.bar = self.progress.create_rectangle(0, 0, 0, 6, fill=ACCENT, width=0)
        self.status = tk.Label(self, text="就緒", bg="#ffffff", fg="#475069", anchor="w",
                               font=("", 9))
        self.status.pack(fill="x", side="bottom")

        self.after(200, self._poll)
        threading.Thread(target=self._probe, daemon=True).start()

    # ---------------- helpers ----------------
    def show(self, key: str) -> None:
        self.pages[key].lift()
        for k, b in self.tabs.items():
            b.configure(bg="#ffffff" if k == key else "#eef0f6",
                        fg=ACCENT if k == key else "#334155")

    def _probe(self) -> None:
        try:
            caps = capabilities()
            self.q.put(("engines", caps))
        except Exception as e:  # noqa: BLE001
            self.q.put(("engines", {"error": str(e)}))

    def _poll(self) -> None:
        try:
            while True:
                kind, *payload = self.q.get_nowait()
                if kind == "engines":
                    caps = payload[0]
                    ocr = "OCR 就緒" if caps.get("ocr") else "OCR 未安裝"
                    fml = "數式就緒" if caps.get("formula") else "數式未安裝"
                    self.engine_lbl.configure(text=f"{ocr} · {fml}")
                elif kind == "status":
                    self.status.configure(text=payload[0])
                elif kind == "progress":
                    self._set_progress(payload[0])
                elif kind == "done":
                    self._finish(payload[0])
                elif kind == "error":
                    self._finish(payload[0], error=True)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _set_progress(self, ratio: float) -> None:
        w = self.progress.winfo_width() or 1
        self.progress.coords(self.bar, 0, 0, max(0.0, min(1.0, ratio)) * w, 6)

    def _finish(self, message: str, error: bool = False) -> None:
        self.busy = False
        self.status.configure(text=message, fg="#d33d43" if error else "#0f9d63")
        self._set_progress(1.0 if not error else 0.0)
        for b in self.action_buttons:
            b.configure(state="normal")

    def _start(self) -> None:
        if self.busy:
            return
        self.busy = True
        for b in self.action_buttons:
            b.configure(state="disabled")
        self._set_progress(0.0)

    # ---------------- files list ----------------
    def _make_list(self, parent) -> tk.Listbox:
        box = tk.Listbox(parent, relief="flat", highlightthickness=1,
                         highlightbackground="#e2e6f0", bg="#ffffff", font=("", 10),
                         selectmode="extended", activestyle="none")
        box.pack(fill="both", expand=True, padx=PAD, pady=(0, 6))
        return box

    def _buttons_row(self, parent, add_cmd, remove_cmd, out_cmd=None):
        row = tk.Frame(parent, bg="#f5f6fa")
        row.pack(fill="x", padx=PAD, pady=(0, 8))
        tk.Button(row, text="加入 PDF…", command=add_cmd, bg="#ffffff", relief="solid",
                  bd=1, padx=12, pady=4).pack(side="left")
        tk.Button(row, text="移除", command=remove_cmd, bg="#ffffff", relief="solid",
                  bd=1, padx=12, pady=4).pack(side="left", padx=6)
        if out_cmd:
            tk.Button(row, text="輸出資料夾…", command=out_cmd, bg="#ffffff",
                      relief="solid", bd=1, padx=12, pady=4).pack(side="left", padx=6)
        return row

    # ---------------- tab 1 ----------------
    def _build_word(self) -> None:
        f = self.pages["word"]
        top = tk.Frame(f, bg="#f5f6fa")
        top.pack(fill="both", expand=True)
        tk.Label(top, text="要轉換的 PDF（可多選，依序處理）", bg="#f5f6fa", fg="#475069",
                 font=("", 10)).pack(anchor="w", padx=PAD, pady=(12, 4))
        self.list_word = self._make_list(top)
        self._buttons_row(top, self._add_word, self._remove_word,
                          lambda: self._pick_out(self.outdir))
        self.out_lbl = tk.Label(top, text="", bg="#f5f6fa", fg="#7b849b", font=("", 9),
                                anchor="w")
        self.out_lbl.pack(fill="x", padx=PAD)

        opts = tk.Frame(top, bg="#f5f6fa")
        opts.pack(fill="x", padx=PAD, pady=10)
        tk.Label(opts, text="OCR", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.ocr_mode = tk.StringVar(value="自動（掃描頁才 OCR）")
        tk.OptionMenu(opts, self.ocr_mode, "自動（掃描頁才 OCR）", "一律 OCR", "關閉").pack(
            side="left", padx=(6, 18))
        tk.Label(opts, text="數式", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.fml_mode = tk.StringVar(value="開啟")
        tk.OptionMenu(opts, self.fml_mode, "開啟", "關閉").pack(side="left", padx=(6, 18))
        tk.Label(opts, text="解析度", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.dpi_var = tk.StringVar(value="200")
        tk.OptionMenu(opts, self.dpi_var, "150", "200", "250", "300").pack(side="left", padx=6)

        act = tk.Frame(top, bg="#f5f6fa")
        act.pack(fill="x", padx=PAD, pady=(4, 12))
        b = tk.Button(act, text="開始轉換", command=self._run_word, bg=ACCENT, fg="white",
                      relief="flat", padx=22, pady=8, font=("", 11, "bold"),
                      activebackground="#4338ca", activeforeground="white")
        b.pack(side="left")
        self.action_buttons = [b]
        self.out_lbl.configure(text=f"輸出到：{self.outdir.get()}")

    def _add_word(self) -> None:
        paths = filedialog.askopenfilenames(title="選擇 PDF", filetypes=[("PDF", "*.pdf")])
        for p in paths:
            self.files.append(p)
            self.list_word.insert("end", os.path.basename(p))

    def _remove_word(self) -> None:
        for i in reversed(self.list_word.curselection()):
            self.list_word.delete(i)
            self.files.pop(i)

    def _pick_out(self, var) -> None:
        d = filedialog.askdirectory(title="選擇輸出資料夾", initialdir=var.get())
        if d:
            var.set(d)
            self.out_lbl.configure(text=f"輸出到：{d}")

    def _run_word(self) -> None:
        if not self.files:
            self.status.configure(text="請先加入 PDF", fg="#d33d43")
            return
        self._start()
        outdir = self.outdir.get()
        ocr = {"自動（掃描頁才 OCR）": "auto", "一律 OCR": "always", "關閉": "never"}[
            self.ocr_mode.get()]
        opts = ConvertOptions(ocr=ocr, formulas="auto" if self.fml_mode.get() == "開啟" else "off",
                              dpi=int(self.dpi_var.get()))
        paths = list(self.files)
        threading.Thread(target=self._word_worker, args=(paths, outdir, opts),
                         daemon=True).start()

    def _word_worker(self, paths, outdir, opts) -> None:
        try:
            for n, src in enumerate(paths, 1):
                name = os.path.splitext(os.path.basename(src))[0] + ".docx"
                dst = os.path.join(outdir, name)
                base, i = dst, 2
                while os.path.exists(dst):
                    dst = f"{os.path.splitext(base)[0]}({i}).docx"
                    i += 1

                def progress(done, total, msg, n=n):
                    frac = (n - 1 + min(1.0, done / max(1, total))) / len(paths)
                    self.q.put(("progress", frac))
                    self.q.put(("status", f"[{n}/{len(paths)}] {os.path.basename(src)} · {msg}"))

                stats = convert_pdf(src, dst, opts, progress=progress)
                self.q.put(("status",
                            f"完成 {name}：{stats['pages']} 頁、{stats['text_lines']} 段、"
                            f"{stats['images']} 圖、{stats['equations']} 條方程式"))
            self.q.put(("done", f"完成 {len(paths)} 個檔案"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.q.put(("error", f"失敗：{e}"))

    # ---------------- tab 2 ----------------
    def _build_merge(self) -> None:
        f = self.pages["merge"]
        tk.Label(f, text="依清單順序合併（用 ↑↓ 調整）", bg="#f5f6fa", fg="#475069",
                 font=("", 10)).pack(anchor="w", padx=PAD, pady=(12, 4))
        self.list_merge = self._make_list(f)
        row = self._buttons_row(f, self._add_merge, self._remove_merge)
        tk.Button(row, text="↑", command=lambda: self._move(-1), bg="#ffffff",
                  relief="solid", bd=1, padx=10, pady=4).pack(side="left", padx=6)
        tk.Button(row, text="↓", command=lambda: self._move(1), bg="#ffffff",
                  relief="solid", bd=1, padx=10, pady=4).pack(side="left")
        act = tk.Frame(f, bg="#f5f6fa")
        act.pack(fill="x", padx=PAD, pady=8)
        b = tk.Button(act, text="合併並存檔…", command=self._run_merge, bg=ACCENT, fg="white",
                      relief="flat", padx=22, pady=8, font=("", 11, "bold"))
        b.pack(side="left")
        self.action_buttons.append(b)

    def _add_merge(self) -> None:
        for p in filedialog.askopenfilenames(title="選擇 PDF", filetypes=[("PDF", "*.pdf")]):
            self.merge_files = getattr(self, "merge_files", [])
            self.merge_files.append(p)
            self.list_merge.insert("end", os.path.basename(p))

    def _remove_merge(self) -> None:
        self.merge_files = getattr(self, "merge_files", [])
        for i in reversed(self.list_merge.curselection()):
            self.list_merge.delete(i)
            self.merge_files.pop(i)

    def _move(self, delta: int) -> None:
        self.merge_files = getattr(self, "merge_files", [])
        sel = self.list_merge.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if not (0 <= j < len(self.merge_files)):
            return
        self.merge_files[i], self.merge_files[j] = self.merge_files[j], self.merge_files[i]
        self.list_merge.delete(i, i)
        self.list_merge.insert(j, os.path.basename(self.merge_files[j]))
        self.list_merge.itemconfig(i, selectbackground="#c7d2fe")
        self.list_merge.selection_clear(0, "end")
        self.list_merge.selection_set(j)

    def _run_merge(self) -> None:
        files = getattr(self, "merge_files", [])
        if len(files) < 2:
            self.status.configure(text="至少需要兩個 PDF", fg="#d33d43")
            return
        dst = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           filetypes=[("PDF", "*.pdf")],
                                           initialfile="merged.pdf")
        if not dst:
            return
        self._start()
        threading.Thread(target=self._merge_worker, args=(files, dst), daemon=True).start()

    def _merge_worker(self, files, dst) -> None:
        try:
            stats = merge_pdfs(files, dst)
            self.q.put(("done", f"已合併 {stats['files']} 個檔案、共 {stats['pages']} 頁 → {os.path.basename(dst)}"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.q.put(("error", f"合併失敗：{e}"))

    # ---------------- tab 3 ----------------
    def _build_split(self) -> None:
        f = self.pages["split"]
        tk.Label(f, text="A3 沿中線切成兩張 A4（向量保留，不會變模糊）", bg="#f5f6fa",
                 fg="#475069", font=("", 10)).pack(anchor="w", padx=PAD, pady=(12, 4))
        self.list_split = self._make_list(f)
        self._buttons_row(f, self._add_split, self._remove_split)
        opts = tk.Frame(f, bg="#f5f6fa")
        opts.pack(fill="x", padx=PAD, pady=8)
        tk.Label(opts, text="切割方向", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.axis_var = tk.StringVar(value="自動")
        tk.OptionMenu(opts, self.axis_var, "自動", "垂直（左右）", "水平（上下）").pack(
            side="left", padx=(6, 18))
        tk.Label(opts, text="輸出順序", bg="#f5f6fa", font=("", 10)).pack(side="left")
        self.order_var = tk.StringVar(value="左→右 / 上→下")
        tk.OptionMenu(opts, self.order_var, "左→右 / 上→下", "右→左 / 下→上").pack(side="left", padx=6)
        act = tk.Frame(f, bg="#f5f6fa")
        act.pack(fill="x", padx=PAD, pady=8)
        b = tk.Button(act, text="切成 A4…", command=self._run_split, bg=ACCENT, fg="white",
                      relief="flat", padx=22, pady=8, font=("", 11, "bold"))
        b.pack(side="left")
        self.action_buttons.append(b)
        self.split_files = []

    def _add_split(self) -> None:
        for p in filedialog.askopenfilenames(title="選擇 A3 PDF", filetypes=[("PDF", "*.pdf")]):
            self.split_files.append(p)
            self.list_split.insert("end", os.path.basename(p))

    def _remove_split(self) -> None:
        for i in reversed(self.list_split.curselection()):
            self.list_split.delete(i)
            self.split_files.pop(i)

    def _run_split(self) -> None:
        if not self.split_files:
            self.status.configure(text="請先加入 A3 PDF", fg="#d33d43")
            return
        dst = filedialog.asksaveasfilename(defaultextension=".pdf",
                                           filetypes=[("PDF", "*.pdf")],
                                           initialfile="a4.pdf")
        if not dst:
            return
        axis = {"自動": "auto", "垂直（左右）": "vertical", "水平（上下）": "horizontal"}[
            self.axis_var.get()]
        order = "reverse" if self.order_var.get().startswith("右") else "normal"
        self._start()
        threading.Thread(target=self._split_worker,
                         args=(list(self.split_files), dst, axis, order), daemon=True).start()

    def _split_worker(self, files, dst, axis, order) -> None:
        try:
            tmp = []
            for i, src in enumerate(files):
                part = dst if len(files) == 1 else f"{os.path.splitext(dst)[0]}_{i + 1}.pdf"
                tmp.append(part)
                self.q.put(("status", f"切割 {os.path.basename(src)}…"))
                split_a3(src, part, axis=axis, order=order)
            total = sum(pymupdf.open(p).page_count for p in tmp)
            self.q.put(("done", f"完成，共 {total} 頁 → {os.path.basename(dst)}"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.q.put(("error", f"切割失敗：{e}"))


# --------------------------------------------------------------------------- #
# 命令列
# --------------------------------------------------------------------------- #
def run_cli(argv: list[str]) -> int:
    if not argv or argv[0] == "--check":
        caps = capabilities()
        print(f"OCR 引擎 : {'就緒' if caps['ocr'] else '未安裝'}")
        print(f"數式引擎 : {'就緒' if caps['formula'] else '未安裝'}")
        return 0
    outdir = os.path.dirname(os.path.abspath(argv[0])) or "."
    for src in argv:
        if not os.path.exists(src):
            print(f"找不到 {src}")
            continue
        dst = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".docx")
        print(f"轉換 {os.path.basename(src)} …")
        stats = convert_pdf(src, dst, ConvertOptions(),
                            progress=lambda d, t, m: print(f"  {m}", end="\r"))
        print(f"\n完成 {dst}")
        print(f"  {stats['pages']} 頁、{stats['text_lines']} 段文字、"
              f"{stats['images']} 張圖、{stats['equations']} 條方程式")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-"):
        return run_cli(argv)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv and argv[0] == "--check":
        return run_cli(["--check"])
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
