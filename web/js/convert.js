/* Tab 1: PDF -> Word.
   Two engines: the bundled server.py when it is reachable, otherwise a fully
   in-browser pipeline (pdf.js + tesseract.js + JSZip). */
(function () {
  const PS = window.PS;
  let seq = 0;
  const files = []; // { id, file }
  let polling = null;

  const listEl = () => PS.$("#list-convert");
  const btn = () => PS.$("#btn-convert");
  const clearBtn = () => PS.$("#btn-clear-convert");
  const note = () => PS.$("#convert-note");

  function addFiles(incoming) {
    let added = 0;
    incoming.forEach((f) => {
      if (!PS.isPdf(f)) { PS.toast(`略過非 PDF 檔案：${f.name}`, "err"); return; }
      if (files.some((x) => x.file.name === f.name && x.file.size === f.size)) return;
      files.push({ id: ++seq, file: f });
      added++;
    });
    if (added) render();
  }

  function render() {
    const el = listEl();
    el.innerHTML = "";
    files.forEach((item, i) => {
      const li = document.createElement("li");
      li.className = "frow";
      li.dataset.id = item.id;
      li.innerHTML = `
        <span class="idx">${i + 1}</span>
        <div class="fmeta">
          <div class="fname">${PS.escape(item.file.name)}</div>
          <div class="fsub">${PS.fmtSize(item.file.size)}</div>
          <div class="bar"><i></i></div>
        </div>
        <div class="facts">
          <button class="icon-btn danger" data-remove="${item.id}" title="移除">✕</button>
        </div>`;
      el.appendChild(li);
    });
    el.querySelectorAll("[data-remove]").forEach((b) =>
      b.addEventListener("click", () => {
        const id = Number(b.dataset.remove);
        const idx = files.findIndex((f) => f.id === id);
        if (idx >= 0) files.splice(idx, 1);
        render();
      })
    );
    const has = files.length > 0;
    btn().disabled = !has || btn().dataset.busy === "1";
    clearBtn().disabled = !has;
    note().textContent = has ? `${files.length} 個檔案待轉換` : "";
  }

  function setBusy(state) {
    btn().dataset.busy = state ? "1" : "0";
    PS.btnBusy(btn(), state);
    btn().querySelector(".btn-label").textContent = state ? "轉換中…" : "開始轉換";
    btn().disabled = state ? true : files.length === 0;
  }

  function chips(stats) {
    const s = stats || {};
    return [
      `${s.pages || 0} 頁`,
      s.ocr_pages ? `OCR ${s.ocr_pages} 頁` : null,
      `${s.text_lines || 0} 段文字`,
      s.images ? `${s.images} 張圖片` : null,
      s.equations ? `${s.equations} 條方程式` : null,
      s.equations_as_image ? `${s.equations_as_image} 條數式以圖片保留` : null,
      s.size ? PS.fmtSize(s.size) : null,
    ]
      .filter(Boolean)
      .map((t) => `<span class="chip${/圖片保留/.test(t) ? " warn" : ""}">${PS.escape(t)}</span>`)
      .join("");
  }

  function resultCard({ title, stats, blob, url, filename, error }) {
    const el = document.createElement("div");
    el.className = "result" + (error ? " error" : "");
    if (error) {
      el.innerHTML = `
        <div class="r-ico">!</div>
        <div class="r-body">
          <div class="r-title">${PS.escape(title)}</div>
          <div class="r-sub">${PS.escape(error)}</div>
        </div>`;
      return el;
    }
    el.innerHTML = `
      <div class="r-ico">✓</div>
      <div class="r-body">
        <div class="r-title">${PS.escape(filename)}</div>
        <div class="chips">${chips(stats)}</div>
      </div>
      <button class="btn primary">下載 .docx</button>`;
    el.querySelector("button").addEventListener("click", () => {
      if (blob) PS.download(blob, filename);
      else PS.downloadUrl(url, filename);
    });
    if (stats && stats.warnings && stats.warnings.length) {
      const box = document.createElement("div");
      box.className = "warnbox";
      box.textContent = "提醒：\n" + stats.warnings.slice(0, 6).join("\n");
      el.querySelector(".r-body").appendChild(box);
    }
    return el;
  }

  /* ---------------- in-browser engine ---------------- */

  async function runBrowser() {
    const rows = listEl().querySelectorAll(".frow");
    const wrap = PS.$("#results-convert");
    wrap.innerHTML = "";
    const opts = {
      ocr: PS.$("#ocr-mode").value,
      images: PS.$("#keep-images").checked,
    };
    setBusy(true);
    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i].file;
        const row = rows[i];
        if (row) row.querySelector(".fsub").textContent = "處理中…";
        try {
          const { blob, stats } = await PS.BrowserEngine.convertPdf(file, opts, (done, total, msg) => {
            if (row) PS.progressRow(row, Math.round((done / total) * 100), msg);
            note().textContent = msg;
          });
          if (row) {
            row.querySelector(".bar").classList.add("done");
            PS.progressRow(row, 100, "完成");
            row.querySelector(".fsub").className = "fsub ok";
          }
          wrap.appendChild(
            resultCard({
              title: file.name,
              filename: PS.stripExt(file.name) + ".docx",
              stats,
              blob,
            })
          );
        } catch (err) {
          if (row) {
            row.querySelector(".fsub").className = "fsub err";
            row.querySelector(".fsub").textContent = "失敗";
          }
          wrap.appendChild(resultCard({ title: file.name, error: `${err.message}` }));
        }
      }
      note().textContent = "完成";
    } finally {
      setBusy(false);
    }
  }

  /* ---------------- local server engine ---------------- */

  async function runServer() {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f.file, f.file.name));
    fd.append("ocr_mode", PS.$("#ocr-mode").value);
    fd.append("formula_mode", PS.$("#formula-mode").value);
    fd.append("keep_images", PS.$("#keep-images").checked ? "true" : "false");
    fd.append("dpi", PS.$("#dpi").value);

    setBusy(true);
    note().textContent = "上傳並解析中…";
    let job;
    try {
      const res = await fetch(PS.apiUrl("/api/convert"), { method: "POST", body: fd });
      if (!res.ok) throw new Error(`伺服器回應 ${res.status}`);
      job = await res.json();
    } catch (err) {
      PS.toast("轉換失敗：" + err.message, "err");
      setBusy(false);
      note().textContent = "";
      return;
    }
    poll(job.job_id);
  }

  function poll(jobId) {
    clearTimeout(polling);
    const tick = async () => {
      let data;
      try {
        const res = await fetch(PS.apiUrl(`/api/jobs/${jobId}`), { cache: "no-store" });
        if (!res.ok) throw new Error(`狀態查詢失敗 ${res.status}`);
        data = await res.json();
      } catch (err) {
        note().textContent = "連線中斷：" + err.message;
        setBusy(false);
        return;
      }
      const rows = listEl().querySelectorAll(".frow");
      data.files.forEach((f, i) => {
        const row = rows[i];
        if (!row) return;
        PS.progressRow(row, f.status === "done" ? 100 : f.progress || 0, f.message || "");
        const bar = row.querySelector(".bar");
        const sub = row.querySelector(".fsub");
        if (f.status === "done") { bar.classList.add("done"); sub.className = "fsub ok"; }
        else if (f.status === "error") sub.className = "fsub err";
      });
      const doneCount = data.files.filter((f) => f.status === "done").length;
      note().textContent =
        data.state === "done" ? `完成 ${doneCount} 個檔案` : `處理中… ${doneCount}/${data.files.length}`;

      if (data.state === "done" || data.state === "error") {
        setBusy(false);
        renderServerResults(data);
        clearTimeout(polling);
        return;
      }
      polling = setTimeout(tick, 800);
    };
    tick();
  }

  function renderServerResults(data) {
    const wrap = PS.$("#results-convert");
    wrap.innerHTML = "";

    if (data.bundle) {
      const el = document.createElement("div");
      el.className = "result";
      el.innerHTML = `
        <div class="r-ico">⬇</div>
        <div class="r-body">
          <div class="r-title">全部檔案（ZIP）</div>
          <div class="r-sub">${data.files.length} 份 Word 文件</div>
        </div>
        <button class="btn primary">下載 ZIP</button>`;
      el.querySelector("button").addEventListener("click", () =>
        PS.downloadUrl(PS.apiUrl(data.bundle.download), data.bundle.filename)
      );
      wrap.appendChild(el);
    }

    data.files.forEach((f) => {
      if (f.status === "error") {
        wrap.appendChild(resultCard({ title: f.name, error: f.message || "轉換失敗" }));
        return;
      }
      wrap.appendChild(
        resultCard({
          title: f.name,
          filename: f.result.filename,
          stats: f.result.stats,
          url: PS.apiUrl(f.result.download),
        })
      );
    });
  }

  /* ---------------- entry ---------------- */

  async function start() {
    if (!files.length) return;
    PS.$("#results-convert").innerHTML = "";
    if (PS.engine === "browser") {
      await runBrowser();
      return;
    }
    if (PS.engine === "server") {
      await runServer();
      return;
    }
    PS.toast("找不到可用的轉檔引擎，請確認 vendor/ 目錄完整", "err");
  }

  document.addEventListener("DOMContentLoaded", () => {
    PS.bindDropzone(PS.$("#drop-convert"), PS.$("#input-convert"), addFiles);
    btn().addEventListener("click", start);
    clearBtn().addEventListener("click", () => {
      files.length = 0;
      PS.$("#results-convert").innerHTML = "";
      render();
    });
  });
})();
