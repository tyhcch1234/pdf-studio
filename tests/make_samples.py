"""Generate the sample PDFs used by the tests and the demo page."""
import os

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples")
os.makedirs(OUT, exist_ok=True)

MATH_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"

BODY = (
    "This report summarises the measurement results of the prototype.\n"
    "The device was tested at three ambient temperatures and the error\n"
    "was recorded for every run."
)


def digital_pdf(path: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "Measurement Report", fontsize=20, fontname="hebo")
    page.insert_textbox(
        pymupdf.Rect(60, 100, 535, 200), BODY, fontsize=11, fontname="tiro", lineheight=1.4
    )

    # vector "figure"
    page.draw_rect(pymupdf.Rect(80, 240, 300, 380), color=(0.2, 0.4, 0.8), width=1.5)
    page.draw_line(
        pymupdf.Point(90, 360), pymupdf.Point(290, 260), color=(0.8, 0.2, 0.2), width=2
    )
    page.draw_circle(pymupdf.Point(190, 310), 26, color=(0.1, 0.6, 0.3), width=1.2)
    page.insert_text((100, 400), "Figure 1. Test rig layout", fontsize=9, fontname="tiro")

    page.insert_textbox(
        pymupdf.Rect(60, 440, 535, 560),
        "Discussion. The measured values stay within the tolerance band.\n"
        "A second batch will be produced next month.",
        fontsize=11,
        fontname="tiro",
        lineheight=1.4,
    )
    doc.save(path)
    doc.close()


def scanned_pdf(path: str) -> None:
    """Image-only PDF (forces the OCR path)."""
    src = pymupdf.open()
    page = src.new_page(width=595, height=842)
    page.insert_text((60, 80), "Scanned Invoice", fontsize=18, fontname="hebo")
    page.insert_textbox(
        pymupdf.Rect(60, 110, 535, 260),
        "Invoice No: 2026-0417\nCustomer: Acme Trading Co.\nAmount: 1,250.00 USD\n"
        "Due date: 2026-11-30",
        fontsize=12,
        fontname="tiro",
        lineheight=1.6,
    )
    page.insert_text((60, 320), "Total = 1250", fontsize=13, fontname="tiro")
    pix = page.get_pixmap(dpi=200)
    img = pix.tobytes("png")
    src.close()

    doc = pymupdf.open()
    out = doc.new_page(width=595, height=842)
    out.insert_image(out.rect, stream=img)
    doc.save(path)
    doc.close()


def math_sample(path: str) -> None:
    """Body text plus two display equations in a fully mapped font."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "Mass-energy equivalence", fontsize=16, fontname="hebo")
    page.insert_textbox(
        pymupdf.Rect(60, 110, 535, 170),
        "The total energy of a body at rest is proportional to its mass, and "
        "Pythagoras' theorem holds for right triangles.",
        fontsize=11,
        fontname="tiro",
        lineheight=1.4,
    )
    page.insert_font(fontname="math", fontfile=MATH_FONT)
    font = pymupdf.Font(fontfile=MATH_FONT)
    eq1 = "E = m c\u00b2"
    w1 = font.text_length(eq1, fontsize=18)
    page.insert_text(((595 - w1) / 2, 230), eq1, fontsize=18, fontname="math")

    page.insert_textbox(
        pymupdf.Rect(60, 260, 535, 300),
        "Both relations are used throughout the derivation that follows.",
        fontsize=11,
        fontname="tiro",
        lineheight=1.4,
    )
    eq2 = "a\u00b2 + b\u00b2 = c\u00b2"
    w2 = font.text_length(eq2, fontsize=18)
    page.insert_text(((595 - w2) / 2, 360), eq2, fontsize=18, fontname="math")
    doc.save(path)
    doc.close()


def chinese_pdf(path: str) -> None:
    """Two column Chinese page with a display equation and a ruled table."""
    cjk = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="cjk", fontfile=cjk)
    page.insert_text((60, 70), "研究報告：材料疲勞測試", fontsize=18, fontname="cjk")
    left = (
        "本實驗針對三種不同熱處理條件下的試片進行疲勞測試。\n"
        "測試採用恆幅載重控制，頻率為 10 Hz，\n"
        "應力比 R = 0.1。結果顯示試片壽命\n"
        "隨回火溫度上升而增加。"
    )
    right = (
        "第二階段的測試加入表面處理變數，\n"
        "並以掃描式電子顯微鏡觀察裂紋起始位置。\n"
        "所有試片均在夾持端附近斷裂，\n"
        "顯示夾具對中性軸仍有偏移。"
    )
    page.insert_textbox(
        pymupdf.Rect(60, 110, 285, 300), left, fontsize=10.5, fontname="cjk", lineheight=1.6
    )
    page.insert_textbox(
        pymupdf.Rect(310, 110, 535, 300), right, fontsize=10.5, fontname="cjk", lineheight=1.6
    )
    eq = "x = (-b \u00b1 \u221a(b\u00b2 - 4ac)) / 2a"
    page.insert_font(fontname="math", fontfile=MATH_FONT)
    w = pymupdf.Font(fontfile=MATH_FONT).text_length(eq, fontsize=14)
    page.insert_text(((595 - w) / 2, 360), eq, fontsize=14, fontname="math")

    page.insert_textbox(
        pymupdf.Rect(60, 400, 535, 520),
        "結論：回火溫度每提高 50 度，疲勞壽命約提升 12%。後續將以有限元素模型\n"
        "驗證實驗結果，並加入殘餘應力場的影響。",
        fontsize=10.5,
        fontname="cjk",
        lineheight=1.6,
    )
    for i in range(4):
        page.draw_line(
            pymupdf.Point(60, 560 + i * 22), pymupdf.Point(300, 560 + i * 22), width=0.6
        )
    page.draw_line(pymupdf.Point(60, 560), pymupdf.Point(60, 626), width=0.6)
    page.draw_line(pymupdf.Point(300, 560), pymupdf.Point(300, 626), width=0.6)
    for i, t in enumerate(["試片", "壽命 (cycles)", "-", "-"]):
        page.insert_text((66, 575 + i * 22 - 5), t, fontsize=9, fontname="cjk")
    doc.save(path)
    doc.close()


def a3_pdf(path: str) -> None:
    """A3 landscape sheet: two A4 panels side by side."""
    doc = pymupdf.open()
    page = doc.new_page(width=1191, height=842)
    page.draw_rect(page.rect, color=(0.85, 0.85, 0.85), width=1)
    page.insert_text((80, 120), "LEFT PANEL", fontsize=28, fontname="hebo")
    page.insert_text((80, 170), "A4 content on the left half.", fontsize=14, fontname="tiro")
    page.insert_text((640, 120), "RIGHT PANEL", fontsize=28, fontname="hebo")
    page.insert_text((640, 170), "A4 content on the right half.", fontsize=14, fontname="tiro")
    page.draw_line(
        pymupdf.Point(595.5, 0), pymupdf.Point(595.5, 842), color=(1, 0, 0), width=0.8
    )
    doc.save(path)
    doc.close()


def a3_portrait(path: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=1191)
    page.insert_text((80, 120), "TOP PANEL", fontsize=28, fontname="hebo")
    page.insert_text((80, 600), "BOTTOM PANEL", fontsize=28, fontname="hebo")
    doc.save(path)
    doc.close()


if __name__ == "__main__":
    digital_pdf(os.path.join(OUT, "digital.pdf"))
    scanned_pdf(os.path.join(OUT, "scanned.pdf"))
    math_sample(os.path.join(OUT, "math_sample.pdf"))
    chinese_pdf(os.path.join(OUT, "chinese_2col.pdf"))
    a3_pdf(os.path.join(OUT, "a3_landscape.pdf"))
    a3_portrait(os.path.join(OUT, "a3_portrait.pdf"))
    for name in sorted(os.listdir(OUT)):
        print(name, os.path.getsize(os.path.join(OUT, name)))
