/* Math structure recognition -> OMML (Office Math Markup Language).

   Input is a list of positioned glyphs — either recovered from the PDF text
   layer (pdf.js) or from Tesseract symbol boxes — and the output is a real
   Word equation (`<m:oMath>`), so the converted .docx keeps editable maths
   instead of a screenshot.

   The recogniser works on geometry, the way a human reads a formula:
     * baseline + size differences become superscripts / subscripts
     * horizontal rules become fraction bars, their overbar a radical
     * big operators (∑ ∫ ∏) collect the limits drawn above/below them
   Everything it cannot explain with confidence is reported through
   `confidence`, and the caller falls back to plain text / the page image. */
(function () {
  const PS = (window.PS = window.PS || {});

  /* ------------------------------------------------------------------ *
   * character tables
   * ------------------------------------------------------------------ */

  const MATH_FONT_HINTS = [
    "cmmi", "cmsy", "cmex", "cmr", "cmb", "msam", "msbm", "mtmi", "mtsy", "mtex",
    "symbol", "cambria math", "stix", "xits", "asana", "lmm", "lmroman", "latin modern",
    "euclid", "mathjax", "noto sans math", "dejavu math", "texgyre", "math",
  ];

  // operators that bind two sides (used for spacing + n-ary operand ends)
  const RELATIONS = new Set([..."=<>≤≥≠≈≡≅∝≪≫∈∉⊂⊃⊆⊇→←↔⇒⇐⇔∴∵≡∼≃≟∣∤∼"]);
  const ADDITIVE = new Set([..."+-−±∓×÷⋅·∗∗"]);
  const OPENERS = new Set([..."([{⟨⌈⌊«"]);
  const CLOSERS = new Set([...")]}\\⟩⌉⌋»"]);
  const NOT_OPERAND_END = new Set([..."=+-−±,;:"]);

  const NARY_CHARS = {
    "∑": { limits: true, latex: "\\sum" },
    "∏": { limits: true, latex: "\\prod" },
    "∐": { limits: true, latex: "\\coprod" },
    "⋃": { limits: true, latex: "\\bigcup" },
    "⋂": { limits: true, latex: "\\bigcap" },
    "⋁": { limits: true, latex: "\\bigvee" },
    "⋀": { limits: true, latex: "\\bigwedge" },
    "⨁": { limits: true, latex: "\\bigoplus" },
    "⨂": { limits: true, latex: "\\bigotimes" },
    "∫": { limits: false, latex: "\\int" },
    "∬": { limits: false, latex: "\\iint" },
    "∭": { limits: false, latex: "\\iiint" },
    "∮": { limits: false, latex: "\\oint" },
    "∯": { limits: false, latex: "\\oiint" },
  };

  // characters that may only appear inside a formula
  const MATH_ONLY = new Set([
    ..."∑∏∐∫∬∭∮∯√∛∜∞∂∇∠⊥∥°′″∀∃∄∅∈∉∋⊂⊃⊆⊇∪∩∧∨¬⊕⊗⊙≤≥≠≈≡≅∝≪≫±∓×÷⋅·∗←→↔⇒⇐⇔⇔⌈⌉⌊⌋⟨⟩≃≟≜∴∵∼≁≪≫∝⊤⊢⊨ℵℏⅅⅆ",
  ]);

  const MATH_LATEX = {
    "×": "\\times", "÷": "\\div", "±": "\\pm", "∓": "\\mp", "≤": "\\le", "≥": "\\ge",
    "≠": "\\ne", "≈": "\\approx", "≡": "\\equiv", "∞": "\\infty", "∂": "\\partial",
    "∇": "\\nabla", "√": "\\sqrt", "∑": "\\sum", "∏": "\\prod", "∫": "\\int",
    "⋅": "\\cdot", "·": "\\cdot", "→": "\\to", "←": "\\leftarrow", "↔": "\\leftrightarrow",
    "⇒": "\\Rightarrow", "⇐": "\\Leftarrow", "⇔": "\\Leftrightarrow", "∈": "\\in",
    "∉": "\\notin", "⊂": "\\subset", "⊃": "\\supset", "⊆": "\\subseteq", "⊇": "\\supseteq",
    "∪": "\\cup", "∩": "\\cap", "∅": "\\emptyset", "∀": "\\forall", "∃": "\\exists",
    "∠": "\\angle", "⊥": "\\perp", "∥": "\\parallel", "′": "'", "″": "''", "°": "^{\\circ}",
    "∴": "\\therefore", "∵": "\\because", "±": "\\pm", "−": "-", "∝": "\\propto",
    "≃": "\\simeq", "≅": "\\cong", "∗": "*", "°": "^{\\circ}", "…": "\\ldots",
  };

  const GREEK_LATEX = {
    "α": "\\alpha", "β": "\\beta", "γ": "\\gamma", "δ": "\\delta", "ε": "\\epsilon",
    "ζ": "\\zeta", "η": "\\eta", "θ": "\\theta", "ι": "\\iota", "κ": "\\kappa",
    "λ": "\\lambda", "μ": "\\mu", "ν": "\\nu", "ξ": "\\xi", "π": "\\pi", "ρ": "\\rho",
    "σ": "\\sigma", "τ": "\\tau", "υ": "\\upsilon", "φ": "\\phi", "χ": "\\chi",
    "ψ": "\\psi", "ω": "\\omega", "Γ": "\\Gamma", "Δ": "\\Delta", "Θ": "\\Theta",
    "Λ": "\\Lambda", "Ξ": "\\Xi", "Π": "\\Pi", "Σ": "\\Sigma", "Υ": "\\Upsilon",
    "Φ": "\\Phi", "Ψ": "\\Psi", "Ω": "\\Omega",
  };

  const FUNCTION_NAMES = [
    "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "coth", "sin", "cos", "tan",
    "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "max", "min", "sup", "inf",
    "det", "gcd", "lcm", "mod", "deg", "arg", "dim", "ker", "hom", "Pr", "limsup", "liminf",
  ];
  const LIMIT_FUNCTIONS = new Set(["lim", "max", "min", "sup", "inf", "det", "gcd", "limsup", "liminf"]);

  function isMathFont(font) {
    const f = String(font || "").toLowerCase();
    if (!f) return false;
    // strip subset prefixes like "ABCDEE+CambriaMath"
    return MATH_FONT_HINTS.some((h) => f.indexOf(h) >= 0);
  }

  function isMathChar(ch) {
    if (!ch) return false;
    if (MATH_ONLY.has(ch)) return true;
    const cp = ch.codePointAt(0);
    return (cp >= 0x0370 && cp <= 0x03ff) || (cp >= 0x1d400 && cp <= 0x1d7ff);
  }

  function isRelation(ch) {
    return RELATIONS.has(ch);
  }

  function isDigit(ch) {
    return ch >= "0" && ch <= "9";
  }

  function isLatin(ch) {
    return /^[A-Za-z]$/.test(ch);
  }

  /** mathematical alphanumeric symbols -> plain ASCII (Word renders the rest) */
  function dematheLetters(ch) {
    const cp = ch.codePointAt(0);
    if (cp < 0x1d400 || cp > 0x1d7ff) return ch;
    if (cp >= 0x1d7ce && cp <= 0x1d7ff) {
      // digits, five consecutive styles of 10
      return String((cp - 0x1d7ce) % 10);
    }
    const k = cp - 0x1d400;
    const idx = k % 52;
    if (idx < 26) return String.fromCharCode(65 + idx);
    return String.fromCharCode(97 + idx - 26);
  }

  /** drop glyphs Word cannot show, keep everything meaningful */
  function normalizeChar(ch) {
    if (!ch) return "";
    if (ch.length > 2) return ch;
    const cp = ch.codePointAt(0);
    if (cp >= 0x1d400 && cp <= 0x1d7ff) return dematheLetters(ch);
    if ((cp >= 0xe000 && cp <= 0xf8ff) || cp === 0xfffd) return ""; // private use / broken glyph
    if (cp < 0x20) return "";
    if (cp === 0xa0) return " ";
    if (cp === 0x00b7) return "·";
    return ch;
  }

  function latexOf(ch) {
    if (GREEK_LATEX[ch]) return GREEK_LATEX[ch];
    if (MATH_LATEX[ch]) return MATH_LATEX[ch];
    if (NARY_CHARS[ch]) return NARY_CHARS[ch].latex;
    if (ch === "\\") return "\\backslash";
    if (ch === "{") return "\\{";
    if (ch === "}") return "\\}";
    return ch;
  }

  /* ------------------------------------------------------------------ *
   * small geometry helpers
   * ------------------------------------------------------------------ */

  const median = (arr) => {
    if (!arr.length) return 0;
    const s = arr.slice().sort((a, b) => a - b);
    const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // glyph box derived from baseline + size; PDFs only give us the baseline
  function boxOf(t) {
    return {
      x0: t.x,
      x1: t.x + Math.max(1, t.w || t.size * 0.5),
      y0: t.y - t.size * 0.72,
      y1: t.y + t.size * 0.24,
    };
  }

  function overlapX(a, b) {
    const x0 = Math.max(a.x0, b.x0);
    const x1 = Math.min(a.x1, b.x1);
    if (x1 <= x0) return 0;
    return (x1 - x0) / Math.max(1, Math.min(a.x1 - a.x0, b.x1 - b.x0));
  }

  /* ------------------------------------------------------------------ *
   * fraction / radical splitting
   * ------------------------------------------------------------------ */

  function unitOf(tok) {
    return { kind: "char", ch: tok.ch, x: tok.x, y: tok.y, size: tok.size, w: tok.w, font: tok.font, box: boxOf(tok) };
  }

  function nodeBox(nodes) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      const b = n && (n.box || n);
      if (!b || typeof b.x0 !== "number") continue;
      x0 = Math.min(x0, b.x0);
      y0 = Math.min(y0, b.y0);
      x1 = Math.max(x1, b.x1);
      y1 = Math.max(y1, b.y1);
    }
    if (!isFinite(x0)) return { x0: 0, y0: 0, x1: 0, y1: 0 };
    return { x0, y0, x1, y1 };
  }

  function rulesInside(rules, box, pad) {
    pad = pad == null ? 1.5 : pad;
    return rules.filter(
      (r) => r.y0 >= box.y0 - pad && r.y1 <= box.y1 + pad && r.x1 > box.x0 - pad && r.x0 < box.x1 + pad
    );
  }

  /** tokens strictly above / below a bar, horizontally within its span */
  function sideTokens(tokens, bar, side) {
    return tokens.filter((t) => {
      const b = boxOf(t);
      const ox = overlapX(b, bar);
      if (side === "num") return ox > 0.02 && t.y <= bar.y0 + 0.6;
      return ox > 0.02 && t.y >= bar.y1 - 0.6;
    });
  }

  /**
   * Turn one flat token list into units, replacing every horizontal rule that
   * has content on both sides by a fraction node.
   */
  function splitFractions(tokens, rules, baseSize, depth) {
    depth = depth || 0;
    if (!tokens.length) return [];
    if (depth > 6) return tokens.map(unitOf);

    const bars = (rules || []).filter((r) => {
      const num = sideTokens(tokens, r, "num");
      const den = sideTokens(tokens, r, "den");
      return num.length > 0 && den.length > 0;
    });

    if (!bars.length) return stackFallback(tokens, baseSize);

    // left-most bar first, so nested and side-by-side fractions unravel in order
    bars.sort((a, b) => a.x0 - b.x0 || a.y0 - b.y0);
    const bar = bars[0];

    const numToks = sideTokens(tokens, bar, "num");
    const denToks = sideTokens(tokens, bar, "den");
    const used = new Set(numToks.concat(denToks));

    const numBox = nodeBox(numToks.map(boxOf));
    const denBox = nodeBox(denToks.map(boxOf));

    const numRules = rulesInside(rules, numBox).filter((r) => r !== bar);
    const denRules = rulesInside(rules, denBox).filter((r) => r !== bar);

    const num = buildLevel(splitFractions(numToks, numRules, baseSize, depth + 1));
    const den = buildLevel(splitFractions(denToks, denRules, baseSize, depth + 1));

    const frac = {
      kind: "frac",
      num,
      den,
      size: baseSize,
      x: bar.x0,
      y: bar.y1,
      box: {
        x0: Math.min(numBox.x0, denBox.x0, bar.x0),
        x1: Math.max(numBox.x1, denBox.x1, bar.x1),
        y0: Math.min(numBox.y0, denBox.y0),
        y1: Math.max(numBox.y1, denBox.y1),
      },
    };

    const left = tokens.filter((t) => !used.has(t) && boxOf(t).x1 <= bar.x0 + 0.5);
    const right = tokens.filter((t) => !used.has(t) && boxOf(t).x0 >= bar.x1 - 0.5);
    const stray = tokens.filter(
      (t) => !used.has(t) && boxOf(t).x1 > bar.x0 + 0.5 && boxOf(t).x0 < bar.x1 - 0.5
    );

    return [
      ...splitFractions(left, rules, baseSize, depth + 1),
      ...stray.map(unitOf),
      frac,
      ...splitFractions(right, rules, baseSize, depth + 1),
    ];
  }

  /**
   * Some PDFs draw no fraction bar (it is part of a font glyph or missing).
   * Two stacked, horizontally aligned blocks of full-size glyphs are then
   * read as numerator / denominator.
   */
  function stackFallback(tokens, baseSize) {
    // A stacked pair with no rule is more often two separate lines of maths
    // than a barless fraction, so it stays flat; the caller keeps the text.
    void baseSize;
    return tokens.map(unitOf);
  }

  /** overbar directly above a √ glyph becomes a radical */
  function attachRadicals(units, rules) {
    if (!rules || !rules.length) return units;
    const out = [];
    for (let i = 0; i < units.length; i++) {
      const u = units[i];
      const isRoot = u.kind === "char" && (u.ch === "√" || u.ch === "∛" || u.ch === "∜");
      if (!isRoot) { out.push(u); continue; }
      const bar = rules.find(
        (r) =>
          r.x0 >= u.box.x0 - 1 &&
          r.x0 <= u.box.x1 + u.size * 0.8 &&
          r.y1 <= u.box.y0 + u.size * 0.3 &&
          r.y0 >= u.box.y0 - u.size * 0.7
      );
      if (!bar) { out.push(u); continue; }
      const body = [];
      let j = i + 1;
      while (j < units.length && units[j].box.x0 < bar.x1 + u.size * 0.45) {
        body.push(units[j]);
        j++;
      }
      if (!body.length) { out.push(u); continue; }
      const deg = u.ch === "√" ? [] : [{ kind: "char", ch: u.ch === "∛" ? "3" : "4", size: u.size, box: u.box }];
      out.push({
        kind: "rad",
        deg,
        body: buildLevel(body),
        size: u.size,
        box: nodeBox([{ box: u.box }].concat(body)),
      });
      i = j - 1;
    }
    return out;
  }

  /* ------------------------------------------------------------------ *
   * structure: scripts, operators, delimiters
   * ------------------------------------------------------------------ */

  function isScript(u, baseSize, baseY) {
    if (!u || !u.size || !isFinite(u.y)) return null;
    const dy = u.y - baseY;
    const small = u.size <= baseSize * 0.87;
    const raised = -dy;
    if (small && raised > baseSize * 0.10) return "sup";
    if (small && raised < -baseSize * 0.10) return "sub";
    if (!small && raised > baseSize * 0.34) return "sup";
    if (!small && raised < -baseSize * 0.34) return "sub";
    return null;
  }

  function levelBase(units) {
    const sizes = units.map((u) => u.size).filter((s) => s > 0);
    if (!sizes.length) return { size: 10, y: 0 };
    const maxSize = Math.max(...sizes);
    const main = units.filter((u) => u.size >= maxSize * 0.9);
    const size = median(main.map((u) => u.size)) || maxSize;
    const y = median(main.map((u) => u.y));
    return { size, y };
  }

  function matchOpener(atoms, closeIdx) {
    const close = atoms[closeIdx].text;
    const open = { ")": "(", "]": "[", "}": "{", "⟩": "⟨", "⌉": "⌈", "⌋": "⌊", "»": "«" }[close];
    if (!open) return -1;
    let depth = 0;
    for (let k = closeIdx - 1; k >= 0; k--) {
      const t = atoms[k].kind === "run" ? atoms[k].text : "";
      if (t === close) depth++;
      else if (t === open) {
        if (depth === 0) return k;
        depth--;
      }
    }
    return -1;
  }

  function assemble(unitsIn, depth) {
    depth = depth || 0;
    if (!unitsIn.length) return [];
    const units = unitsIn.slice().sort((a, b) => a.box.x0 - b.box.x0 || b.box.y1 - a.box.y1);
    const { size: baseSize, y: baseY } = levelBase(units);

    const main = [];
    const scripts = [];
    for (const u of units) (isScript(u, baseSize, baseY) ? scripts : main).push(u);

    // atoms: neighbouring letters of one size are a single base ("sin", "lim")
    const atoms = [];
    for (const u of main) {
      const prev = atoms[atoms.length - 1];
      if (
        u.kind === "char" &&
        prev &&
        prev.kind === "run" &&
        isLatin(u.ch) &&
        /[A-Za-z]$/.test(prev.text) &&
        Math.abs(prev.size - u.size) < u.size * 0.06 &&
        u.box.x0 - prev.box.x1 < u.size * 0.3 &&
        !FUNCTION_NAMES.some((f) => f.toLowerCase() === prev.text.toLowerCase())
      ) {
        prev.text += u.ch;
        prev.box = {
          x0: prev.box.x0,
          x1: Math.max(prev.box.x1, u.box.x1),
          y0: Math.min(prev.box.y0, u.box.y0),
          y1: Math.max(prev.box.y1, u.box.y1),
        };
        continue;
      }
      const atom =
        u.kind === "char"
          ? { kind: "run", text: u.ch, upright: !isLatin(u.ch), size: u.size, box: u.box }
          : u;
      if (atom.kind === "run" && atom.text.length > 1 && FUNCTION_NAMES.some((f) => f.toLowerCase() === atom.text.toLowerCase())) {
        atom.upright = true;
      }
      atoms.push(atom);
    }

    for (const a of atoms) {
      if (a.kind === "run" && a.text.length > 1 && FUNCTION_NAMES.some((f) => f.toLowerCase() === a.text.toLowerCase())) {
        a.upright = true;
      }
    }

    if (!atoms.length) {
      return scripts.map((v) => ({ kind: "run", text: v.ch, upright: !isLatin(v.ch), size: v.size, box: v.box, origin: "loose" }));
    }
    if (!scripts.length) return atoms;

    // every script glyph joins the atom it is drawn over / next to
    const groups = new Map(); // atom index -> {to, sup, sub}
    const loose = [];
    for (const v of scripts) {
      const centre = (v.box.x0 + v.box.x1) / 2;
      let ti = -1;
      for (let k = 0; k < atoms.length; k++) if (atoms[k].box.x0 <= centre + v.size * 0.25) ti = k;
      if (ti < 0) { loose.push(v); continue; }
      let from = ti;
      // a script attached to a closing bracket belongs to the whole group
      if (atoms[ti].kind === "run" && CLOSERS.has(atoms[ti].text)) {
        const o = matchOpener(atoms, ti);
        if (o >= 0) from = o;
      }
      const kind = isScript(v, baseSize, baseY) || "sup";
      let g = groups.get(from);
      if (!g) { g = { to: ti, sup: [], sub: [] }; groups.set(from, g); }
      g.to = Math.max(g.to, ti);
      g[kind].push(v);
    }

    const out = [];
    for (let k = 0; k < atoms.length; k++) {
      const g = groups.get(k);
      if (!g) { out.push(atoms[k]); continue; }
      const base = atoms.slice(k, g.to + 1);
      // keep a bracketed base as a proper delimiter object
      let baseNodes = base;
      if (base.length > 2 && base[0].kind === "run" && OPENERS.has(base[0].text) && base[base.length - 1].kind === "run" && CLOSERS.has(base[base.length - 1].text)) {
        baseNodes = [{
          kind: "delim",
          beg: base[0].text,
          end: base[base.length - 1].text,
          body: base.slice(1, -1),
          size: base[0].size,
          box: nodeBox(base),
        }];
      }
      const scriptNode = {
        kind: "script",
        limitUnder: false,
        base: baseNodes,
        sup: g.sup.length ? assemble(g.sup, depth + 1) : null,
        sub: g.sub.length ? assemble(g.sub, depth + 1) : null,
        size: base[0].size,
        box: nodeBox(base.concat(g.sup.map((v) => ({ box: v.box }))).concat(g.sub.map((v) => ({ box: v.box })))),
      };
      if (!scriptNode.sup && scriptNode.sub && base.length === 1) {
        const name = nodeText(base[0]).trim().toLowerCase();
        const sb = nodeBox(g.sub.map(boxOf));
        const nb = base[0].box;
        const c = (sb.x0 + sb.x1) / 2;
        if (LIMIT_FUNCTIONS.has(name) && c > nb.x0 - (nb.x1 - nb.x0) * 0.4 && c < nb.x1 + (nb.x1 - nb.x0) * 0.4) {
          scriptNode.limitUnder = true;
        }
      }
      out.push(scriptNode);
      k = g.to;
    }

    for (const v of loose) {
      out.push({ kind: "run", text: v.ch, upright: !isLatin(v.ch), size: v.size, box: v.box, origin: "loose" });
    }
    out.sort((a, b) => a.box.x0 - b.box.x0 || b.box.y1 - a.box.y1);
    return out;
  }

  function nodeText(n) {
    if (!n) return "";
    if (n.kind === "run") return n.text;
    if (n.kind === "script") return nodeText(n.base[0]);
    return "";
  }

  function isTall(n) {
    return n && (n.kind === "frac" || n.kind === "rad" || n.kind === "nary" || n.kind === "delim");
  }

  /** merge adjacent runs, insert a space where the PDF left a wide gap */
  function mergeRuns(list, baseSize) {
    const out = [];
    for (const n of list) {
      const prev = out[out.length - 1];
      if (
        n.kind === "run" &&
        prev &&
        prev.kind === "run" &&
        !!prev.upright === !!n.upright &&
        Math.abs(prev.size - n.size) < 0.6 &&
        !prev.origin &&
        !n.origin
      ) {
        const gap = n.box.x0 - prev.box.x1;
        if (gap > n.size * 1.1) prev.text += " ";
        prev.text += n.text;
        prev.box = {
          x0: prev.box.x0,
          x1: Math.max(prev.box.x1, n.box.x1),
          y0: Math.min(prev.box.y0, n.box.y0),
          y1: Math.max(prev.box.y1, n.box.y1),
        };
        continue;
      }
      out.push(n);
    }
    return out;
  }

  /** function names (sin, log, lim ...) read as one upright run */
  function markFunctions(list) {
    for (const n of list) {
      if (n.kind !== "run") continue;
      const text = n.text.trim();
      if (!text || !/^[A-Za-z]{2,8}$/.test(text)) continue;
      const lower = text.toLowerCase();
      if (FUNCTION_NAMES.some((f) => f.toLowerCase() === lower)) n.upright = true;
    }
    return list;
  }

  /** big operators swallow the operand that follows them */
  function attachNaryBodies(list) {
    for (let i = 0; i < list.length; i++) {
      const n = list[i];
      if (n.kind !== "script") continue;
      const baseCh = nodeText(n.base[0]);
      if (!NARY_CHARS[baseCh]) continue;
      // functions like log are not operators even though they take a subscript
      const body = [];
      let j = i + 1;
      while (j < list.length && body.length < 8) {
        const v = list[j];
        if (v.kind === "run" && NOT_OPERAND_END.has(v.text)) break;
        if (v.kind === "run" && CLOSERS.has(v.text)) break;
        body.push(v);
        j++;
        if (body.length && v.kind !== "script" && v.kind === "run" && v.text.length > 1) break;
      }
      if (!body.length) continue;
      list.splice(i, 1 + body.length, {
        kind: "nary",
        chr: baseCh,
        sup: n.sup,
        sub: n.sub,
        body,
        size: n.size,
        box: nodeBox([n.base[0]].concat(body.map((b) => b))),
      });
    }
    return list;
  }

  /** bracketed groups that contain tall material become m:d */
  function wrapDelims(list, baseSize) {
    const out = [];
    for (let i = 0; i < list.length; i++) {
      const n = list[i];
      const ch = n.kind === "run" ? n.text : "";
      if (ch && ch.length === 1 && OPENERS.has(ch)) {
        const closeCh = { "(": ")", "[": "]", "{": "}", "⟨": "⟩", "⌈": "⌉", "⌊": "⌋", "«": "»" }[ch];
        let depth = 0;
        let end = -1;
        for (let j = i + 1; j < list.length; j++) {
          const t = list[j].kind === "run" ? list[j].text : "";
          if (t === ch) depth++;
          if (t === closeCh) {
            if (depth === 0) { end = j; break; }
            depth--;
          }
        }
        if (end > i + 1) {
          const inner = list.slice(i + 1, end);
          const tall = inner.some(isTall) || inner.length > 2 || n.size > baseSize * 1.12;
          if (tall) {
            out.push({
              kind: "delim",
              beg: ch,
              end: closeCh,
              body: inner,
              size: n.size,
              box: nodeBox(inner.concat([n])),
            });
            i = end;
            continue;
          }
        }
      }
      out.push(n);
    }
    // recurse into children so nested brackets are handled too
    for (const n of out) {
      if (n.kind === "delim") n.body = wrapDelims(n.body, baseSize);
      if (n.kind === "frac") {
        n.num = wrapDelims(n.num, baseSize);
        n.den = wrapDelims(n.den, baseSize);
      }
      if (n.kind === "script") {
        n.base = wrapDelims(n.base, baseSize);
        if (n.sup) n.sup = wrapDelims(n.sup, baseSize);
        if (n.sub) n.sub = wrapDelims(n.sub, baseSize);
      }
      if (n.kind === "nary") {
        n.body = wrapDelims(n.body, baseSize);
      }
    }
    return out;
  }

  /** one nesting level: units in, finished nodes out */
  function buildLevel(units) {
    const baseSize = levelBase(units).size || 10;
    let list = assemble(units, 0);
    list = mergeRuns(list, baseSize);
    list = markFunctions(list);
    list = attachNaryBodies(list);
    list = wrapDelims(list, baseSize);
    return list;
  }

  /* ------------------------------------------------------------------ *
   * emitters
   * ------------------------------------------------------------------ */

  const escXml = (s) =>
    String(s)
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g, "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

  function runXml(text, upright) {
    const style = upright ? "p" : "i";
    return `<m:r><m:rPr><m:sty m:val="${style}"/></m:rPr><m:t xml:space="preserve">${escXml(text)}</m:t></m:r>`;
  }

  const EMPTY_RUN = `<m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t xml:space="preserve"> </m:t></m:r>`;

  function emitList(list, depth) {
    depth = depth || 0;
    if (depth > 24) return "";
    let xml = "";
    for (const n of list) {
      switch (n.kind) {
        case "run":
          if (n.text) xml += runXml(n.text, n.upright);
          break;
        case "script": {
          const base = emitList(n.base, depth + 1) || EMPTY_RUN;
          if (n.limitUnder && n.sub) {
            xml += `<m:limLow><m:e>${base}</m:e><m:lim>${emitList(n.sub, depth + 1) || EMPTY_RUN}</m:lim></m:limLow>`;
            break;
          }
          if (n.sup && n.sub) {
            xml += `<m:sSubSup><m:e>${base}</m:e><m:sub>${emitList(n.sub, depth + 1) || EMPTY_RUN}</m:sub><m:sup>${
              emitList(n.sup, depth + 1) || EMPTY_RUN
            }</m:sup></m:sSubSup>`;
          } else if (n.sup) {
            xml += `<m:sSup><m:e>${base}</m:e><m:sup>${emitList(n.sup, depth + 1) || EMPTY_RUN}</m:sup></m:sSup>`;
          } else {
            xml += `<m:sSub><m:e>${base}</m:e><m:sub>${emitList(n.sub, depth + 1) || EMPTY_RUN}</m:sub></m:sSub>`;
          }
          break;
        }
        case "frac": {
          const type = n.synth ? "noBar" : "bar";
          xml += `<m:f><m:fPr><m:type m:val="${type}"/></m:fPr><m:num>${
            emitList(n.num, depth + 1) || EMPTY_RUN
          }</m:num><m:den>${emitList(n.den, depth + 1) || EMPTY_RUN}</m:den></m:f>`;
          break;
        }
        case "rad": {
          const deg = n.deg && n.deg.length ? emitList(n.deg, depth + 1) : "";
          xml += `<m:rad><m:radPr>${deg ? "" : '<m:degHide m:val="1"/>'}</m:radPr><m:deg>${deg}</m:deg><m:e>${
            emitList(n.body, depth + 1) || EMPTY_RUN
          }</m:e></m:rad>`;
          break;
        }
        case "nary": {
          const meta = NARY_CHARS[n.chr] || { limits: false };
          const pr = [
            `<m:chr m:val="${escXml(n.chr)}"/>`,
            `<m:limLoc m:val="${meta.limits ? "undOvr" : "subSup"}"/>`,
            n.sub ? "" : '<m:subHide m:val="1"/>',
            n.sup ? "" : '<m:supHide m:val="1"/>',
          ].join("");
          xml += `<m:nary><m:naryPr>${pr}</m:naryPr><m:sub>${
            n.sub ? emitList(n.sub, depth + 1) : ""
          }</m:sub><m:sup>${n.sup ? emitList(n.sup, depth + 1) : ""}</m:sup><m:e>${
            emitList(n.body, depth + 1) || EMPTY_RUN
          }</m:e></m:nary>`;
          break;
        }
        case "delim": {
          xml += `<m:d><m:dPr><m:begChr m:val="${escXml(n.beg)}"/><m:endChr m:val="${escXml(n.end)}"/></m:dPr><m:e>${
            emitList(n.body, depth + 1) || EMPTY_RUN
          }</m:e></m:d>`;
          break;
        }
        default:
          break;
      }
    }
    return xml;
  }

  function latexList(list, depth) {
    depth = depth || 0;
    if (depth > 20) return "";
    let s = "";
    for (const n of list) {
      switch (n.kind) {
        case "run":
          s += n.text.replace(/%/g, "\\%").replace(/#/g, "\\#").replace(/&/g, "\\&");
          break;
        case "script": {
          const base = latexList(n.base, depth + 1);
          if (n.limitUnder && n.sub) {
            s += base + "_{" + latexList(n.sub, depth + 1) + "}";
            break;
          }
          const sub = n.sub ? "_{" + latexList(n.sub, depth + 1) + "}" : "";
          const sup = n.sup ? "^{" + latexList(n.sup, depth + 1) + "}" : "";
          s += base + sub + sup;
          break;
        }
        case "frac":
          s += "\\frac{" + latexList(n.num, depth + 1) + "}{" + latexList(n.den, depth + 1) + "}";
          break;
        case "rad":
          s +=
            (n.deg && n.deg.length ? "\\sqrt[" + latexList(n.deg, depth + 1) + "]" : "\\sqrt") +
            "{" + latexList(n.body, depth + 1) + "}";
          break;
        case "nary":
          s +=
            (NARY_CHARS[n.chr] ? NARY_CHARS[n.chr].latex : n.chr) +
            (n.sub ? "_{" + latexList(n.sub, depth + 1) + "}" : "") +
            (n.sup ? "^{" + latexList(n.sup, depth + 1) + "}" : "") +
            " " + latexList(n.body, depth + 1);
          break;
        case "delim":
          s += n.beg + latexList(n.body, depth + 1) + n.end;
          break;
        default:
          break;
      }
    }
    return s;
  }

  /* ------------------------------------------------------------------ *
   * public entry
   * ------------------------------------------------------------------ */

  /**
   * @param {Array} tokens  [{ch, x, y, size, w, font}]
   * @param {Object} opts   {rules:[{x0,y0,x1,y1}], display:bool}
   * @returns {{omml:string, latex:string, confidence:number, chars:number, structures:number}|null}
   */
  function parse(tokens, opts) {
    opts = opts || {};
    if (!tokens || tokens.length < 1) return null;

    const clean = [];
    let dropped = 0;
    for (const t of tokens) {
      if (!t || typeof t.x !== "number" || typeof t.y !== "number") continue;
      const ch = normalizeChar(String(t.ch || ""));
      if (!ch) { dropped++; continue; }
      if (!(t.size > 1)) continue;
      clean.push({ ch, x: t.x, y: t.y, size: t.size, w: t.w > 0 ? t.w : t.size * 0.5, font: t.font || "" });
    }
    if (!clean.length) return null;
    if (clean.length > 900) return null; // a page of prose, not a formula

    const baseSize = levelBase(clean.map(unitOf)).size || 10;
    let units = splitFractions(clean, opts.rules || [], baseSize, 0);
    units = attachRadicals(units, opts.rules || []);

    const list = buildLevel(units);

    const ommlBody = emitList(list, 0);
    if (!ommlBody) return null;
    const latex = latexList(list, 0);

    // ---- confidence: how much of the geometry we actually explained ----
    let conf = 1;
    const total = clean.length;
    const mathChars = clean.filter((t) => isMathChar(t.ch) || isMathFont(t.font)).length;
    if (dropped / (dropped + total) > 0.2) conf -= 0.15;
    if (total < 2) conf -= 0.2;
    const loose = list.filter((n) => n.kind === "run" && n.origin === "loose").length;
    if (loose) conf -= Math.min(0.3, loose * 0.05);
    // a formula made only of prose words is suspicious
    const letters = clean.filter((t) => isLatin(t.ch)).length;
    if (letters / total > 0.75 && mathChars / total < 0.15) conf -= 0.35;
    const structures = list.reduce((n, x) => n + (x.kind === "run" ? 0 : 1), 0);

    return {
      omml: ommlBody,
      latex: latex.trim(),
      confidence: clamp(conf, 0, 1),
      chars: total,
      structures,
      mathChars,
      baseSize,
      tree: list,
    };
  }

  PS.MathOMML = {
    parse,
    isMathFont,
    isMathChar,
    isRelation,
    isLatin,
    isDigit,
    latexOf,
    normalizeChar,
    NARY_CHARS,
    RELATIONS,
    ADDITIONAL: ADDITIVE,
    OPENERS,
    CLOSERS,
    FUNCTION_NAMES,
    LIMIT_FUNCTIONS,
  };
})();
