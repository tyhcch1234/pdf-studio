/* PDF -> Word entirely in the browser: pdf.js for the text layer, tesseract.js
   for scanned pages, canvas + custom code for figures, JSZip for the .docx.
   No network calls leave the machine (all engines are vendored here). */
(function () {
  const PS = (window.PS = window.PS || {});

  const CFG = {
    textThreshold: 25, // fewer characters than this on a page => OCR
    renderScale: 2.0, // canvas scale for OCR and figure crops
    analysis: 4, // downsample factor for figure detection
  };

  let workerPromise = null;

  function getOcrWorker(onStatus) {
    if (!workerPromise) {
      workerPromise = Tesseract.createWorker("eng+chi_tra", 1, {
        workerPath: "vendor/tesseract/worker.min.js",
        corePath: "vendor/tesseract/tesseract-core-simd.wasm.js",
        langPath: "vendor/tesseract/lang",
        workerBlobURL: false,
        logger: (m) => {
          if (onStatus && m.status === "recognizing text") onStatus(m.progress);
        },
      }).catch((err) => {
        workerPromise = null;
        throw err;
      });
    }
    return workerPromise;
  }

  function renderPage(page, scale) {
    const vp = page.getViewport({ scale });
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.ceil(vp.width));
    canvas.height = Math.max(1, Math.ceil(vp.height));
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return page.render({ canvasContext: ctx, viewport: vp }).promise.then(() => ({ canvas, vp }));
  }

  /* ---------------- text layer ---------------- */

  function textLines(textContent, vp) {
    const styles = textContent.styles || {};
    const items = [];
    for (const it of textContent.items) {
      if (!it.str || !it.str.trim()) continue;
      const m = PDFLibUtil(vp, it.transform);
      const size = Math.hypot(m[2], m[3]) || Math.hypot(m[0], m[1]) || 10;
      items.push({
        str: it.str,
        x: m[4],
        y: m[5],
        w: it.width || size * it.str.length * 0.5,
        size,
        font: (styles[it.fontName] || {}).fontFamily || "",
      });
    }
    if (!items.length) return [];

    items.sort((a, b) => b.y - a.y || a.x - b.x);
    const lines = [];
    for (const it of items) {
      let line = lines.find((l) => Math.abs(l.y - it.y) <= Math.max(2, Math.min(l.size, it.size) * 0.45));
      if (!line) {
        line = { y: it.y, size: it.size, items: [] };
        lines.push(line);
      }
      line.items.push(it);
      line.size = Math.max(line.size, it.size);
    }

    return lines.map((line) => {
      line.items.sort((a, b) => a.x - b.x);
      const x0 = line.items[0].x;
      let x1 = x0;
      const runs = [];
      let prevEnd = null;
      for (const it of line.items) {
        let text = it.str;
        if (prevEnd !== null) {
          const gap = it.x - prevEnd;
          if (gap > it.size * 1.6) {
            text = " ".repeat(Math.min(12, Math.max(1, Math.round(gap / (it.size * 0.5))))) + text;
          } else if (gap > it.size * 0.25 && !/\s$/.test(runs.length ? runs[runs.length - 1].text : "")) {
            text = " " + text;
          }
        }
        prevEnd = it.x + it.w;
        x1 = Math.max(x1, it.x + it.w);
        runs.push({
          text,
          size: it.size,
          bold: /bold|black|heavy|semibold/i.test(it.font),
          italic: /italic|oblique/i.test(it.font),
          color: [0, 0, 0],
        });
      }
      return {
        kind: "text",
        bbox: [x0, line.y - line.size * 0.82, x1, line.y + line.size * 0.28],
        runs,
      };
    });
  }

  function PDFLibUtil(vp, transform) {
    // pdf.js exposes Util.transform; fall back to a manual multiply
    if (window.pdfjsLib && pdfjsLib.Util && pdfjsLib.Util.transform) {
      return pdfjsLib.Util.transform(vp.transform, transform);
    }
    const [a, b, c, d, e, f] = vp.transform;
    const [ga, gb, gc, gd, ge, gf] = transform;
    return [
      a * ga + c * gb,
      b * ga + d * gb,
      a * gc + c * gd,
      b * gc + d * gd,
      a * ge + c * gf + e,
      b * ge + d * gf + f,
    ];
  }

  /* ---------------- figures ---------------- */

  function blankMask(mask, w, h, boxes, pad) {
    for (const b of boxes) {
      const x0 = Math.max(0, Math.floor(b[0]) - pad);
      const y0 = Math.max(0, Math.floor(b[1]) - pad);
      const x1 = Math.min(w, Math.ceil(b[2]) + pad);
      const y1 = Math.min(h, Math.ceil(b[3]) + pad);
      for (let y = y0; y < y1; y++) {
        const row = y * w;
        for (let x = x0; x < x1; x++) mask[row + x] = 0;
      }
    }
    return mask;
  }

  function components(mask, w, h, minPx) {
    const seen = new Uint8Array(w * h);
    const boxes = [];
    const stack = [];
    for (let i = 0; i < mask.length; i++) {
      if (!mask[i] || seen[i]) continue;
      stack.length = 0;
      stack.push(i);
      seen[i] = 1;
      let x0 = w, x1 = 0, y0 = h, y1 = 0, count = 0;
      while (stack.length) {
        const p = stack.pop();
        const y = (p / w) | 0;
        const x = p - y * w;
        count++;
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
        if (x > 0 && mask[p - 1] && !seen[p - 1]) { seen[p - 1] = 1; stack.push(p - 1); }
        if (x < w - 1 && mask[p + 1] && !seen[p + 1]) { seen[p + 1] = 1; stack.push(p + 1); }
        if (y > 0 && mask[p - w] && !seen[p - w]) { seen[p - w] = 1; stack.push(p - w); }
        if (y < h - 1 && mask[p + w] && !seen[p + w]) { seen[p + w] = 1; stack.push(p + w); }
        if (count > 200000) break;
      }
      if (count >= minPx) boxes.push([x0, y0, x1, y1, count]);
    }
    return boxes;
  }

  function dilate(mask, w, h, passes) {
    let src = mask;
    for (let p = 0; p < passes; p++) {
      const dst = new Uint8Array(src);
      for (let y = 1; y < h - 1; y++) {
        const row = y * w;
        for (let x = 1; x < w - 1; x++) {
          const i = row + x;
          if (src[i]) continue;
          if (src[i - 1] || src[i + 1] || src[i - w] || src[i + w]) dst[i] = 1;
        }
      }
      src = dst;
    }
    return src;
  }

  function cropCanvas(canvas, box) {
    const w = Math.max(1, Math.round(box[2] - box[0]));
    const h = Math.max(1, Math.round(box[3] - box[1]));
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(canvas, box[0], box[1], w, h, 0, 0, w, h);
    return c;
  }

  function canvasToPng(canvas) {
    return new Promise((resolve) =>
      canvas.toBlob(async (blob) => {
        resolve(new Uint8Array(await blob.arrayBuffer()));
      }, "image/png")
    );
  }

  /** find ink that is neither text nor background, crop it as a picture */
  async function findFigures(canvas, elements, scale) {
    const f = CFG.analysis;
    const aw = Math.max(1, Math.floor(canvas.width / f));
    const ah = Math.max(1, Math.floor(canvas.height / f));

    const small = document.createElement("canvas");
    small.width = aw;
    small.height = ah;
    const sctx = small.getContext("2d", { willReadFrequently: true });
    sctx.fillStyle = "#fff";
    sctx.fillRect(0, 0, aw, ah);
    sctx.drawImage(canvas, 0, 0, aw, ah);
    const data = sctx.getImageData(0, 0, aw, ah).data;

    let mask = new Uint8Array(aw * ah);
    let ink = 0;
    for (let i = 0, p = 0; i < data.length; i += 4, p++) {
      const g = (data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114) | 0;
      if (g < 210) { mask[p] = 1; ink++; }
    }
    if (ink < 120) return { figures: [], dropped: new Set() };

    const textBoxes = elements.filter((e) => e.kind === "text").map((e) => e.bbox);
    const scaled = textBoxes.map((b) => [b[0] * scale / f, b[1] * scale / f, b[2] * scale / f, b[3] * scale / f]);
    mask = blankMask(mask, aw, ah, scaled, 1);
    if (mask.reduce((a, v) => a + v, 0) < 60) return { figures: [], dropped: new Set() };

    const grown = dilate(mask, aw, ah, 6);
    // never let a dilated region swallow the text areas again
    const textMask = new Uint8Array(aw * ah);
    for (const b of scaled) {
      const x0 = Math.max(0, Math.floor(b[0]));
      const y0 = Math.max(0, Math.floor(b[1]));
      const x1 = Math.min(aw, Math.ceil(b[2]));
      const y1 = Math.min(ah, Math.ceil(b[3]));
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) textMask[y * aw + x] = 1;
    }
    for (let i = 0; i < grown.length; i++) if (textMask[i]) grown[i] = 0;

    const boxes = components(grown, aw, ah, 80);
    const pageArea = aw * ah;
    const out = [];
    for (const [bx0, by0, bx1, by1, count] of boxes) {
      const w = bx1 - bx0;
      const h = by1 - by0;
      if (w < 8 || h < 6) continue;
      if ((w * h) / pageArea < 0.004) continue;
      if (Math.max(w, h) / Math.max(1, Math.min(w, h)) > 26) continue;
      if (count / (w * h) < 0.012) continue;
      // pad a little so thin strokes are not shaved off
      const box = [
        Math.max(0, (bx0 - 2) * f),
        Math.max(0, (by0 - 2) * f),
        Math.min(canvas.width, (bx1 + 2) * f),
        Math.min(canvas.height, (by1 + 2) * f),
      ];
      out.push([box[0], box[1], box[2], box[3], count]);
    }
    // merge overlapping crops
    const merged = [];
    for (const b of out) {
      const hit = merged.find((m) => !(b[2] < m[0] || b[0] > m[2] || b[3] < m[1] || b[1] > m[3]));
      if (hit) {
        hit[4] = (hit[4] || 0) + (b[4] || 0);
        hit[0] = Math.min(hit[0], b[0]);
        hit[1] = Math.min(hit[1], b[1]);
        hit[2] = Math.max(hit[2], b[2]);
        hit[3] = Math.max(hit[3], b[3]);
      } else merged.push([b[0], b[1], b[2], b[3], b[4] || 0]);
    }

    const figures = [];
    const dropped = new Set();
    for (const b of merged) {
      if (b[2] - b[0] < 10 || b[3] - b[1] < 8) continue;
      const pt = [b[0] / scale, b[1] / scale, b[2] / scale, b[3] / scale];
      const inside = [];
      elements.forEach((el, idx) => {
        if (el.kind !== "text") return;
        const cx = (el.bbox[0] + el.bbox[2]) / 2;
        const cy = (el.bbox[1] + el.bbox[3]) / 2;
        if (cx >= pt[0] && cx <= pt[2] && cy >= pt[1] && cy <= pt[3]) inside.push(idx);
      });
      const density = (b[4] || 0) / Math.max(1, (b[2] - b[0]) * (b[3] - b[1]));

      if (inside.length) {
        if (density < 0.1) continue; // ruled table / frame: keep the words
        inside.forEach((i) => dropped.add(i)); // chart labels live in the picture
      }
      figures.push({ kind: "image", bbox: pt, png: await canvasToPng(cropCanvas(canvas, b)) });
    }
    return { figures, dropped };
  }

  /* ---------------- reading order ---------------- */

  function gutters(items) {
    const x0 = Math.min(...items.map((i) => i.bbox[0]));
    const x1 = Math.max(...items.map((i) => i.bbox[2]));
    const width = x1 - x0;
    if (width < 40) return null;
    const bin = 2;
    const n = Math.ceil(width / bin) + 1;
    const cover = new Float64Array(n);
    let top = Infinity;
    let bottom = -Infinity;
    for (const it of items) {
      top = Math.min(top, it.bbox[1]);
      bottom = Math.max(bottom, it.bbox[3]);
      const lo = Math.max(0, Math.floor((it.bbox[0] - x0) / bin));
      const hi = Math.min(n - 1, Math.ceil((it.bbox[2] - x0) / bin));
      const h = Math.max(1, it.bbox[3] - it.bbox[1]);
      for (let b = lo; b <= hi; b++) cover[b] += h;
    }
    const th = 0.14 * Math.max(1, bottom - top);
    let best = null;
    let start = -1;
    for (let b = 0; b <= n; b++) {
      const empty = b < n ? cover[b] < th : false;
      if (empty && start < 0) start = b;
      if (!empty && start >= 0) {
        if (b - start >= 3) {
          const gx0 = x0 + start * bin;
          const gx1 = x0 + b * bin;
          const left = items.filter((i) => i.bbox[2] <= gx0 + 1).length;
          const right = items.filter((i) => i.bbox[0] >= gx1 - 1).length;
          if (left >= 2 && right >= 2 && (!best || gx1 - gx0 > best[1] - best[0])) best = [gx0, gx1];
        }
        start = -1;
      }
    }
    return best;
  }

  function rowGaps(items) {
    const ordered = items.slice().sort((a, b) => a.bbox[1] - b.bbox[1]);
    const heights = ordered.map((i) => i.bbox[3] - i.bbox[1]).sort((a, b) => a - b);
    const minGap = Math.max(6, heights[Math.floor(heights.length / 2)] * 0.6);
    const cuts = [];
    let reach = ordered[0].bbox[3];
    for (const it of ordered.slice(1)) {
      if (it.bbox[1] - reach >= minGap) cuts.push(reach);
      reach = Math.max(reach, it.bbox[3]);
    }
    return cuts;
  }

  function xyCut(items, depth) {
    depth = depth || 0;
    if (items.length <= 1 || depth > 12) {
      return items.slice().sort((a, b) => a.bbox[1] - b.bbox[1] || a.bbox[0] - b.bbox[0]);
    }
    const g = gutters(items);
    if (g) {
      const mid = (g[0] + g[1]) / 2;
      const middle = items.filter((i) => i.bbox[0] < g[0] - 1 && i.bbox[2] > g[1] + 1);
      const left = [];
      const right = [];
      for (const it of items) {
        if (middle.includes(it)) continue;
        const cx = (it.bbox[0] + it.bbox[2]) / 2;
        (cx <= mid ? left : right).push(it);
      }
      if (left.length && right.length) {
        return [...xyCut(left, depth + 1), ...xyCut(middle, depth + 1), ...xyCut(right, depth + 1)];
      }
    }
    const cuts = rowGaps(items);
    if (cuts.length) {
      const ordered = items.slice().sort((a, b) => a.bbox[1] - b.bbox[1]);
      const bands = [];
      let current = [];
      let ci = 0;
      for (const it of ordered) {
        while (ci < cuts.length && it.bbox[1] > cuts[ci] + 0.5) {
          if (current.length) bands.push(current);
          current = [];
          ci++;
        }
        current.push(it);
      }
      if (current.length) bands.push(current);
      if (bands.length > 1) {
        const out = [];
        for (const band of bands) out.push(...xyCut(band, depth + 1));
        return out;
      }
    }
    return items.slice().sort((a, b) => a.bbox[1] - b.bbox[1] || a.bbox[0] - b.bbox[0]);
  }

  /* ---------------- main ---------------- */

  async function convertPdf(file, opts, onProgress) {
    onProgress = onProgress || (() => {});
    if (!window.pdfjsLib) throw new Error("pdf.js 未載入");
    if (!window.JSZip) throw new Error("JSZip 未載入");

    pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.js";
    const data = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(data) }).promise;

    const builder = new PS.DocxBuilder();
    const stats = { pages: pdf.numPages, ocr_pages: 0, text_lines: 0, images: 0, size: 0, warnings: [] };
    const useOcr = opts.ocr !== "never";
    let worker = null;

    for (let p = 1; p <= pdf.numPages; p++) {
      const page = await pdf.getPage(p);
      const vp1 = page.getViewport({ scale: 1 });
      const W = vp1.width;
      const H = vp1.height;
      onProgress(p - 1, pdf.numPages, `第 ${p}/${pdf.numPages} 頁`);

      if (p > 1) builder.pageBreak();
      builder._section(W, H);

      const tc = await page.getTextContent();
      let items = textLines(tc, vp1);
      const chars = items.reduce((n, l) => n + l.runs.reduce((m, r) => m + r.text.trim().length, 0), 0);

      let canvas = null;
      const needOcr = useOcr && (opts.ocr === "always" || chars < CFG.textThreshold);
      if (needOcr) {
        onProgress(p - 1, pdf.numPages, `第 ${p}/${pdf.numPages} 頁： OCR 辨識中`);
        if (!worker) worker = await getOcrWorker();
        const rendered = await renderPage(page, CFG.renderScale);
        canvas = rendered.canvas;
        const { data: res } = await worker.recognize(canvas);
        const scale = CFG.renderScale;
        items = (res.lines || [])
          .map((l) => {
            const b = l.bbox;
            const size = Math.max(6, ((b.y1 - b.y0) / scale) * 0.82);
            return {
              kind: "text",
              bbox: [b.x0 / scale, b.y0 / scale, b.x1 / scale, b.y1 / scale],
              runs: [{ text: l.text.replace(/\s+$/, ""), size, color: [0, 0, 0] }],
            };
          })
          .filter((l) => l.runs[0].text.trim());
        if (items.length) stats.ocr_pages += 1;
        else stats.warnings.push(`第 ${p} 頁 OCR 沒有辨識出文字`);
      }

      // figures: whatever ink is left once the text is masked out
      if (opts.images !== false) {
        if (!canvas) canvas = (await renderPage(page, CFG.renderScale)).canvas;
        const found = await findFigures(canvas, items, CFG.renderScale);
        items = items.filter((_, i) => !found.dropped.has(i)).concat(found.figures);
      }

      const ordered = xyCut(items);
      let prevBottom = 14;
      for (const el of ordered) {
        const before = Math.max(0, Math.min(26, el.bbox[1] - prevBottom));
        const indent = Math.max(0, el.bbox[0] - 14);
        if (el.kind === "text") {
          if (!el.runs.some((r) => r.text.trim())) continue;
          builder.paragraph(el.runs, { before, indent, pageW: W, pageH: H });
          stats.text_lines += 1;
        } else if (el.png) {
          builder.image(el.png, el.bbox[2] - el.bbox[0], el.bbox[3] - el.bbox[1], {
            before,
            indent,
            pageW: W,
            pageH: H,
          });
          stats.images += 1;
        }
        prevBottom = el.bbox[3];
      }
      page.cleanup();
    }

    if (worker) await worker.terminate().catch(() => {});
    workerPromise = null;

    onProgress(pdf.numPages, pdf.numPages, "產生 Word 檔");
    const blob = await builder.build(file.name.replace(/\.pdf$/i, ""));
    stats.size = blob.size;
    return { blob, stats };
  }

  PS.BrowserEngine = { convertPdf, available: () => !!(window.pdfjsLib && window.JSZip && window.Tesseract) };
})();
