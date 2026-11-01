/* Shared helpers: tabs, dropzones, toasts, downloads, formatting. */
(function () {
  const PS = (window.PS = window.PS || {});

  PS.$ = (sel, root) => (root || document).querySelector(sel);
  PS.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  PS.escape = function (str) {
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  };

  PS.fmtSize = function (bytes) {
    if (!bytes && bytes !== 0) return "";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    let n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
  };

  PS.stripExt = (name) => String(name).replace(/\.[^.]+$/, "");

  PS.stamp = function () {
    const d = new Date();
    const p = (v) => String(v).padStart(2, "0");
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
  };

  PS.toast = function (msg, kind) {
    const wrap = PS.$("#toast-wrap");
    if (!wrap) return;
    const el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => {
      el.style.transition = "opacity .25s, transform .25s";
      el.style.opacity = "0";
      el.style.transform = "translateY(6px)";
      setTimeout(() => el.remove(), 260);
    }, 3600);
  };

  PS.download = function (blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  };

  PS.downloadUrl = async function (url, filename) {
    // fetch -> blob so it also works when the API lives on another origin
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      PS.download(blob, filename || "download");
    } catch (err) {
      const a = document.createElement("a");
      a.href = url;
      a.download = filename || "";
      a.target = "_blank";
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
      PS.toast("改用瀏覽器下載：" + err.message, "err");
    }
  };

  PS.bindTabs = function () {
    PS.$$(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        PS.$$(".tab").forEach((t) => {
          t.classList.toggle("active", t === tab);
          t.setAttribute("aria-selected", t === tab ? "true" : "false");
        });
        PS.$$(".panel").forEach((p) => {
          p.classList.toggle("active", p.id === "panel-" + tab.dataset.panel);
        });
      });
    });
  };

  PS.bindDropzone = function (zone, input, onFiles) {
    let depth = 0;
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
    });
    input.addEventListener("change", () => {
      if (input.files && input.files.length) onFiles(Array.from(input.files));
      input.value = "";
    });
    ["dragenter", "dragover"].forEach((ev) =>
      zone.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (ev === "dragenter") depth++;
        zone.classList.add("drag");
      })
    );
    zone.addEventListener("dragleave", (e) => {
      e.preventDefault();
      depth = Math.max(0, depth - 1);
      if (depth === 0) zone.classList.remove("drag");
    });
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      depth = 0;
      zone.classList.remove("drag");
      const files = Array.from(e.dataTransfer?.files || []);
      if (files.length) onFiles(files);
    });
  };

  PS.isPdf = function (file) {
    return /\.pdf$/i.test(file.name) || file.type === "application/pdf";
  };

  PS.progressRow = function (row, percent, text) {
    const bar = row.querySelector(".bar > i");
    if (bar) bar.style.width = Math.max(0, Math.min(100, percent)) + "%";
    const sub = row.querySelector(".fsub");
    if (sub && text) sub.textContent = text;
  };

  PS.btnBusy = function (btn, busy) {
    btn.disabled = busy;
    const sp = btn.querySelector(".spinner");
    if (sp) sp.hidden = !busy;
  };

  PS.detectCapabilities = async function () {
    const ocr = PS.$("#cap-ocr");
    const fml = PS.$("#cap-formula");
    const noteEl = PS.$("#engine-note");
    const browserReady = !!(window.Tesseract && window.JSZip && window.pdfjsLib);

    try {
      const res = await fetch("/api/capabilities", { cache: "no-store" });
      if (!res.ok) throw new Error("bad status");
      const caps = await res.json();
      PS.caps = caps;
      PS.engine = "server";
      ocr.textContent = "本機服務：就緒";
      ocr.className = "cap-pill ok";
      fml.textContent = caps.formula ? "數式辨識：就緒" : "數式辨識：未安裝";
      fml.className = "cap-pill " + (caps.formula ? "ok" : "bad");
      if (noteEl) {
        noteEl.textContent =
          "轉檔引擎：本專案 server.py（完整品質，數式可轉成 Word 方程式）";
      }
      return PS.caps;
    } catch (err) {
      const mathReady = !!(browserReady && PS.MathOMML && PS.MathDetect);
      PS.caps = { ocr: browserReady, formula: mathReady, offline: true };
      PS.engine = browserReady ? "browser" : "none";
      if (browserReady) {
        const fsel = PS.$("#formula-mode");
        if (fsel) {
          fsel.disabled = false;
          fsel.title = mathReady ? "數式辨識成 Word 方程式（瀏覽器內建）" : "此瀏覽器無法執行數式辨識";
          if (!mathReady) fsel.value = "off";
        }
        ocr.textContent = "瀏覽器內建：就緒";
        ocr.className = "cap-pill ok";
        fml.textContent = mathReady ? "數式辨識：瀏覽器內建" : "數式辨識：不可用";
        fml.className = "cap-pill " + (mathReady ? "ok" : "bad");
        if (noteEl) {
          noteEl.textContent = mathReady
            ? "轉檔引擎：瀏覽器內建（OCR、數式辨識與 Word 都在這個頁面完成，不上傳、不需要伺服器）。數式會轉成可編輯的 Word 方程式。"
            : "轉檔引擎：瀏覽器內建（OCR 與 Word 都在這個頁面完成）。數式會保留為文字或圖片。";
        }
      } else {
        ocr.textContent = "引擎未就緒";
        ocr.className = "cap-pill bad";
        fml.textContent = "請確認 vendor/ 檔案完整";
        fml.className = "cap-pill bad";
      }
      return PS.caps;
    }
  };

  function wireApiSettings() {
    const input = PS.$("#api-input");
    const save = PS.$("#api-save");
    const state = PS.$("#api-state");
    if (!input || !save) return;
    input.value = PS.apiBase || "";
    save.addEventListener("click", async () => {
      PS.setApiBase(input.value);
      state.textContent = "連線測試中…";
      state.className = "api-state";
      const caps = await PS.detectCapabilities();
      const ok = caps && !caps.offline;
      state.textContent = ok ? "連線成功" : "無法連線";
      state.className = "api-state " + (ok ? "ok" : "bad");
      PS.toast(
        ok ? "轉檔服務已連線" : "無法連線到該網址，請確認服務已啟動且允許 CORS",
        ok ? "ok" : "err"
      );
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    PS.bindTabs();
    PS.detectCapabilities();
    wireApiSettings();
  });
})();
