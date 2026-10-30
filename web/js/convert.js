/* Tab 1: PDF -> Word (server side pipeline) */
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
      if (files.some((x) => x.file.name === f.name && x.file.size === f.file.size)) return;
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
    if (state) btn().querySelector(".btn-label").textContent = "轉換中…";
    else btn().querySelector(".btn-label").textContent = "開始轉換";
    btn().disabled = state ? true : files.length === 0;
  }

  async function start() {
    if (!files.length) return;
    const results = PS.$("#results-convert");
    results.innerHTML = "";
    if (PS.caps && PS.caps.offline) {
      PS.toast(
        PS.isHosted()
          ? "轉檔服務未連線：請在「轉檔服務設定」填入後端網址"
          : "轉檔服務未連線，請先執行 python3 server.py",
        "err"
      );
      const box = PS.$("#api-panel");
      if (box) box.open = true;
      return;
    }
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
        const pct = f.status === "done" ? 100 : f.progress || 0;
        PS.progressRow(row, pct, f.message || "");
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
        renderResults(data);
        clearTimeout(polling);
        return;
      }
      polling = setTimeout(tick, 800);
    };
    tick();
  }

  function renderResults(data) {
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
      const el = document.createElement("div");
      el.className = "result" + (f.status === "error" ? " error" : "");
      if (f.status === "error") {
        el.innerHTML = `
          <div class="r-ico">!</div>
          <div class="r-body">
            <div class="r-title">${PS.escape(f.name)}</div>
            <div class="r-sub">${PS.escape(f.message || "轉換失敗")}</div>
          </div>`;
        wrap.appendChild(el);
        return;
      }
      const s = f.result.stats || {};
      const chips = [
        `${s.pages || 0} 頁`,
        s.ocr_pages ? `OCR ${s.ocr_pages} 頁` : null,
        `${s.text_lines || 0} 段文字`,
        s.images ? `${s.images} 張圖片` : null,
        s.equations ? `${s.equations} 條方程式` : null,
        s.equations_as_image ? `${s.equations_as_image} 條數式以圖片保留` : null,
        PS.fmtSize(s.size),
      ]
        .filter(Boolean)
        .map((t) => `<span class="chip${/圖片保留/.test(t) ? " warn" : ""}">${PS.escape(t)}</span>`)
        .join("");

      el.innerHTML = `
        <div class="r-ico">✓</div>
        <div class="r-body">
          <div class="r-title">${PS.escape(f.result.filename)}</div>
          <div class="chips">${chips}</div>
        </div>
        <button class="btn primary">下載 .docx</button>`;
      el.querySelector("button").addEventListener("click", () =>
        PS.downloadUrl(PS.apiUrl(f.result.download), f.result.filename)
      );
      if (s.warnings && s.warnings.length) {
        const box = document.createElement("div");
        box.className = "warnbox";
        box.textContent = "提醒：\n" + s.warnings.slice(0, 6).join("\n");
        el.querySelector(".r-body").appendChild(box);
      }
      wrap.appendChild(el);
    });
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
