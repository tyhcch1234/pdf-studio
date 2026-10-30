/* Tab 2: merge several PDFs (runs fully in the browser via pdf-lib) */
(function () {
  const PS = window.PS;
  let seq = 0;
  let dragId = null;
  const files = []; // { id, file, pages, range }

  const listEl = () => PS.$("#list-merge");
  const btn = () => PS.$("#btn-merge");

  function addFiles(incoming) {
    let added = 0;
    incoming.forEach((f) => {
      if (!PS.isPdf(f)) { PS.toast(`略過非 PDF 檔案：${f.name}`, "err"); return; }
      files.push({ id: ++seq, file: f, pages: null, range: "" });
      added++;
    });
    if (!added) return;
    render();
    readPageCounts();
  }

  async function readPageCounts() {
    for (const item of files) {
      if (item.pages !== null) continue;
      try {
        const buf = await item.file.arrayBuffer();
        const doc = await PDFLib.PDFDocument.load(buf, { ignoreEncryption: true });
        item.pages = doc.getPageCount();
        const row = listEl().querySelector(`.frow[data-id="${item.id}"] .fsub`);
        if (row) row.textContent = `${item.pages} 頁 · ${PS.fmtSize(item.file.size)}`;
      } catch (err) {
        item.broken = true;
        const row = listEl().querySelector(`.frow[data-id="${item.id}"] .fsub`);
        if (row) {
          row.textContent = "無法讀取（可能加密或損壞）";
          row.className = "fsub err";
        }
      }
    }
    updateMeta();
  }

  function render() {
    const el = listEl();
    el.innerHTML = "";
    files.forEach((item, i) => {
      const li = document.createElement("li");
      li.className = "frow";
      li.draggable = true;
      li.dataset.id = item.id;
      li.innerHTML = `
        <span class="idx">${i + 1}</span>
        <div class="fmeta">
          <div class="fname">${PS.escape(item.file.name)}</div>
          <div class="fsub">${item.pages === null ? PS.fmtSize(item.file.size) : item.pages + " 頁"}</div>
        </div>
        <div class="facts">
          <input class="range-input" placeholder="頁碼 e.g. 1-3,5" value="${PS.escape(item.range)}" data-range="${item.id}" />
          <button class="icon-btn" data-up="${item.id}" title="上移" ${i === 0 ? "disabled" : ""}>↑</button>
          <button class="icon-btn" data-down="${item.id}" title="下移" ${i === files.length - 1 ? "disabled" : ""}>↓</button>
          <button class="icon-btn danger" data-remove="${item.id}" title="移除">✕</button>
        </div>`;
      el.appendChild(li);
    });

    el.querySelectorAll("[data-up]").forEach((b) =>
      b.addEventListener("click", () => move(Number(b.dataset.up), -1))
    );
    el.querySelectorAll("[data-down]").forEach((b) =>
      b.addEventListener("click", () => move(Number(b.dataset.down), 1))
    );
    el.querySelectorAll("[data-remove]").forEach((b) =>
      b.addEventListener("click", () => {
        const idx = files.findIndex((f) => f.id === Number(b.dataset.remove));
        if (idx >= 0) files.splice(idx, 1);
        render();
        updateMeta();
      })
    );
    el.querySelectorAll("[data-range]").forEach((inp) =>
      inp.addEventListener("input", () => {
        const item = files.find((f) => f.id === Number(inp.dataset.range));
        if (item) item.range = inp.value.trim();
      })
    );
    el.querySelectorAll(".frow").forEach((row) => {
      row.addEventListener("dragstart", () => {
        dragId = Number(row.dataset.id);
        row.style.opacity = ".45";
      });
      row.addEventListener("dragend", () => {
        row.style.opacity = "";
        dragId = null;
      });
      row.addEventListener("dragover", (e) => e.preventDefault());
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        const target = Number(row.dataset.id);
        if (dragId === null || dragId === target) return;
        const from = files.findIndex((f) => f.id === dragId);
        const to = files.findIndex((f) => f.id === target);
        if (from < 0 || to < 0) return;
        const [moved] = files.splice(from, 1);
        files.splice(to, 0, moved);
        dragId = null;
        render();
      });
    });
    updateMeta();
  }

  function move(id, delta) {
    const idx = files.findIndex((f) => f.id === id);
    const next = idx + delta;
    if (idx < 0 || next < 0 || next >= files.length) return;
    const [item] = files.splice(idx, 1);
    files.splice(next, 0, item);
    render();
  }

  function updateMeta() {
    const has = files.length > 0;
    btn().disabled = !has || btn().dataset.busy === "1";
    PS.$("#btn-clear-merge").disabled = !has;
    const total = files.reduce((sum, f) => sum + (f.pages || 0), 0);
    PS.$("#merge-note").textContent = has ? `${files.length} 個檔案 · 共 ${total} 頁` : "";
  }

  function parseRange(text, max) {
    if (!text) return null;
    const out = new Set();
    text.split(/[,，]/).forEach((part) => {
      const seg = part.trim();
      if (!seg) return;
      const m = seg.match(/^(\d+)\s*[-–~]\s*(\d+)$/);
      if (m) {
        let a = parseInt(m[1], 10);
        let b = parseInt(m[2], 10);
        if (a > b) [a, b] = [b, a];
        for (let i = Math.max(1, a); i <= Math.min(max, b); i++) out.add(i - 1);
      } else if (/^\d+$/.test(seg)) {
        const n = parseInt(seg, 10);
        if (n >= 1 && n <= max) out.add(n - 1);
      }
    });
    const list = Array.from(out).sort((a, b) => a - b);
    return list.length ? list : null;
  }

  async function merge() {
    if (!files.length) return;
    const outDoc = await PDFLib.PDFDocument.create();
    const wrap = PS.$("#results-merge");
    wrap.innerHTML = "";
    psBtn(true);
    try {
      let copied = 0;
      for (let i = 0; i < files.length; i++) {
        const item = files[i];
        PS.$("#merge-note").textContent = `合併中… ${i + 1}/${files.length}`;
        const buf = await item.file.arrayBuffer();
        const src = await PDFLib.PDFDocument.load(buf, { ignoreEncryption: true });
        const total = src.getPageCount();
        const idx = parseRange(item.range, total) || src.getPageIndices();
        const pages = await outDoc.copyPages(src, idx);
        pages.forEach((p) => outDoc.addPage(p));
        copied += pages.length;
      }
      const stamp = PS.$("#merge-bookmarks").checked ? "-" + PS.stamp() : "";
      const name = (PS.$("#merge-name").value.trim() || "merged") + stamp + ".pdf";
      const bytes = await outDoc.save();
      const blob = new Blob([bytes], { type: "application/pdf" });
      renderResult(name, copied, blob.size, blob);
      PS.$("#merge-note").textContent = `完成，共 ${copied} 頁`;
    } catch (err) {
      wrap.innerHTML = `<div class="result error"><div class="r-ico">!</div>
        <div class="r-body"><div class="r-title">合併失敗</div>
        <div class="r-sub">${PS.escape(err.message)}</div></div></div>`;
      PS.toast("合併失敗：" + err.message, "err");
      PS.$("#merge-note").textContent = "";
    } finally {
      psBtn(false);
    }
  }

  function psBtn(busy) {
    btn().dataset.busy = busy ? "1" : "0";
    PS.btnBusy(btn(), busy);
    btn().querySelector(".btn-label").textContent = busy ? "合併中…" : "合併 PDF";
    btn().disabled = busy ? true : files.length === 0;
  }

  function renderResult(name, pages, size, blob) {
    const el = document.createElement("div");
    el.className = "result";
    el.innerHTML = `
      <div class="r-ico">✓</div>
      <div class="r-body">
        <div class="r-title">${PS.escape(name)}</div>
        <div class="chips"><span class="chip">${pages} 頁</span><span class="chip">${PS.fmtSize(size)}</span></div>
      </div>
      <button class="btn primary">下載 PDF</button>`;
    el.querySelector("button").addEventListener("click", () => PS.download(blob, name));
    PS.$("#results-merge").appendChild(el);
  }

  document.addEventListener("DOMContentLoaded", () => {
    PS.bindDropzone(PS.$("#drop-merge"), PS.$("#input-merge"), addFiles);
    btn().addEventListener("click", merge);
    PS.$("#btn-clear-merge").addEventListener("click", () => {
      files.length = 0;
      PS.$("#results-merge").innerHTML = "";
      render();
    });
  });
})();
