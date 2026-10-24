"""Registers the resume and cover letter fonts with reportlab.

Carlito (resume) and Roboto (cover letter) live in data/base/fonts/. The resume
name uses Arial Bold from Windows, which has the same shapes as the original.
"""

from pathlib import Path

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from interniq import config

FONT_DIR = config.ROOT / "data" / "base" / "fonts"
ARIAL_BOLD = Path(r"C:\Windows\Fonts\arialbd.ttf")
STYLES = [("", "Regular", 0, 0), ("-Bold", "Bold", 1, 0), ("-Italic", "Italic", 0, 1), ("-BoldItalic", "BoldItalic", 1, 1)]

_registered = False


def register() -> None:
    """Register every font once. Families support <b> and <i> in Paragraphs."""
    global _registered
    if _registered:
        return
    for family in ("Carlito", "Roboto"):
        for suffix, file_style, bold, italic in STYLES:
            path = FONT_DIR / f"{family}-{file_style}.ttf"
            if not path.exists():
                raise SystemExit(f"Missing font {path}. See the setup steps in README.md")
            pdfmetrics.registerFont(TTFont(family + suffix, str(path)))
            addMapping(family, bold, italic, family + suffix)
    pdfmetrics.registerFont(TTFont("Arial-Bold", str(ARIAL_BOLD)))
    _registered = True
