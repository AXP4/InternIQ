"""Renders a cover letter PDF in Ali's design (ported from reference/letter_lib.py).

A4, Roboto, navy name header with the legal name beside it, a rule under the
Re: line, and a footer. Paragraph text may use <b> and <i>; everything else is
escaped. Raises LetterTooLong if the body runs past the footer.
"""

import html
import re

from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from interniq.writer import fonts

NAVY = Color(0.0588, 0.0902, 0.1647)
BODY = Color(0.2, 0.2549, 0.3333)
SLATE = Color(0.3922, 0.4549, 0.5451)
RULE1 = Color(0.7961, 0.8353, 0.8824)
RULE2 = Color(0.8863, 0.9098, 0.9412)

W, H = A4
L, R = 56.96, 538.88
TW = R - L
BODY_LIMIT = 765  # body must end above this (points from the top)


class LetterTooLong(Exception):
    def __init__(self, ends_at: float):
        super().__init__(f"Letter body ends at {ends_at:.0f}pt, limit is {BODY_LIMIT}pt")
        self.overflow = ends_at - BODY_LIMIT


def markup(text: str) -> str:
    """Escape text for a reportlab Paragraph, keeping only <b> and <i> tags."""
    text = html.escape(text, quote=False)
    return re.sub(r"&lt;(/?)([bi])&gt;", r"<\1\2>", text)


def build(out_path, *, name: str, legal_name: str, contact: str, recipient_bold: str,
          recipient_lines: list[str], date: str, re_line: str, paragraphs: list[str],
          footer: str, title: str) -> float:
    """Write the letter. Returns where the body ends (points from the top)."""
    fonts.register()
    c = canvas.Canvas(str(out_path), pagesize=A4)
    c.setTitle(title)
    c.setAuthor(name)

    def top(y, size):
        return H - y - size * 0.93

    upper = name.upper()
    c.setFillColor(NAVY)
    c.setFont("Roboto-Bold", 20)
    c.drawString(L, top(52, 20), upper)
    c.setFillColor(SLATE)
    c.setFont("Roboto", 10)
    c.drawString(L + stringWidth(upper, "Roboto-Bold", 20) + 8, top(52, 20), f"(legal name: {legal_name})")
    c.setFont("Roboto", 9.5)
    c.setFillColor(BODY)
    c.drawString(L, top(80, 9.5), contact)
    c.setFillColor(NAVY)
    c.rect(L, H - 100.43, TW, 1.5, stroke=0, fill=1)

    c.setFont("Roboto-Bold", 10)
    c.drawString(L, top(115, 10), recipient_bold)
    c.setFont("Roboto", 10)
    c.setFillColor(BODY)
    c.drawRightString(R, top(115, 10), date)
    yy = 130
    for line in recipient_lines:
        c.drawString(L, top(yy, 10), line)
        yy += 15
    ry = yy + 13
    c.setFillColor(NAVY)
    c.setFont("Roboto-Bold", 10.5)
    c.drawString(L, top(ry, 10.5), re_line)
    c.setFillColor(RULE1)
    c.rect(L, H - (ry + 17.4), TW, 0.75, stroke=0, fill=1)

    style = ParagraphStyle("body", fontName="Roboto", fontSize=10, leading=15.5, textColor=BODY)
    y = H - (ry + 35) + 2
    for i, text in enumerate(["Dear Hiring Team,"] + [markup(p) for p in paragraphs]):
        p = Paragraph(text, style)
        _, h = p.wrap(TW, H)
        p.drawOn(c, L, y - h)
        y -= h + (8.5 if i == 0 else 9.5)
    y -= 10
    for text, s in [("Sincerely,", style), (f"<b>{name}</b>", ParagraphStyle("sig", parent=style, textColor=NAVY))]:
        p = Paragraph(text, s)
        _, h = p.wrap(TW, H)
        p.drawOn(c, L, y - h)
        y -= h + 7
    ends_at = H - y + 7
    if ends_at > BODY_LIMIT:
        raise LetterTooLong(ends_at)

    c.setFillColor(RULE2)
    c.rect(L, H - 775.72, TW, 0.75, stroke=0, fill=1)
    c.setFillColor(SLATE)
    c.setFont("Roboto", 8.5)
    c.drawString(L, top(790, 8.5), footer)
    c.save()
    return ends_at
