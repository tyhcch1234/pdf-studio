/* Regression test for the scanned-page OCR path (web/js/ocr-engine.js).

   Self-contained: every page it needs is drawn in the browser, so no sample
   PDFs are required. Start a static server first:

     python3 -m http.server 8123 --directory web &
     node tests/ocr_test.js http://127.0.0.1:8123

   Set CHROME_PATH if Chrome is not at /usr/bin/google-chrome. */
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

const BASE = process.argv[2] || "http://127.0.0.1:8123";
const CHROME = process.env.CHROME_PATH || "/usr/bin/google-chrome";

let failed = 0;
function check(cond, msg, detail) {
  console.log((cond ? "  PASS  " : "  FAIL  ") + msg + (cond || detail === undefined ? "" : `  [${detail}]`));
  if (!cond) failed++;
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    protocolTimeout: 600000,
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(BASE + "/index.html", { waitUntil: "networkidle2" });
  await page.waitForFunction("window.PS && window.PS.OcrEngine");

  /* ---- 1. text clean-up rules ---- */
  const clean = await page.evaluate(() => ({
    spaced: PS.OcrEngine.cleanText("這 一 行 是 普通 中 文 文 字 , 不 應 該 被 當 成 數 式 。"),
    latin: PS.OcrEngine.cleanText("The  school runs  24 classes with 1, 236 students."),
    punct: PS.OcrEngine.cleanText("本校共有二十四班 , 學生人數一千二百三十六人 ;"),
    trail: PS.OcrEngine.cleanText("Mathematics Sample /\n\n"),
    fw: PS.OcrEngine.cleanText("２０２５ 年 度 報 告"),
  }));
  check(clean.spaced === "這一行是普通中文文字，不應該被當成數式。", "CJK spacing + punctuation repaired", clean.spaced);
  check(clean.latin === "The school runs 24 classes with 1,236 students.", "Latin spacing and numbers", clean.latin);
  check(clean.punct === "本校共有二十四班，學生人數一千二百三十六人；", "ASCII punctuation -> full width", clean.punct);
  check(clean.trail === "Mathematics Sample /", "trailing newlines trimmed", JSON.stringify(clean.trail));
  check(clean.fw === "2025年度報告", "full-width digits -> ASCII", clean.fw);

  /* ---- 2. deskew ---- */
  const skew = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 1000;
    c.height = 700;
    const g = c.getContext("2d", { willReadFrequently: true });
    g.fillStyle = "#fff";
    g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = "#000";
    g.font = "18px sans-serif";
    g.translate(500, 350);
    g.rotate((2 * Math.PI) / 180);
    for (let i = -8; i <= 8; i++) g.fillText("The quick brown fox jumps over the lazy dog 0123456789", -460, i * 34);
    const prep = PS.OcrEngine.prepare(c, { deskew: true });
    const again = PS.OcrEngine.prepare(prep.canvas, { deskew: true });
    return { detected: prep.info.skew, residual: again.info.skew };
  });
  check(Math.abs(skew.detected + 2) < 0.6, `skew measured (${skew.detected.toFixed(2)}°)`, skew.detected);
  check(skew.residual === 0, "page straight after correction", skew.residual);

  /* ---- 3. shading flattening keeps a photographed page readable ---- */
  const shadow = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 900;
    c.height = 600;
    const g = c.getContext("2d", { willReadFrequently: true });
    // white paper with a strong left-to-right shadow
    const grad = g.createLinearGradient(0, 0, 900, 0);
    grad.addColorStop(0, "#b9b9b9");
    grad.addColorStop(0.5, "#ececec");
    grad.addColorStop(1, "#fdfdfd");
    g.fillStyle = grad;
    g.fillRect(0, 0, 900, 600);
    g.fillStyle = "#000";
    g.font = "22px sans-serif";
    g.fillText("Invoice 2026 total 1,250.00 HKD", 60, 120);
    g.fillText("本年度全校共有二十四班", 60, 200);
    const before = PS.OcrEngine.prepare(c, { deskew: false, shading: false });
    const after = PS.OcrEngine.prepare(c, { deskew: false });
    const sample = (cv) => {
      const cx = cv.getContext("2d", { willReadFrequently: true });
      return [
        cx.getImageData(20, 40, 1, 1).data[0],
        cx.getImageData(880, 40, 1, 1).data[0],
      ];
    };
    return { before: sample(before.canvas), after: sample(after.canvas) };
  });
  const spread = (a) => Math.abs(a[0] - a[1]);
  check(spread(shadow.after) <= spread(shadow.before), `shadow flattened (${shadow.before} -> ${shadow.after})`, JSON.stringify(shadow));
  check(shadow.after[0] >= 235 && shadow.after[1] >= 235, "paper becomes white across the page", JSON.stringify(shadow.after));

  /* ---- 4. recognition on a drawn page (both language packs) ---- */
  const ocr = await page.evaluate(async () => {
    const draw = () => {
      const c = document.createElement("canvas");
      c.width = 1100;
      c.height = 420;
      const g = c.getContext("2d", { willReadFrequently: true });
      g.fillStyle = "#fff";
      g.fillRect(0, 0, c.width, c.height);
      g.fillStyle = "#000";
      g.font = "26px sans-serif";
      g.fillText("School report 2026 - attendance 96.4%", 50, 70);
      g.fillText("本年度全校共有二十四班，學生人數一千二百三十六人。", 50, 140);
      g.fillText("圖書館全年借閱量較去年上升百分之十八。", 50, 210);
      g.fillText("The reading programme reached every form.", 50, 280);
      return c;
    };
    const render = async () => ({ canvas: draw(), scale: 200 / 72 });
    const out = {};
    for (const lang of ["auto", "simp"]) {
      const r = await PS.OcrEngine.recognizePage(null, { render, dpi: 200, lang, noRetry: true });
      out[lang] = {
        conf: r.confidence,
        lines: r.lines.map((l) => l.runs[0].text),
      };
    }
    await PS.OcrEngine.release();
    return out;
  });
  const cnLine = ocr.auto.lines.find((l) => /本年度|圖書館/.test(l));
  check(!!cnLine, "Chinese line recognised", JSON.stringify(ocr.auto.lines).slice(0, 160));
  check(cnLine && !/[一-龥] [一-龥]/.test(cnLine), "no spaces between Chinese characters", cnLine);
  check(ocr.auto.conf >= 75, `confidence reported (${ocr.auto.conf}%)`, ocr.auto.conf);
  check(ocr.simp.lines.length > 0, "simplified pack runs", ocr.simp.lines.length);
  check(
    ocr.auto.lines.some((l) => /attendance|School report/i.test(l)),
    "English line recognised",
    JSON.stringify(ocr.auto.lines).slice(0, 160)
  );

  /* ---- 5. end-to-end conversion still produces a docx ---- */
  const sample = path.join(__dirname, "samples", "scan_text.pdf");
  if (fs.existsSync(sample)) {
    const stats = await page.evaluate(async (b64) => {
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const f = new File([arr], "scan_text.pdf", { type: "application/pdf" });
      const { blob, stats } = await PS.BrowserEngine.convertPdf(
        f,
        { ocr: "always", images: false, formula: "off", dpi: 200 },
        () => {}
      );
      const buf = new Uint8Array(await blob.arrayBuffer());
      let s = "";
      for (let i = 0; i < buf.length; i += 0x8000)
        s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      window.__lastDocx = btoa(s);
      return { ...stats, bytes: buf.length };
    }, fs.readFileSync(sample).toString("base64"));
    check(stats.ocr_pages === 1, "scanned PDF went through OCR", JSON.stringify(stats));
    check(stats.bytes > 2000, "docx produced", stats.bytes);
    check(stats.ocr_confidence >= 70, `page confidence ${stats.ocr_confidence}%`, stats.ocr_confidence);
  } else {
    console.log("  SKIP  end-to-end check (run tests/make_text_sample.py for the fixture)");
  }

  check(errors.length === 0, "no javascript errors", errors.slice(0, 2).join(" | "));

  await browser.close();
  console.log(failed ? `\n${failed} CHECK(S) FAILED` : "\nALL PASSED");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
