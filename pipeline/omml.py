"""LaTeX -> OMML (Office Math Markup Language) conversion.

Produces real Word equation objects (`m:oMath`) so the result stays fully
editable inside Word / LibreOffice instead of being a static picture.
"""
from __future__ import annotations

import re

NS_M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_WS = re.compile(r"\s+")


def clean_latex(latex: str) -> str:
    """Normalise the noisy output that formula OCR models usually produce."""
    if not latex:
        return ""
    s = latex.strip()
    # strip math delimiters
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if s.startswith(a) and s.endswith(b) and len(s) > len(a) + len(b):
            s = s[len(a) : len(s) - len(b)]
    s = s.strip()
    s = s.replace("\\displaystyle", "").replace("\\limits", "")
    # OCR models love to emit stray spacing macros
    s = re.sub(r"\\(?:left|right)\s*\.", "", s)
    s = re.sub(r"\\(?:quad|qquad|,|;|:|!)", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    # unsupported environments -> plain rows
    s = re.sub(r"\\begin\{(?:array|align\*?|gathered|split)\}(\{[^}]*\})?", "", s)
    s = re.sub(r"\\end\{(?:array|align\*?|gathered|split)\}", "", s)
    s = s.replace("\\\\", " \\\\ ")
    return s.strip()


def _wrap_namespaces(omml: str) -> str:
    """`mathml2omml` emits unqualified prefixes; declare them locally."""
    decls = f' xmlns:m="{NS_M}" xmlns:w="{NS_W}"'
    m = re.match(r"<(m:oMathPara|m:oMath)\b", omml)
    if not m:
        return ""
    head = m.group(1)
    return omml.replace(f"<{head}", f"<{head}{decls}", 1)


def latex_to_omml(latex: str, display: bool = True) -> str | None:
    """Convert a LaTeX snippet into an OMML string, or None if impossible."""
    src = clean_latex(latex)
    if not src or len(src) > 4000:
        return None
    try:
        from latex2mathml.converter import convert as latex_to_mathml
        import mathml2omml

        mathml = latex_to_mathml(src)
        omml = mathml2omml.convert(mathml)
    except Exception:
        return None
    if not omml:
        return None
    omml = _wrap_namespaces(omml)
    if not omml:
        return None
    if display and omml.startswith("<m:oMath"):
        # wrap in a display paragraph container
        inner = omml
        inner = re.sub(r'^<m:oMath[^>]*>', "", inner)
        inner = re.sub(r"</m:oMath>$", "", inner)
        omml = f'<m:oMathPara xmlns:m="{NS_M}" xmlns:w="{NS_W}"><m:oMath>{inner}</m:oMath></m:oMathPara>'
    return omml


def omml_from_latex_fallback(latex: str) -> str | None:
    """Very small converter used when the MathML route fails.

    Handles a single flat expression (no fractions/roots) so simple inline
    formulas still become native equations.
    """
    src = clean_latex(latex)
    if not src or "\\" in src:
        return None
    runs = []
    for chunk in re.findall(r"[A-Za-z0-9\u0370-\u03ff+\-*/=<>()\[\]|.,']+|\s+", src):
        if not chunk:
            continue
        style = "p" if not chunk.strip().isalpha() else "i"
        esc = (
            chunk.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        runs.append(
            f'<m:r><m:rPr><m:sty m:val="{style}"/></m:rPr><m:t xml:space="preserve">{esc}</m:t></m:r>'
        )
    if not runs:
        return None
    return (
        f'<m:oMath xmlns:m="{NS_M}" xmlns:w="{NS_W}">' + "".join(runs) + "</m:oMath>"
    )
