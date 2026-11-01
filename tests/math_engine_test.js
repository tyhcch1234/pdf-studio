/* Unit test for the in-browser math recogniser (no browser needed).

   Builds glyph tokens the way a PDF text layer would expose them — each glyph
   with its own x, baseline y and font size — then checks the LaTeX and OMML
   that `web/js/math-omml.js` derives from that geometry.

   run:  node tests/math_engine_test.js
*/
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "web/js/math-omml.js"), "utf8"), sandbox);
const M = sandbox.window.PS.MathOMML;

/* ---------- tiny "PDF typesetter": builds positioned glyphs ---------- */

const ADV = 0.5; // advance width factor of the em square

function glyphs(text, x, y, size, opts) {
  opts = opts || {};
  const out = [];
  let cx = x;
  for (const ch of text) {
    const w = size * (ch === " " ? 0.28 : ADV);
    if (ch !== " ") out.push({ ch, x: cx, y, size, w, font: opts.font || "CMMI10" });
    cx += w + (opts.tracking || 0);
  }
  return { tokens: out, end: cx };
}

/** a formula row: items are strings, or {sup}, {sub}, {frac:[num,den]}, {nary} */
function layout(items, x, y, size) {
  let cx = x;
  const tokens = [];
  const rules = [];
  const push = (part) => {
    const g = glyphs(part.t, cx, part.y, part.s, part.font ? { font: part.font } : {});
    tokens.push(...g.tokens);
    cx = g.end + (part.pad || 0);
  };
  for (const it of items) {
    if (typeof it === "string") {
      push({ t: it, y, s: size });
      cx += size * 0.12;
      continue;
    }
    if (it.nary) {
      const g = glyphs(it.nary, cx, y, size * 1.15, { font: "CMEX10" });
      tokens.push(...g.tokens);
      const center = cx + (g.end - cx) / 2 - size * 0.15;
      cx = g.end + size * 0.1;
      if (it.sub) {
        const gs = glyphs(it.sub, center, y + size * 0.75, size * 0.6);
        tokens.push(...gs.tokens);
      }
      if (it.sup) {
        const gu = glyphs(it.sup, center, y - size * 0.95, size * 0.6);
        tokens.push(...gu.tokens);
      }
      cx += size * 0.5;
      continue;
    }
    if (it.sup || it.sub) {
      if (it.base) push({ t: it.base, y, s: size });
      const sSize = size * 0.65;
      if (it.sup) {
        const g = glyphs(it.sup, cx, y - size * 0.45, sSize);
        tokens.push(...g.tokens);
        cx = g.end;
      }
      if (it.sub) {
        const g = glyphs(it.sub, cx, y + size * 0.28, sSize);
        tokens.push(...g.tokens);
        cx = g.end;
      }
      cx += size * 0.1;
      continue;
    }
    if (it.frac) {
      const [num, den] = it.frac;
      const nw = num.length * size * ADV * 0.9;
      const dw = den.length * size * ADV * 0.9;
      const w = Math.max(nw, dw);
      const g1 = glyphs(num, cx + (w - nw) / 2, y - size * 0.62, size * 0.9);
      const g2 = glyphs(den, cx + (w - dw) / 2, y + size * 1.05, size * 0.9);
      tokens.push(...g1.tokens, ...g2.tokens);
      rules.push({ x0: cx - size * 0.06, x1: cx + w + size * 0.06, y0: y - size * 0.06, y1: y - size * 0.02 });
      cx += w + size * 0.3;
      continue;
    }
  }
  return { tokens, rules };
}

/* ---------- checks ---------- */

let pass = 0;
let fail = 0;

function check(name, items, expect, opts) {
  const size = 11;
  const { tokens, rules } = layout(items, 60, 300, size);
  const res = M.parse(tokens, { rules: rules.concat((opts && opts.rules) || []) });
  if (!res) {
    console.log(`FAIL ${name}: parser returned null`);
    fail++;
    return;
  }
  const latex = res.latex.replace(/\s+/g, " ").trim();
  const ok = expect.every((re) => re.test(latex)) && !(opts && opts.not && opts.not.some((re) => re.test(latex)));
  const wellFormed = xmlOk(res.omml);
  if (ok && wellFormed && res.confidence > 0.5) {
    console.log(`ok   ${name}\n     ${latex}`);
    pass++;
  } else {
    console.log(`FAIL ${name}\n     got: ${latex}\n     conf=${res.confidence.toFixed(2)} xmlOk=${wellFormed}`);
    fail++;
  }
}

function xmlOk(xml) {
  // minimal balance check: every opening tag has a matching close in order
  const stack = [];
  const re = /<\/?([a-zA-Z0-9:]+)([^>]*?)(\/?)>/g;
  let m;
  while ((m = re.exec(xml))) {
    const [full, tag, attrs, selfClose] = m;
    if (full.startsWith("</")) {
      if (stack.pop() !== tag) return false;
    } else if (!selfClose) {
      stack.push(tag);
    }
  }
  return stack.length === 0;
}

check("superscript E=mc^2", ["E", "=", { base: "m", sup: "2" }, { base: "c", sup: "2" }], [/E=.*m.*2.*c.*2/]);

check("pythagoras a^2+b^2=c^2", [
  { base: "a", sup: "2" }, "+", { base: "b", sup: "2" }, "=", { base: "c", sup: "2" },
], [/a\^\{2\}/, /c\^\{2\}/]);

check("quadratic with fraction", [
  "x=", { frac: ["-b+√(b²-4ac)", "2a"] },
], [/x=/, /\\frac\{/, /2a/]);

check("sum with limits", [
  { nary: "∑", sub: "i=1", sup: "n" }, { base: "i", sup: "2" }, "=", { frac: ["n(n+1)(2n+1)", "6"] },
], [/\\sum_\{i=1\}\^\{n\}/, /\\frac\{/]);

check("integral", [
  { nary: "∫", sub: "0", sup: "1" }, { base: "x", sup: "2" }, "dx", "=", { frac: ["1", "3"] },
], [/\\int_\{0\}\^\{1\}/, /\\frac\{1\}\{3\}/]);

check("sin/cos stay upright", ["f(x)=", "sin", "x+", "cos", "2x"], [/sin/, /cos/]);

check("subscript only", [{ base: "a", sub: "n" }, "=", { base: "a", sub: "n-1" }, "+d"], [/a_\{n\}=a_\{n-1\}\+d/]);

check("no false fraction from prose", ["The", "area", "is", "large"], [/area/], { not: [/\\frac/] });

/* limit with the condition drawn underneath (lim / max / min) */
{
  const tokens = [];
  const put = (text, x, y, sz) => {
    let cx = x;
    for (const ch of text) {
      tokens.push({ ch, x: cx, y, size: sz, w: sz * 0.5, font: "CMMI10" });
      cx += sz * 0.5;
    }
    return cx;
  };
  put("lim", 60, 300, 11);
  const w = put("x→0", 62, 310.5, 6.4);
  put("f(x)", w + 6, 300, 11);
  const res = M.parse(tokens, { rules: [] });
  if (res && /lim_\{x→0\}/.test(res.latex) && /<m:limLow>/.test(res.omml)) {
    console.log(`ok   limit underneath\n     ${res.latex}`);
    pass++;
  } else {
    console.log(`FAIL limit underneath\n     ${res && res.latex}`);
    fail++;
  }
}

/* ---------- summary ---------- */

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
