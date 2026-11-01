/* Scanned-page OCR.

   Everything the browser engine needs to turn a raster page into clean Word
   text: page preparation (grey, shadow flattening, deskew, optional
   binarisation), recognition settings (language pack, layout mode), a
   confidence-driven second pass, and the text clean-up that turns tesseract's
   character-by-character Chinese output back into sentences.

   The recogniser itself is the vendored tesseract.js build; nothing here
   talks to the network. */
(function () {
  const PS = (window.PS = window.PS || {});

  /* language packs that ship with the site */
  const LANGS = {
    auto: "chi_tra+eng", // traditional Chinese documents may contain English
    trad: "chi_tra+eng",
    simp: "chi_sim+eng",
    eng: "eng",
  };

  const DEFAULTS = {
    dpi: 200, // 200 dpi is enough for 10 pt text; sharp mode goes to 300
    minConfidence: 80, // below this, a sharper second pass is worth the time
    retryDpi: 300, // sharpest pass worth paying for (400 dpi barely helps text)
    maxDpi: 400,
    skewLimit: 0.35, // degrees; below this rotating the page hurts more than it helps
  };

  const pool = new Map(); // langs -> Promise<Worker>
  let progressSink = null;

  /* ---------------- worker ---------------- */

  function workerFor(langs) {
    if (!pool.has(langs)) {
      const created = Tesseract.createWorker(langs, 1, {
        workerPath: "vendor/tesseract/worker.min.js",
        corePath: "vendor/tesseract/tesseract-core-simd.wasm.js",
        langPath: "vendor/tesseract/lang",
        workerBlobURL: false,
        logger: (m) => {
          if (progressSink && m.status === "recognizing text") progressSink(m.progress);
        },
      }).catch((err) => {
        pool.delete(langs);
        throw err;
      });
      pool.set(langs, created);
    }
    return pool.get(langs);
  }

  async function release() {
    const all = Array.from(pool.values());
    pool.clear();
    await Promise.all(all.map((p) => p.then((w) => w.terminate()).catch(() => {})));
  }

  function available() {
    return !!window.Tesseract;
  }

  /* ---------------- image preparation ---------------- */

  function makeCanvas(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return c;
  }

  function copyCanvas(src) {
    const c = makeCanvas(src.width, src.height);
    const g = c.getContext("2d", { willReadFrequently: true });
    g.fillStyle = "#fff";
    g.fillRect(0, 0, c.width, c.height);
    g.drawImage(src, 0, 0);
    return c;
  }

  function greyInPlace(data) {
    const hist = new Uint32Array(256);
    for (let i = 0; i < data.length; i += 4) {
      const v = (data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114) | 0;
      data[i] = data[i + 1] = data[i + 2] = v;
      hist[v]++;
    }
    return hist;
  }

  function percentile(hist, total, frac) {
    const target = total * frac;
    let acc = 0;
    for (let v = 0; v < 256; v++) {
      acc += hist[v];
      if (acc >= target) return v;
    }
    return 255;
  }

  /** rotate the page so text baselines are horizontal */
  function estimateSkew(canvas) {
    const maxW = 700;
    const s = Math.min(1, maxW / canvas.width);
    const w = Math.max(1, Math.round(canvas.width * s));
    const h = Math.max(1, Math.round(canvas.height * s));
    const small = makeCanvas(w, h);
    const g = small.getContext("2d", { willReadFrequently: true });
    g.fillStyle = "#fff";
    g.fillRect(0, 0, w, h);
    g.drawImage(canvas, 0, 0, w, h);

    const d = g.getImageData(0, 0, w, h).data;
    const xs = [];
    const ys = [];
    for (let p = 0, i = 0; i < d.length; i += 4, p++) {
      const v = (d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) | 0;
      if (v < 150) {
        xs.push(p % w);
        ys.push((p / w) | 0);
        if (xs.length > 60000) break;
      }
    }
    if (xs.length < 220) return 0;

    const bins = h + 8;
    const row = new Float64Array(bins);
    const variance = (deg) => {
      row.fill(0);
      const t = (deg * Math.PI) / 180;
      const sin = Math.sin(t);
      const cos = Math.cos(t);
      for (let i = 0; i < xs.length; i++) {
        const y = ys[i] * cos + xs[i] * sin + 4;
        const b = y | 0;
        if (b >= 0 && b < bins) row[b]++;
      }
      let sum = 0;
      for (let i = 0; i < bins; i++) sum += row[i];
      const mean = sum / bins;
      let acc = 0;
      for (let i = 0; i < bins; i++) acc += (row[i] - mean) * (row[i] - mean);
      return acc;
    };

    let best = 0;
    let bestDeg = 0;
    for (let deg = -4; deg <= 4.0001; deg += 0.5) {
      const v = variance(deg);
      if (v > best) {
        best = v;
        bestDeg = deg;
      }
    }
    for (let deg = bestDeg - 0.45; deg <= bestDeg + 0.4501; deg += 0.1) {
      const v = variance(deg);
      if (v > best) {
        best = v;
        bestDeg = deg;
      }
    }
    return Math.abs(bestDeg) < DEFAULTS.skewLimit ? 0 : bestDeg;
  }

  /** estimateSkew() reports the projection angle that straightens the page, so
      it is applied as-is; negating it would double the crookedness. */
  function rotateCanvas(src, deg) {
    const out = makeCanvas(src.width, src.height);
    const g = out.getContext("2d", { willReadFrequently: true });
    g.fillStyle = "#fff";
    g.fillRect(0, 0, out.width, out.height);
    g.translate(out.width / 2, out.height / 2);
    g.rotate((deg * Math.PI) / 180);
    g.drawImage(src, -src.width / 2, -src.height / 2);
    return out;
  }

  function otsu(hist, total) {
    let sum = 0;
    for (let t = 0; t < 256; t++) sum += t * hist[t];
    let sumB = 0;
    let wB = 0;
    let best = -1;
    let thr = 128;
    for (let t = 0; t < 256; t++) {
      wB += hist[t];
      if (!wB) continue;
      const wF = total - wB;
      if (!wF) break;
      sumB += t * hist[t];
      const mB = sumB / wB;
      const mF = (sum - sumB) / wF;
      const between = wB * wF * (mB - mF) * (mB - mF);
      if (between > best) {
        best = between;
        thr = t;
      }
    }
    return thr;
  }

  /**
   * Flat-field the page: estimate the local paper brightness on a coarse tile
   * grid and divide it out. This removes the shadow of a hand-held photo
   * without touching ink/paper contrast, which is what an aggressive
   * histogram stretch would ruin (it amplifies sensor noise instead).
   */
  function flattenShading(data, w, h, tile) {
    const tw = Math.ceil(w / tile);
    const th = Math.ceil(h / tile);
    const map = new Float32Array(tw * th);
    const hist = new Uint32Array(256);

    for (let ty = 0; ty < th; ty++) {
      const y0 = ty * tile;
      const y1 = Math.min(h, y0 + tile);
      for (let tx = 0; tx < tw; tx++) {
        const x0 = tx * tile;
        const x1 = Math.min(w, x0 + tile);
        hist.fill(0);
        let n = 0;
        for (let y = y0; y < y1; y++) {
          let i = (y * w + x0) * 4;
          for (let x = x0; x < x1; x++, i += 4) {
            hist[data[i]]++;
            n++;
          }
        }
        // 90th percentile of the tile is paper, unless the tile is all ink
        let acc = 0;
        let bg = 255;
        const want = n * 0.9;
        for (let v = 0; v < 256; v++) {
          acc += hist[v];
          if (acc >= want) {
            bg = v;
            break;
          }
        }
        map[ty * tw + tx] = bg < 40 ? 40 : bg;
      }
    }

    // even lighting? then dividing it out would only cost time and resampling
    const sorted = Float32Array.from(map).sort();
    const lo = sorted[Math.floor(sorted.length * 0.1)];
    const hi = sorted[Math.floor(sorted.length * 0.9)];
    if (hi - lo < 14) return { applied: false, lo, hi };

    // bilinear interpolation of the tile grid, then divide the paper out
    for (let y = 0; y < h; y++) {
      const fy = (y + 0.5) / tile - 0.5;
      let y0 = fy | 0;
      if (y0 < 0) y0 = 0;
      else if (y0 > th - 1) y0 = th - 1;
      const y1 = y0 + 1 > th - 1 ? th - 1 : y0 + 1;
      const wy = fy - y0 < 0 ? 0 : fy - y0 > 1 ? 1 : fy - y0;
      const rowA = y0 * tw;
      const rowB = y1 * tw;
      let i = y * w * 4;
      for (let x = 0; x < w; x++, i += 4) {
        const fx = (x + 0.5) / tile - 0.5;
        let x0 = fx | 0;
        if (x0 < 0) x0 = 0;
        else if (x0 > tw - 1) x0 = tw - 1;
        const x1 = x0 + 1 > tw - 1 ? tw - 1 : x0 + 1;
        const wx = fx - x0 < 0 ? 0 : fx - x0 > 1 ? 1 : fx - x0;
        const top = map[rowA + x0] * (1 - wx) + map[rowA + x1] * wx;
        const bottom = map[rowB + x0] * (1 - wx) + map[rowB + x1] * wx;
        const bg = top * (1 - wy) + bottom * wy;
        const v = (data[i] * 244) / bg;
        data[i] = data[i + 1] = data[i + 2] = v > 255 ? 255 : v;
      }
    }
    return { applied: true, lo, hi };
  }

  /**
   * Prepare a rendered page for recognition: grey, flip dark pages, flatten
   * uneven lighting, straighten a crooked scan and — only when asked — binarise.
   * Every step is bounded, so a clean digital render passes through almost
   * unchanged while a photographed page still gets its shadow removed.
   */
  function prepare(src, opts) {
    opts = opts || {};
    let canvas = copyCanvas(src);
    const info = { skew: 0, binarised: false, shading: false, uneven: 0, inverted: false };

    let g = canvas.getContext("2d", { willReadFrequently: true });
    let img = g.getImageData(0, 0, canvas.width, canvas.height);
    const d = img.data;
    const total = canvas.width * canvas.height;
    const hist = greyInPlace(d);

    // a dark page is a scanned negative or a night-mode photo: flip it
    if (percentile(hist, total, 0.5) < 110) {
      for (let i = 0; i < d.length; i += 4) {
        const v = 255 - d[i];
        d[i] = d[i + 1] = d[i + 2] = v;
      }
      hist.fill(0);
      for (let i = 0; i < d.length; i += 4) hist[d[i]]++;
      info.inverted = true;
    }

    if (opts.shading !== false) {
      const flat = flattenShading(d, canvas.width, canvas.height, 32);
      info.shading = flat.applied;
      info.uneven = Math.round(flat.hi - flat.lo);
    }
    g.putImageData(img, 0, 0);

    if (opts.deskew !== false) {
      const skew = estimateSkew(canvas);
      if (skew && Math.abs(skew) >= (opts.skewLimit || DEFAULTS.skewLimit)) {
        canvas = rotateCanvas(canvas, skew);
        info.skew = skew;
      }
    }

    if (opts.binarise === true) {
      g = canvas.getContext("2d", { willReadFrequently: true });
      img = g.getImageData(0, 0, canvas.width, canvas.height);
      const h2 = new Uint32Array(256);
      for (let i = 0; i < img.data.length; i += 4) h2[img.data[i]]++;
      const thr = otsu(h2, canvas.width * canvas.height);
      for (let i = 0; i < img.data.length; i += 4) {
        const v = img.data[i] < thr ? 0 : 255;
        img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
      }
      g.putImageData(img, 0, 0);
      info.binarised = true;
    }
    return { canvas, info };
  }

  /* ---------------- layout ---------------- */

  /**
   * How many text columns does the page have? Tesseract's automatic layout
   * mode (PSM 3) reorders and occasionally drops lines on single-column
   * pages; PSM 4 reads those cleanly but merges two columns into one line.
   * One cheap vertical projection tells us which case we are in.
   */
  function columnCount(canvas) {
    const width = 600;
    const height = Math.max(1, Math.round((canvas.height * width) / canvas.width));
    const small = makeCanvas(width, height);
    const g = small.getContext("2d", { willReadFrequently: true });
    g.fillStyle = "#fff";
    g.fillRect(0, 0, width, height);
    g.drawImage(canvas, 0, 0, width, height);
    const d = g.getImageData(0, 0, width, height).data;

    // ink per column, then close the gaps inside words: a gutter survives a
    // horizontal closing, the space between two characters does not
    const colInk = new Float64Array(width);
    let total = 0;
    for (let y = 0; y < height; y++) {
      const row = y * width;
      for (let x = 0; x < width; x++) {
        const i = (row + x) * 4;
        const v = (d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) | 0;
        if (v < 185) {
          colInk[x]++;
          total++;
        }
      }
    }
    if (total < 900) return 1;

    const radius = 5; // ~0.8 % of the page width, wider than any kerning gap
    const closed = new Float64Array(width);
    for (let x = 0; x < width; x++) {
      let sum = 0;
      for (let k = Math.max(0, x - radius); k <= Math.min(width - 1, x + radius); k++) sum += colInk[k];
      closed[x] = sum;
    }

    let start = -1;
    let left = 0;
    for (let x = 0; x <= width; x++) {
      const isGap = x < width && closed[x] === 0;
      if (isGap && start < 0) {
        start = x;
        left = colInk.subarray(0, x).reduce((a, b) => a + b, 0);
      }
      if (!isGap && start >= 0) {
        const gap = x - start;
        const right = x < width ? colInk.subarray(x).reduce((a, b) => a + b, 0) : 0;
        const centred = start > width * 0.1 && x < width * 0.9;
        if (gap >= width * 0.04 && centred && left > total * 0.22 && right > total * 0.22) {
          return 2;
        }
        start = -1;
      }
    }
    return 1;
  }

  /* ---------------- text clean-up ---------------- */

  const CJK = "\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff";
  const CJK_CHAR = new RegExp(`[${CJK}]`);
  const FULLWIDTH_PUNCT = { ",": "，", ";": "；", ":": "：", "!": "！", "?": "？" };

  /**
   * Tesseract treats every Chinese character as its own word, so its output is
   * full of spaces that must not survive into the Word file. It also prefers
   * ASCII punctuation. Both are repaired here; Latin text keeps its spacing.
   */
  function cleanText(text) {
    if (!text) return "";
    let t = String(text).replace(/[\u00ad\u200b-\u200d\ufeff]/g, "");

    // full-width digits and letters read better as their ASCII forms
    t = t.replace(/[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]/g, (c) =>
      String.fromCharCode(c.charCodeAt(0) - 0xfee0)
    );

    // no spaces between two CJK characters ("這 一 行" -> "這一行")
    t = t.replace(new RegExp(`([${CJK}])[ \\t]+(?=[${CJK}])`, "g"), "$1");
    // ... nor between a Chinese character and a number ("2025 年" -> "2025年")
    t = t.replace(new RegExp(`([${CJK}])[ \\t]+(?=[0-9])`, "g"), "$1");
    t = t.replace(new RegExp(`([0-9])[ \\t]+(?=[${CJK}])`, "g"), "$1");
    // a comma next to Chinese text is a Chinese comma, even after a number
    // ("平均分72.5, 英文科" -> "平均分72.5，英文科")
    t = t.replace(new RegExp(`([0-9])\\s*,\\s*(?=[${CJK}])`, "g"), "$1，");

    // ASCII punctuation that is clearly standing in for a Chinese one
    t = t.replace(new RegExp(`([${CJK}])\\s*([,;:!?])\\s*(?=[${CJK}]|$)`, "g"), (m, c, p) => c + FULLWIDTH_PUNCT[p]);
    t = t.replace(new RegExp(`([${CJK}])[ \\t]*\\.\\s*(?=[${CJK}]|$)`, "g"), "$1。");
    t = t.replace(new RegExp(`([${CJK}])\\s*\\?`, "g"), "$1？");
    t = t.replace(new RegExp(`([${CJK}])\\s*!`, "g"), "$1！");

    // spaces that should never sit next to CJK punctuation
    t = t.replace(/[ \t]+(?=[，。、；：！？）】》」』])/g, "");
    t = t.replace(/([（【《「『])[ \t]+/g, "$1");

    // "1, 250.00" and "3 . 5" are spacing accidents, not word gaps
    t = t.replace(/(\d)[ \t]*,[ \t]*(?=\d{3}\b)/g, "$1,");
    t = t.replace(/(\d)[ \t]*\.[ \t]*(?=\d)/g, "$1.");

    // tesseract ends every line with a newline; wrapped lines become one
    t = t.replace(/\s*[\r\n]+\s*/g, " ");
    t = t.replace(/[ \t]{2,}/g, " ");
    return t.trim();
  }

  /** lines that are pure recognition noise, e.g. a lone "-" at 12 % confidence */
  function isJunk(text, confidence) {
    const t = text.replace(/\s+/g, "");
    if (!t) return true;
    if (confidence >= 45) return false;
    if (t.length > 2) return false;
    if (CJK_CHAR.test(t)) return false;
    if (/[0-9A-Za-z]/.test(t) && confidence >= 30) return false;
    return true;
  }

  /* ---------------- recognition ---------------- */

  function median(values) {
    if (!values.length) return 0;
    const s = values.slice().sort((a, b) => a - b);
    return s[s.length >> 1];
  }

  function linesFromResult(data, scale) {
    const lines = [];
    for (const l of data.lines || []) {
      const text = cleanText(l.text);
      if (!text.trim()) continue;
      const conf = typeof l.confidence === "number" ? l.confidence : 0;
      if (isJunk(text, conf)) continue;

      const words = l.words || [];
      const heights = words.filter((w) => w.bbox).map((w) => w.bbox.y1 - w.bbox.y0);
      const emPx = median(heights) || l.bbox.y1 - l.bbox.y0;
      const size = Math.max(6, (emPx / scale) * 0.86);
      const bold = words.length ? words.filter((w) => w.is_bold).length / words.length > 0.6 : false;
      const italic = words.length ? words.filter((w) => w.is_italic).length / words.length > 0.6 : false;

      lines.push({
        kind: "text",
        bbox: [l.bbox.x0 / scale, l.bbox.y0 / scale, l.bbox.x1 / scale, l.bbox.y1 / scale],
        confidence: conf,
        runs: [{ text, size, bold, italic, color: [0, 0, 0] }],
      });
    }
    return lines;
  }

  async function recogniseOnce(page, w, scale, opts) {
    const rendered = await opts.render(scale);
    // the renderer may clamp the scale to stay inside the memory budget
    const used = rendered.scale || scale;
    let canvas = rendered.canvas;
    if (opts.prepare) {
      const prepared = prepare(canvas, opts);
      canvas = prepared.canvas;
      opts.info = prepared.info;
    }
    const psm = opts.psm || (columnCount(rendered.canvas) > 1 ? "3" : "4");
    await w.setParameters({
      tessedit_pageseg_mode: String(psm),
      preserve_interword_spaces: "1",
    });
    const { data } = await w.recognize(canvas, {}, { blocks: true, text: true });
    const lines = linesFromResult(data, used);
    const chars = lines.reduce((n, l) => n + l.runs[0].text.replace(/\s/g, "").length, 0);
    return {
      lines,
      scale: used,
      raw: rendered.canvas,
      res: data,
      confidence: typeof data.confidence === "number" ? data.confidence : 0,
      psm,
      chars,
      info: opts.info || {},
    };
  }

  /**
   * Recognise one page.
   *
   * opts.render(scale) must return {canvas} for the given scale — the caller
   * owns pdf.js, so the engine can ask for a sharper render when the first
   * pass looks weak.
   */
  async function recognizePage(page, opts) {
    opts = opts || {};
    if (!available()) throw new Error("tesseract.js 未載入");

    const langs = LANGS[opts.lang] || LANGS.auto;
    const requested = Math.round(opts.dpi || DEFAULTS.dpi);
    const w = await workerFor(langs);

    const pass = function (dpi, extra) {
      const over = extra || {};
      return recogniseOnce(page, w, dpi / 72, {
        render: opts.render,
        // undefined lets recogniseOnce pick the layout mode from the page
        psm: over.psm || opts.psm,
        prepare: opts.prepare !== false,
        // undefined means "decide from the histogram"
        binarise: "binarise" in over ? over.binarise : opts.binarise,
        deskew: "deskew" in over ? over.deskew : opts.deskew,
      });
    };

    progressSink = opts.onProgress || null;
    try {
      const first = await pass(requested);
      let best = first;
      let retried = false;

      // weak first pass (blurred scan, tiny print, wrong language): one sharper
      // attempt, then keep whichever page actually read better
      if (!opts.noRetry && first.confidence < DEFAULTS.minConfidence) {
        const sharp = Math.min(DEFAULTS.retryDpi, DEFAULTS.maxDpi);
        if (sharp > requested) {
          retried = true;
          const second = await pass(sharp, {});
          const score = (r) => r.confidence + Math.min(12, r.chars / 40);
          if (score(second) > score(first) && second.chars > 0) best = second;
        }
      }

      best.retried = retried;
      best.psm = best.psm || "4";
      best.langs = langs;
      best.confidence = Math.round(best.confidence);
      return best;
    } finally {
      progressSink = null;
    }
  }

  PS.OcrEngine = {
    available,
    release,
    prepare,
    cleanText,
    recognizePage,
    LANGS,
    DEFAULTS,
    columnCount,
  };
})();
