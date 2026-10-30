/* Minimal .docx writer that runs entirely in the browser (JSZip + raw OOXML).
   Produces paragraphs with styled runs, inline pictures and per-page sections. */
(function () {
  const PS = (window.PS = window.PS || {});

  const EMU_PER_PT = 12700;
  const TWIP_PER_PT = 20;

  const esc = (s) =>
    String(s)
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g, "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const hex = (rgb) =>
    (rgb || [0, 0, 0]).map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, "0")).join("").toUpperCase();

  class DocxBuilder {
    constructor() {
      this.paras = [];
      this.images = []; // { name, bytes, rid }
      this.sections = []; // closed sections (pgSz xml)
      this.currentSize = null;
      this.picId = 0;
      this.rid = 10;
    }

    /** page size in points; starts a new Word section when it changes */
    _section(wPt, hPt) {
      if (
        this.currentSize &&
        Math.abs(this.currentSize[0] - wPt) < 1 &&
        Math.abs(this.currentSize[1] - hPt) < 1
      ) {
        return;
      }
      if (this.currentSize) this._closeSection();
      this.currentSize = [wPt, hPt];
      this.pendingSection = true;
    }

    _pgSzXml(wPt, hPt) {
      const w = Math.round(wPt * TWIP_PER_PT);
      const h = Math.round(hPt * TWIP_PER_PT);
      return (
        `<w:pgSz w:w="${w}" w:h="${h}"/>` +
        `<w:pgMar w:top="${Math.round(14 * TWIP_PER_PT)}" w:right="${Math.round(14 * TWIP_PER_PT)}" ` +
        `w:bottom="${Math.round(14 * TWIP_PER_PT)}" w:left="${Math.round(14 * TWIP_PER_PT)}" ` +
        `w:header="170" w:footer="170" w:gutter="0"/>`
      );
    }

    _closeSection() {
      const [w, h] = this.currentSize;
      this.paras.push(`<w:p><w:pPr><w:sectPr>${this._pgSzXml(w, h)}<w:type w:val="nextPage"/></w:sectPr></w:pPr></w:p>`);
    }

    /** runs: [{text,size,bold,italic,sup,sub,color}] */
    paragraph(runs, opts) {
      opts = opts || {};
      this._section(opts.pageW || this.currentSize[0], opts.pageH || this.currentSize[1]);
      const pPr = [];
      pPr.push(`<w:spacing w:before="${Math.round((opts.before || 0) * TWIP_PER_PT)}" w:after="0" w:line="240" w:lineRule="auto"/>`);
      if (opts.indent) pPr.push(`<w:ind w:left="${Math.round(opts.indent * TWIP_PER_PT)}"/>`);
      if (opts.center) pPr.push(`<w:jc w:val="center"/>`);

      const body = runs
        .filter((r) => r.text && r.text.length)
        .map((r) => {
          const rPr = [`<w:sz w:val="${Math.round((r.size || 10.5) * 2)}"/>`, `<w:szCs w:val="${Math.round((r.size || 10.5) * 2)}"/>`];
          if (r.bold) rPr.push("<w:b/>");
          if (r.italic) rPr.push("<w:i/>");
          if (r.sup) rPr.push('<w:vertAlign w:val="superscript"/>');
          if (r.sub) rPr.push('<w:vertAlign w:val="subscript"/>');
          if (r.color && hex(r.color) !== "000000") rPr.push(`<w:color w:val="${hex(r.color)}"/>`);
          return `<w:r><w:rPr>${rPr.join("")}</w:rPr><w:t xml:space="preserve">${esc(r.text)}</w:t></w:r>`;
        })
        .join("");

      this.paras.push(`<w:p><w:pPr>${pPr.join("")}</w:pPr>${body}</w:p>`);
    }

    /** png: Uint8Array, size in points */
    image(png, wPt, hPt, opts) {
      opts = opts || {};
      this._section(opts.pageW || this.currentSize[0], opts.pageH || this.currentSize[1]);
      const idx = this.images.length + 1;
      const name = `image${idx}.png`;
      const rid = `rId${this.rid++}`;
      this.images.push({ name, bytes: png, rid });
      this.picId += 1;
      const cx = Math.round(wPt * EMU_PER_PT);
      const cy = Math.round(hPt * EMU_PER_PT);
      const pPr = [
        `<w:spacing w:before="${Math.round((opts.before || 0) * TWIP_PER_PT)}" w:after="0" w:line="240" w:lineRule="auto"/>`,
      ];
      if (opts.indent) pPr.push(`<w:ind w:left="${Math.round(opts.indent * TWIP_PER_PT)}"/>`);

      const drawing =
        `<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">` +
        `<wp:extent cx="${cx}" cy="${cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>` +
        `<wp:docPr id="${this.picId}" name="Picture ${this.picId}"/>` +
        `<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>` +
        `<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">` +
        `<pic:pic><pic:nvPicPr><pic:cNvPr id="${this.picId}" name="${name}"/><pic:cNvPicPr/></pic:nvPicPr>` +
        `<pic:blipFill><a:blip r:embed="${rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>` +
        `<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${cx}" cy="${cy}"/></a:xfrm>` +
        `<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>` +
        `</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>`;

      this.paras.push(`<w:p><w:pPr>${pPr.join("")}</w:pPr>${drawing}</w:p>`);
    }

    pageBreak() {
      this.paras.push('<w:p><w:r><w:br w:type="page"/></w:r></w:p>');
    }

    async build(title) {
      const zip = new JSZip();

      zip.file(
        "[Content_Types].xml",
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>`
      );

      zip.file(
        "_rels/.rels",
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>`
      );

      const mediaRels = this.images
        .map(
          (img) =>
            `<Relationship Id="${img.rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/${img.name}"/>`
        )
        .join("");
      zip.file(
        "word/_rels/document.xml.rels",
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
${mediaRels}
</Relationships>`
      );

      zip.file(
        "word/styles.xml",
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft JhengHei" w:cs="Arial"/><w:sz w:val="21"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
</w:styles>`
      );

      const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      zip.file(
        "docProps/core.xml",
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>${esc(title || "PDF Studio")}</dc:title><dc:creator>PDF Studio</dc:creator>
<cp:lastModifiedBy>PDF Studio</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">${now}</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">${now}</dcterms:modified></cp:coreProperties>`
      );

      zip.file(
        "docProps/app.xml",
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>PDF Studio</Application></Properties>`
      );

      const [w, h] = this.currentSize || [595.28, 841.89];
      const body =
        this.paras.join("") +
        `<w:sectPr>${this._pgSzXml(w, h)}</w:sectPr>`;

      zip.file(
        "word/document.xml",
        `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
<w:body>${body}</w:body></w:document>`
      );

      this.images.forEach((img) => zip.file(`word/media/${img.name}`, img.bytes));

      return zip.generateAsync({ type: "blob", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", compression: "DEFLATE" });
    }
  }

  PS.DocxBuilder = DocxBuilder;
})();
