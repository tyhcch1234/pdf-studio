/* Tab 3: cut an A3 page down the middle into two A4 pages (pdf-lib + pdf.js preview) */
(function () {
  const PS = window.PS;
  const A3 = { w: 841.89, h: 1190.55 };
  const A4 = { w: 595.28, h: 841.89 };
  const TOL = 9; // points

  let seq = 0;
  const files = []; // { id, file, pageCount }

  const listEl = () => PS.$("#list-split");
  const btn = () => PS.$("#btn-split");

  function addFiles(incoming) {
    let added = 0;
    incoming.forEach((f) => {
      if (!PS.isPdf(f)) { PS.toast(`略過非 PDF 檔案：${f.name}`, "err"); return; }
      files.push({ id: ++seq, file: f, pageCount: null });
      added++;
    });
    if (!added) return;
    render();
    renderPreviews();
  }

  function render() {
    const el = listEl();
    el.innerHTML = "";
    files.forEach((item, i) => {
      const li = document.createElement("li");
      li.className = "frow";
      li.innerHTML = `
        <span class="idx">${i + 1}</span>
        <div class="fmeta">
          <div class="fname">${PS.escape(item.file.name)}</div>
          <div class="fsub">${item.pageCount ? item.pageCount + " 頁 · " : ""}${PS.fmtSize(item.file.size)}</div>
        </div>
        <div class="facts">
          <button class="icon-btn danger" data-remove="${item.id}" title="移除">✕</button>
        </div>`;
      el.appendChild(li);
    });
    el.querySelectorAll("[data-remove]").forEach((b) =>
      b.addEventListener("click", () => {
        const idx = files.findIndex((f) => f.id === Number(b.dataset.remove));
        if (idx >= 0) files.splice(idx, 1);
        render();
        renderPreviews();
      })
    );
    const has = files.length > 0;
    btn().disabled = !has || btn().dataset.busy === "1";
    PS.$("#btn-clear-split").disabled = !has;
    PS.$("#split-note").textContent = has ? `${files.length} 個檔案` : "";
  }

  const isA3 = (w, h) =>
    (Math.abs(w - A3.w) < TOL && Math.abs(h - A3.h) < TOL) ||
    (Math.abs(w - A3.h) < TOL && Math.abs(h - A3.w) < TOL);

  function decideAxis(w, h, mode) {
    if (mode === "vertical") return "v";
    if (mode === "horizontal") return "h";
    return w >= h ? "v" : "h";
  }

  async function renderPreviews() {
    const wrap = PS.$("#previews-split");
    wrap.innerHTML = "";
    if (!window.pdfjsLib || !files.length) return;
    pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.js";
    const onlyA3 = PS.$("#split-only-a3").checked;
    const mode = PS.$("#split-axis").value;
    let shown = 0;

    for (const item of files) {
      if (shown >= 12) break;
      let doc;
      try {
        const buf = await item.file.arrayBuffer();
        doc = await pdfjsLib.getDocument({ data: buf.slice(0) }).promise;
        item.pageCount = doc.numPages;
      } catch (err) {
        PS.toast(`無法預覽 ${item.file.name}`, "err");
        continue;
      }
      for (let p = 1; p <= doc.numPages && shown < 12; p++) {
        const page = await doc.getPage(p);
        const base = page.getViewport({ scale: 1 });
        const scale = Math.min(1.4, 190 / base.width) * (window.devicePixelRatio || 1);
        const vp = page.getViewport({ scale });
        const canvas = document.createElement("canvas");
        canvas.width = Math.ceil(vp.width);
        canvas.height = Math.ceil(vp.height);
        const ctx = canvas.getContext("2d");
        try {
          await page.render({ canvasContext: ctx, viewport: vp }).promise;
        } catch (e) { /* ignore single page render errors */ }

        const a3 = isA3(base.width, base.height);
        const willSplit = onlyA3 ? a3 : true;
        const axis = decideAxis(base.width, base.height, mode);

        const card = document.createElement("div");
        card.className = "pv";
        card.appendChild(canvas);
        if (willSplit) {
          const cut = document.createElement("div");
          cut.className = "cut " + (axis === "v" ? "v" : "h");
          card.appendChild(cut);
        }
        const tag = document.createElement("span");
        tag.className = "tag" + (willSplit ? "" : " skip");
        tag.textContent = willSplit
          ? `${a3 ? "A3" : "非 A3"} · 切成 2 張`
          : `${a3 ? "A3" : "非 A3"} · 保留`;
        card.appendChild(tag);
        wrap.appendChild(card);
        shown++;
      }
      if (doc.destroy) doc.destroy();
    }
    render();
  }

  async function uprightPage(doc, srcPage) {
    const rot = ((srcPage.getRotation().angle % 360) + 360) % 360;
    const { width: W, height: H } = srcPage.getSize();
    if (rot === 0) return { page: srcPage, w: W, h: H };
    const swap = rot === 90 || rot === 270;
    const vw = swap ? H : W;
    const vh = swap ? W : H;
    const p = doc.addPage([vw, vh]);
    const emb = await doc.embedPage(srcPage, { left: 0, bottom: 0, right: W, top: H });
    if (rot === 90) {
      p.drawPage(emb, { x: 0, y: vh, width: W, height: H, rotate: PDFLib.degrees(-90) });
    } else if (rot === 270) {
      p.drawPage(emb, { x: vw, y: 0, width: W, height: H, rotate: PDFLib.degrees(90) });
    } else {
      p.drawPage(emb, { x: vw, y: vh, width: W, height: H, rotate: PDFLib.degrees(180) });
    }
    doc.__temps = (doc.__temps || []).concat([p.ref]);
    return { page: p, w: vw, h: vh };
  }

  async function splitFile(item) {
    const buf = await item.file.arrayBuffer();
    const src = await PDFLib.PDFDocument.load(buf, { ignoreEncryption: true });
    const out = await PDFLib.PDFDocument.create();
    const onlyA3 = PS.$("#split-only-a3").checked;
    const normalize = PS.$("#split-normalize").checked;
    const mode = PS.$("#split-axis").value;
    const reverse = PS.$("#split-order").value === "reverse";

    let produced = 0;
    let kept = 0;
    const pageCount = src.getPageCount();

    for (let i = 0; i < pageCount; i++) {
      const { page, w, h } = await uprightPage(out, src.getPage(i));
      const a3 = isA3(w, h);
      const axis = decideAxis(w, h, mode);
      const shouldSplit = onlyA3 ? a3 : true;

      if (!shouldSplit) {
        // keep the page untouched (re-embed so the temp page can be dropped later)
        const emb = await out.embedPage(page, { left: 0, bottom: 0, right: w, top: h });
        out.addPage([w, h]).drawPage(emb, { x: 0, y: 0, width: w, height: h });
        kept++;
      } else {
        const half = axis === "v" ? w / 2 : h / 2;
        const boxes =
          axis === "v"
            ? [
                { left: 0, bottom: 0, right: half, top: h, out: [half, h] },
                { left: half, bottom: 0, right: w, top: h, out: [half, h] },
              ]
            : [
                { left: 0, bottom: half, right: w, top: h, out: [w, half] },
                { left: 0, bottom: 0, right: w, top: half, out: [w, half] },
              ];
        if (reverse) boxes.reverse();

        for (const box of boxes) {
          const emb = await out.embedPage(page, {
            left: box.left,
            bottom: box.bottom,
            right: box.right,
            top: box.top,
          });
          let [ow, oh] = box.out;
          if (normalize) {
            const want = axis === "v" ? [A4.w, A4.h] : [A4.h, A4.w];
            if (Math.abs(ow - want[0]) < 40 && Math.abs(oh - want[1]) < 40) {
              ow = want[0];
              oh = want[1];
            }
          }
          out.addPage([ow, oh]).drawPage(emb, { x: 0, y: 0, width: ow, height: oh });
          produced++;
        }
      }
    }
    // drop the temporary rotation-normalising pages
    for (const ref of out.__temps || []) {
      const idx = out.getPages().findIndex((pg) => pg.ref === ref);
      if (idx >= 0) {
        try { out.removePage(idx); } catch (e) { /* ignore */ }
      }
    }
    const bytes = await out.save();
    return {
      name: PS.stripExt(item.file.name) + "-A4.pdf",
      blob: new Blob([bytes], { type: "application/pdf" }),
      size: bytes.length,
      produced,
      kept,
      pages: pageCount,
    };
  }

  async function run() {
    if (!files.length) return;
    PS.$("#results-split").innerHTML = "";
    btn().dataset.busy = "1";
    PS.btnBusy(btn(), true);
    btn().querySelector(".btn-label").textContent = "切割中…";
    try {
      for (let i = 0; i < files.length; i++) {
        PS.$("#split-note").textContent = `處理中… ${i + 1}/${files.length}`;
        const r = await splitFile(files[i]);
        renderResult(r);
      }
      PS.$("#split-note").textContent = "完成";
    } catch (err) {
      PS.toast("切割失敗：" + err.message, "err");
      PS.$("#results-split").innerHTML = `<div class="result error"><div class="r-ico">!</div>
        <div class="r-body"><div class="r-title">切割失敗</div>
        <div class="r-sub">${PS.escape(err.message)}</div></div></div>`;
    } finally {
      btn().dataset.busy = "0";
      PS.btnBusy(btn(), false);
      btn().querySelector(".btn-label").textContent = "切成 A4";
      btn().disabled = files.length === 0;
    }
  }

  function renderResult(r) {
    const el = document.createElement("div");
    el.className = "result";
    el.innerHTML = `
      <div class="r-ico">✓</div>
      <div class="r-body">
        <div class="r-title">${PS.escape(r.name)}</div>
        <div class="chips">
          <span class="chip">${r.pages} 頁輸入</span>
          <span class="chip">${r.produced} 張 A4</span>
          ${r.kept ? `<span class="chip">${r.kept} 頁保留原樣</span>` : ""}
          <span class="chip">${PS.fmtSize(r.size)}</span>
        </div>
      </div>
      <button class="btn primary">下載 PDF</button>`;
    el.querySelector("button").addEventListener("click", () => PS.download(r.blob, r.name));
    PS.$("#results-split").appendChild(el);
  }

  document.addEventListener("DOMContentLoaded", () => {
    PS.bindDropzone(PS.$("#drop-split"), PS.$("#input-split"), addFiles);
    btn().addEventListener("click", run);
    PS.$("#btn-clear-split").addEventListener("click", () => {
      files.length = 0;
      PS.$("#results-split").innerHTML = "";
      PS.$("#previews-split").innerHTML = "";
      render();
    });
    ["#split-axis", "#split-only-a3"].forEach((sel) =>
      PS.$(sel).addEventListener("change", renderPreviews)
    );
  });
})();
