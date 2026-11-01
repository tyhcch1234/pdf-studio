/* Finding maths on a page and handing it to the OMML writer.

   Two sources of glyphs:
     * digital PDFs  -> pdf.js text content (positions are exact)
     * scanned pages -> Tesseract symbol boxes (positions are approximate)

   For each candidate line we measure how "mathy" it looks (symbol density,
   math fonts, raised/lowered glyphs), then look for horizontal rules in the
   rendered page — a rule between two stacked blocks is a fraction bar. Only
   when the recogniser is confident does the line leave the text stream, so a
   failed guess falls back to the plain text we already had. */
(function () {
  const PS = (window.PS = window.PS || {});
  const MO = () => PS.MathOMML;

  const MIN_CONFIDENCE = 0.55;

  /* ------------------------------------------------------------------ *
   * glyphs
   * ------------------------------------------------------------------ */

  function toDevice(vp, transform) {
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

  /** one token per glyph, with the baseline pdf.js reports */
  function tokensFromText(textContent, vp) {
    const out = [];
    for (const it of textContent.items) {
      if (!it.str) continue;
      const m = toDevice(vp, it.transform);
      const size = Math.hypot(m[2], m[3]) || Math.hypot(m[0], m[1]) || 10;
      const total = it.width || size * it.str.length * 0.5;
      const per = total / Math.max(1, it.str.length);
      let cx = m[4];
      const chars = Array.from(it.str);
      for (const ch of chars) {
        if (ch.trim()) out.push({ ch, x: cx, y: m[5], size, w: per, font: it.fontName || "" });
        cx += per;
      }
    }
    return out;
  }

  /** Tesseract blocks -> tokens (it reports per symbol boxes) */
  function tokensFromOcr(res, scale) {
    const out = [];
    const seen = new Set();
    const push = (s) => {
      const text = (s.text || "").trim();
      if (!text || !s.bbox) return;
      const b = s.bbox;
      const key = `${Math.round(b.x0)},${Math.round(b.y0)},${text}`;
      if (seen.has(key)) return; // the same symbol is listed at several depths
      seen.add(key);
      out.push({
        ch: text,
        x: b.x0 / scale,
        y: b.y1 / scale,
        size: Math.max(4, ((b.y1 - b.y0) / scale) * 0.9),
        w: (b.x1 - b.x0) / scale,
        font: "",
      });
    };
    const walk = (node) => {
      if (!node) return;
      if (Array.isArray(node)) return node.forEach(walk);
      if (node.words) return walk(node.words);
      if (node.symbols) return node.symbols.forEach(push);
      for (const key of ["blocks", "paragraphs", "lines"]) if (node[key]) walk(node[key]);
    };
    walk(res);
    return out;
  }

  /* ------------------------------------------------------------------ *
   * line grouping + mathiness
   * ------------------------------------------------------------------ */

  const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff]/;
  const OP_CHARS = new Set([..."+-−×÷±∓=/<>≤≥≠≈≡≅∼√∛∜∫∬∭∮∑∏∈∉⊂⊃⊆⊇∪∩∞∂∇→←↔⇒⇐⇔⋅·∗^_±"]);
  const PROSE_SIZE_TOL = 0.55;

  function rowsOf(tokens) {
    const rows = [];
    const sorted = tokens.slice().sort((a, b) => a.y - b.y || a.x - b.x);
    for (const t of sorted) {
      let best = null;
      let bestDy = Infinity;
      for (const r of rows) {
        const dy = Math.abs(r.y - t.y);
        if (dy > Math.max(r.size, t.size) * 1.1) continue;
        let ok = dy <= Math.max(r.size, t.size) * PROSE_SIZE_TOL;
        // a raised / lowered small glyph belongs to the line it sits on
        if (!ok && t.size <= r.size * 0.86 && dy <= r.size * 0.95) ok = true;
        if (!ok && r.size <= t.size * 0.86 && dy <= t.size * 0.95) ok = true;
        if (ok && dy < bestDy) { bestDy = dy; best = r; }
      }
      if (!best) {
        best = { y: t.y, size: t.size, tokens: [] };
        rows.push(best);
      }
      best.tokens.push(t);
      best.size = Math.max(best.size, t.size);
    }

    for (const r of rows) {
      r.tokens.sort((a, b) => a.x - b.x);
      // the line's own size is the most common one; scripts are the minority
      const counts = new Map();
      for (const t of r.tokens) {
        const key = Math.round(t.size * 2) / 2;
        counts.set(key, (counts.get(key) || 0) + 1);
      }
      let domSize = 0;
      let domCount = -1;
      for (const [k, c] of counts) {
        if (c > domCount || (c === domCount && k > domSize)) { domSize = k; domCount = c; }
      }
      r.size = domSize || r.size;
      const main = r.tokens.filter((t) => Math.abs(Math.round(t.size * 2) / 2 - r.size) < 0.01);
      const ys = (main.length ? main : r.tokens).map((t) => t.y).sort((a, b) => a - b);
      r.y = ys[Math.floor(ys.length / 2)];

      const x0 = Math.min(...r.tokens.map((t) => t.x));
      const x1 = Math.max(...r.tokens.map((t) => t.x + Math.max(2, t.w || t.size * 0.5)));
      const top = Math.min(...r.tokens.map((t) => t.y - t.size * 0.78));
      const bottom = Math.max(...r.tokens.map((t) => t.y + t.size * 0.26));
      r.bbox = [x0, top, x1, bottom];
      r.text = r.tokens.map((t) => t.ch).join("");
      r.signals = signalsOf(r);
      r.score = scoreRow(r);
    }
    return rows;
  }

  function signalsOf(row) {
    const toks = row.tokens;
    const n = Math.max(1, toks.length);
    let mathChars = 0;
    let mathFont = 0;
    let ops = 0;
    let relations = 0;
    let letters = 0;
    let digits = 0;
    let scripts = 0;
    for (const t of toks) {
      if (MO().isMathChar(t.ch)) mathChars++;
      if (MO().isMathFont(t.font)) mathFont++;
      if (OP_CHARS.has(t.ch)) ops++;
      if (MO().isRelation(t.ch)) relations++;
      if (MO().isLatin(t.ch)) letters++;
      if (MO().isDigit(t.ch)) digits++;
      const dy = t.y - row.y;
      if (t.size <= row.size * 0.85 && Math.abs(dy) > row.size * 0.12) scripts++;
    }
    const nonSpace = toks.filter((t) => t.ch.trim()).length;
    return {
      chars: nonSpace,
      mathChars,
      mathFontRatio: mathFont / n,
      ops,
      mathCharRatio: mathChars / Math.max(1, nonSpace),
      relations,
      letters,
      digits,
      scripts,
      wordRatio: letters / Math.max(1, nonSpace),
    };
  }

  function scoreRow(row) {
    const s = row.signals;
    let score = 0;
    score += Math.min(3, s.ops * 0.6);
    score += Math.min(2.4, s.scripts * 0.9);
    if (s.ops && (s.scripts || s.digits)) score += 0.8;
    if (s.scripts && s.chars <= 60) score += 0.4;
    if (s.digits && s.ops >= 2) score += 0.4;
    // prose: words only, nothing to typeset
    if (s.wordRatio > 0.75 && s.ops === 0 && s.scripts === 0) score -= 3;
    if (s.wordRatio > 0.7 && s.chars > 40) score -= 1.5;
    if (s.chars > 90) score -= 1;
    return score;
  }

  /* ------------------------------------------------------------------ *
   * fraction bars / overbars from pixels
   * ------------------------------------------------------------------ */

  /**
   * Horizontal rules inside a region, in page points.
   * A rule must be thin, wide enough to be a fraction bar and must not run
   * into the crop border (that would be a table frame or an underline).
   */
  function findRules(canvas, bbox, scale) {
    const cx0 = Math.max(0, Math.floor(bbox[0] * scale) - 2);
    const cy0 = Math.max(0, Math.floor(bbox[1] * scale) - 2);
    const cx1 = Math.min(canvas.width, Math.ceil(bbox[2] * scale) + 2);
    const cy1 = Math.min(canvas.height, Math.ceil(bbox[3] * scale) + 2);
    const w = cx1 - cx0;
    const h = cy1 - cy0;
    if (w < 8 || h < 6) return [];

    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const data = ctx.getImageData(cx0, cy0, w, h).data;
    const dark = (x, y) => {
      const i = (y * w + x) * 4;
      return data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114 < 170;
    };

    const minRun = Math.max(10, Math.round(w * 0.22));
    const bars = [];
    let current = null;
    for (let y = 0; y < h; y++) {
      let best = 0;
      let bestStart = 0;
      let run = 0;
      let start = 0;
      for (let x = 0; x < w; x++) {
        if (dark(x, y)) {
          if (run === 0) start = x;
          run++;
          if (run > best) { best = run; bestStart = start; }
        } else run = 0;
      }
      // one clipped end is fine (the bar of a fraction can start outside the
      // glyph box); both ends clipped means a frame or a rule of the layout
      const clippedLeft = bestStart <= 1;
      const clippedRight = bestStart + best >= w - 1;
      const solid = best >= minRun && !(clippedLeft && clippedRight);
      if (solid) {
        if (current && y === current.y1 + 1) {
          current.y1 = y;
          current.x0 = Math.min(current.x0, bestStart);
          current.x1 = Math.max(current.x1, bestStart + best);
        } else {
          current = { y0: y, y1: y, x0: bestStart, x1: bestStart + best };
          bars.push(current);
        }
      } else current = null;
    }

    const out = [];
    for (const b of bars) {
      const thick = b.y1 - b.y0 + 1;
      if (thick > Math.max(5, Math.round(scale * 3.2))) continue;
      // whitespace above and below keeps glyph strokes from counting as bars
      const lightBand = (from, to) => {
        let light = 0;
        let total = 0;
        for (let y = Math.max(0, from); y < Math.min(h, to); y++) {
          for (let x = b.x0; x < b.x1; x++) {
            total++;
            if (!dark(x, y)) light++;
          }
        }
        return total === 0 ? 1 : light / total;
      };
      if (lightBand(b.y0 - 3, b.y0) < 0.55) continue;
      if (lightBand(b.y1 + 1, b.y1 + 4) < 0.55) continue;
      out.push({
        x0: (cx0 + b.x0) / scale,
        x1: (cx0 + b.x1) / scale,
        y0: (cy0 + b.y0) / scale,
        y1: (cy0 + b.y1 + 1) / scale,
      });
    }
    return out;
  }

  /* ------------------------------------------------------------------ *
   * region assembly
   * ------------------------------------------------------------------ */

  function overlapRatio(a, b) {
    const x0 = Math.max(a[0], b[0]);
    const x1 = Math.min(a[2], b[2]);
    if (x1 <= x0) return 0;
    return (x1 - x0) / Math.max(1, Math.min(a[2] - a[0], b[2] - b[0]));
  }

  function unionBox(a, b) {
    return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
  }

  function boxOfToken(t) {
    return [t.x, t.y - t.size * 0.78, t.x + Math.max(1.5, t.w || t.size * 0.5), t.y + t.size * 0.26];
  }

  function boxesTouch(a, b, pad) {
    return !(a[2] + pad < b[0] || b[2] + pad < a[0] || a[3] + pad < b[1] || b[3] + pad < a[1]);
  }

  /** glyphs that overlap vertically form one visual unit (x, x², √(...) …) */
  function clustersOf(tokens) {
    const boxes = tokens.map(boxOfToken);
    const parent = tokens.map((_, i) => i);
    const find = (i) => {
      while (parent[i] !== i) {
        parent[i] = parent[parent[i]];
        i = parent[i];
      }
      return i;
    };
    const union = (a, b) => {
      const ra = find(a);
      const rb = find(b);
      if (ra !== rb) parent[rb] = ra;
    };
    for (let i = 0; i < tokens.length; i++) {
      for (let j = i + 1; j < tokens.length; j++) {
        const a = tokens[i];
        const b = tokens[j];
        const pad = Math.min(a.size, b.size) * 0.12;
        if (boxesTouch(boxes[i], boxes[j], pad)) { union(i, j); continue; }
        // same baseline and almost adjacent: one run of the same formula
        const sameLine = Math.abs(a.y - b.y) <= Math.max(a.size, b.size) * 0.14;
        if (!sameLine) continue;
        const size = Math.max(a.size, b.size);
        // per-glyph advances are approximate (an item's width is spread over
        // its characters), so the tolerance has to be generous
        const gap = a.x < b.x ? b.x - (a.x + (a.w || 0)) : a.x - (b.x + (b.w || 0));
        if (gap < size * 0.55) union(i, j);
      }
    }
    const map = new Map();
    for (let i = 0; i < tokens.length; i++) {
      const r = find(i);
      if (!map.has(r)) map.set(r, []);
      map.get(r).push(tokens[i]);
    }
    const clusters = [];
    for (const toks of map.values()) {
      toks.sort((a, b) => a.x - b.x);
      const sizes = toks.map((t) => t.size).sort((a, b) => a - b);
      const size = sizes[sizes.length - 1];
      const main = toks.filter((t) => t.size >= size * 0.94);
      const ys = (main.length ? main : toks).map((t) => t.y).sort((a, b) => a - b);
      const box = toks.reduce((acc, t) => unionBox(acc, boxOfToken(t)), boxOfToken(toks[0]));
      const c = {
        tokens: toks,
        size,
        y: ys[Math.floor(ys.length / 2)],
        box,
        text: toks.map((t) => t.ch).join(""),
      };
      c.signals = signalsOf({ tokens: toks, size: c.size, y: c.y, bbox: box });
      c.score = scoreRow({ signals: c.signals });
      clusters.push(c);
    }
    return clusters;
  }

  /** detached limits (∑ with sub/sup drawn apart) join their operator */
  function mergeLimits(clusters) {
    const big = clusters.filter((c) => Array.from(c.text).some((ch) => MO().NARY_CHARS[ch]));
    if (!big.length) return clusters;
    const used = new Set();
    for (const op of big) {
      for (const c of clusters) {
        if (c === op || used.has(c) || big.indexOf(c) >= 0) continue;
        if (c.size > op.size * 0.9) continue;
        const cc = (c.box[0] + c.box[2]) / 2;
        if (cc < op.box[0] - op.size * 0.3 || cc > op.box[2] + op.size * 0.3) continue;
        const gap = c.y > op.y ? c.box[1] - op.box[3] : op.box[1] - c.box[3];
        if (gap > op.size * 1.15) continue;
        op.tokens = op.tokens.concat(c.tokens);
        op.box = unionBox(op.box, c.box);
        used.add(c);
      }
    }
    return clusters.filter((c) => !used.has(c));
  }

  /** two stacked blocks with a rule between them are one fraction */
  function mergeFractions(clusters, canvas, scale) {
    if (!canvas) return clusters;
    const merged = new Set();
    const out = [];
    const sorted = clusters.slice().sort((a, b) => a.box[1] - b.box[1]);
    for (let i = 0; i < sorted.length; i++) {
      const top = sorted[i];
      if (merged.has(top)) continue;
      let box = top.box;
      let tokens = top.tokens;
      let changed = false;
      for (let j = i + 1; j < sorted.length; j++) {
        const bot = sorted[j];
        if (merged.has(bot)) continue;
        const maxSize = Math.max(top.size, bot.size);
        const gap = bot.box[1] - box[3];
        if (gap > maxSize * 1.9) break;
        if (overlapRatio(box, bot.box) < 0.3) continue;
        const testBox = unionBox(box, bot.box);
        const probe = [testBox[0] - maxSize * 0.8, testBox[1], testBox[2] + maxSize * 0.8, testBox[3]];
        const bars = findRules(canvas, probe, scale).filter(
          (b) => b.y0 > box[3] - maxSize * 0.6 && b.y1 < bot.box[3] + maxSize * 0.2
        );
        if (!bars.length) continue;
        tokens = tokens.concat(bot.tokens);
        box = testBox;
        merged.add(bot);
        changed = true;
      }
      out.push(
        changed
          ? Object.assign({}, top, {
              tokens,
              box,
              text: tokens.map((t) => t.ch).join(""),
            })
          : top
      );
    }
    return out;
  }

  /** blocks sharing a band form one line of typesetting */
  function lineRegions(clusters) {
    const regions = [];
    for (const c of clusters.slice().sort((a, b) => a.box[1] - b.box[1])) {
      const hit = regions.find((r) => {
        const size = Math.max(r.size, c.size);
        const vGap = c.box[3] < r.box[1] ? r.box[1] - c.box[3] : c.box[1] - r.box[3];
        if (vGap > size * 0.25) return false;
        const hGap = c.box[0] > r.box[2] ? c.box[0] - r.box[2] : r.box[0] - c.box[2];
        return hGap < size * 2.2;
      });
      if (hit) {
        hit.clusters.push(c);
        hit.box = unionBox(hit.box, c.box);
        hit.size = Math.max(hit.size, c.size);
      } else {
        regions.push({ clusters: [c], box: c.box.slice(), size: c.size });
      }
    }
    return regions;
  }

  /**
   * @param {HTMLCanvasElement} canvas rendered page (or null: no bar detection)
   * @param {Array} tokens glyph tokens in page points
   * @param {Object} opts {scale}
   * @returns {{equations:Array, covered:Array, examined:number, rejected:number}}
   */
  function detect(canvas, tokens, opts) {
    opts = opts || {};
    const scale = opts.scale || 2;
    if (!tokens || !tokens.length) return { equations: [], covered: [], examined: 0, rejected: 0 };

    let clusters = clustersOf(tokens);
    clusters = mergeLimits(clusters);
    clusters = mergeFractions(clusters, canvas, scale);

    const regions = lineRegions(clusters);
    const equations = [];
    const covered = [];
    let rejected = 0;

    for (const region of regions) {
      const regionTokens = region.clusters.reduce((acc, c) => acc.concat(c.tokens), []);
      if (regionTokens.length < 2) continue;

      // page-level signals, so an inline formula inside prose is left alone
      const sig = signalsOf({ tokens: regionTokens, size: region.size, y: region.clusters[0].y, bbox: region.box });
      const content = regionTokens.filter((t) => t.ch.trim()).length;
      if (!content) continue;
      const text = regionTokens.map((t) => t.ch).join("");
      if (CJK.test(text)) continue;
      const mathish = (sig.ops + sig.scripts + sig.mathChars) / content;
      const score = scoreRow({ signals: sig });
      if (score < 2.2 || mathish < 0.28 || sig.wordRatio > 0.62) continue;
      if (opts.strict) {
        // OCR glyph boxes are noisy: only trust short lines with real scripts
        if (!sig.scripts || !sig.ops || content > 42) continue;
      }

      const pad = region.size * 0.55;
      const rules = canvas
        ? findRules(canvas, [region.box[0] - pad, region.box[1], region.box[2] + pad, region.box[3]], scale)
        : [];
      const res = MO().parse(regionTokens, { rules });
      const minConf = opts.strict ? 0.75 : MIN_CONFIDENCE;
      if (!res || res.confidence < minConf || res.structures === 0) {
        rejected++;
        continue;
      }
      equations.push({
        kind: "equation",
        bbox: region.box,
        omml: res.omml,
        latex: res.latex,
        confidence: res.confidence,
        chars: res.chars,
      });
      covered.push({ bbox: region.box });
    }

    return { equations, covered, examined: regions.length, rejected };
  }

  /** drop the text elements that an equation replaced */
  function removeCovered(items, covered) {
    if (!covered.length) return items;
    const boxes = covered.map((r) => r.bbox);
    return items.filter((el) => {
      if (el.kind !== "text") return true;
      const cx = (el.bbox[0] + el.bbox[2]) / 2;
      const cy = (el.bbox[1] + el.bbox[3]) / 2;
      return !boxes.some((b) => cx >= b[0] - 2 && cx <= b[2] + 2 && cy >= b[1] - 2 && cy <= b[3] + 2);
    });
  }

  PS.MathDetect = {
    tokensFromText,
    tokensFromOcr,
    rowsOf,
    findRules,
    detect,
    clustersOf,
    mergeLimits,
    mergeFractions,
    lineRegions,
    removeCovered,
    MIN_CONFIDENCE,
    available: () => !!(PS.MathOMML && PS.MathOMML.parse),
  };
})();
