"""Builds Ali's resume PDF from config/resume.yaml.

The layout copies his master resume (data/base/Ali_Alanizi_Resume_master.pdf):
Letter size, Carlito 11pt, centered header, a rule under each section title,
dates right aligned at a tab stop. All spacing values were measured from that PDF.
Only the profile line changes per job.
"""

from dataclasses import dataclass, field

import yaml
from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from interniq import config
from interniq.writer import fonts

RESUME_YAML = config.ROOT / "config" / "resume.yaml"
PROFILE_TEMPLATE = (
    "Dedicated Software Engineering student at University of Calgary seeking a {months} month "
    "internship in {focus}. Available {start} through {end}."
)

W, H = LETTER
LEFT, RIGHT = 36.0, 576.0
CENTER = W / 2
DATE_RIGHT = 487.5          # right edge of the date column
BULLET_X, BULLET_TEXT_X = 43.2, 54.1
SIZE = 11
GREY = Color(0.35, 0.35, 0.35)

# Baseline to baseline distances (points).
WRAP = 13.5                 # wrapped line inside one item
ITEM = 15.0                 # next bullet, or subtitle to first bullet
SKILL = 15.75               # next skill line
TITLE_TO_SUBTITLE = 13.5
ENTRY_GAP = 15.5            # last bullet to next entry title
HEADING_GAP = 17.0          # last line of a section to the next section title
AFTER_HEADING = 16.6        # section title to its first line
RULE_DROP = 3.5             # rule sits this far below the section title
FIRST_HEADING_Y = 694.5     # page 1, under the header
PAGE_TOP_Y = 746.3          # page 2+, first baseline
BOTTOM_Y = 50.0             # no baseline below this

REGULAR, BOLD, ITALIC = "Carlito", "Carlito-Bold", "Carlito-Italic"


@dataclass
class Row:
    """Things drawn on one baseline, placed `gap` points below the previous row."""
    gap: float
    items: list = field(default_factory=list)  # (kind, x, text, font, size)


def wrap(text: str, font: str, width: float, first_width: float | None = None) -> list[str]:
    """Greedy word wrap. `first_width` lets the first line be shorter (after a label)."""
    fonts.register()
    lines, line = [], ""
    limit = first_width if first_width is not None else width
    for word in text.split():
        trial = f"{line} {word}" if line else word
        if stringWidth(trial, font, SIZE) <= limit or not line:
            line = trial
        else:
            lines.append(line)
            line, limit = word, width
    if line:
        lines.append(line)
    return lines


def profile_line(months: int, focus: str, start: str, end: str) -> str:
    text = PROFILE_TEMPLATE.format(months=months, focus=focus, start=start, end=end)
    if len(wrap(text, REGULAR, RIGHT - LEFT)) > 2:
        raise ValueError(f"Profile line is longer than 2 lines: {text}")
    return text


def text_rows(text: str, x: float, font: str, first_gap: float, bullet: bool = False) -> list[Row]:
    rows = []
    for i, line in enumerate(wrap(text, font, RIGHT - x)):
        row = Row(first_gap if i == 0 else WRAP, [("text", x, line, font, SIZE)])
        if bullet and i == 0:
            row.items.append(("text", BULLET_X, "•", "Times-Roman", 10))
        rows.append(row)
    return rows


def entry_rows(entry: dict, first_gap: float) -> list[Row]:
    title = Row(first_gap, [("text", LEFT, entry["title"], BOLD, SIZE)])
    if entry.get("date"):
        title.items.append(("right", DATE_RIGHT, entry["date"], REGULAR, SIZE))
    rows = [title]
    if entry.get("subtitle"):
        font = ITALIC if entry.get("subtitle_italic", True) else REGULAR
        rows += text_rows(entry["subtitle"], LEFT, font, TITLE_TO_SUBTITLE)
    for bullet in entry.get("bullets", []):
        rows += text_rows(bullet, BULLET_TEXT_X, REGULAR, ITEM, bullet=True)
    return rows


def skill_rows(label: str, items: str, first_gap: float) -> list[Row]:
    label_text = f"{label}: "
    label_w = stringWidth(label_text, BOLD, SIZE)
    lines = wrap(items, REGULAR, RIGHT - LEFT, first_width=RIGHT - LEFT - label_w)
    rows = [Row(first_gap, [("text", LEFT, label_text, BOLD, SIZE), ("text", LEFT + label_w, lines[0], REGULAR, SIZE)])]
    rows += [Row(WRAP, [("text", LEFT, line, REGULAR, SIZE)]) for line in lines[1:]]
    return rows


def blocks(data: dict, profile: str) -> list[list[Row]]:
    """Group rows into blocks that must stay on one page.

    A section title is glued to its first entry so it never ends a page alone.
    """
    out = []
    for s_index, section in enumerate(data["sections"]):
        heading = Row(0 if s_index == 0 else HEADING_GAP, [("heading", LEFT, section["title"], BOLD, SIZE)])
        if section.get("profile"):
            parts = [text_rows(profile, LEFT, REGULAR, AFTER_HEADING)]
        elif section.get("skills"):
            parts = [skill_rows(label, items, AFTER_HEADING if i == 0 else SKILL)
                     for i, (label, items) in enumerate(section["skills"])]
        else:
            parts = [entry_rows(e, AFTER_HEADING if i == 0 else ENTRY_GAP)
                     for i, e in enumerate(section["entries"])]
        parts[0] = [heading] + parts[0]
        out += parts
    return out


def draw_header(c: canvas.Canvas, data: dict) -> None:
    c.setFillColor(black)
    c.setFont("Arial-Bold", 14)
    c.drawCentredString(CENTER, 757.0, data["name"])
    c.setFillColor(GREY)
    c.setFont(REGULAR, 9)
    c.drawCentredString(CENTER, 745.0, f"Legal name: {data['legal_name']}")
    c.setFillColor(black)
    c.setFont(REGULAR, SIZE)
    for i, line in enumerate(data["contact_lines"]):
        c.drawCentredString(CENTER, 727.5 - 15.7 * i, line)


def draw_row(c: canvas.Canvas, row: Row, y: float) -> None:
    for kind, x, text, font, size in row.items:
        c.setFont(font, size)
        if kind == "right":
            c.drawRightString(x, y, text)
        else:
            c.drawString(x, y, text)
        if kind == "heading":
            c.setLineWidth(1)
            c.line(LEFT, y - RULE_DROP, RIGHT, y - RULE_DROP)


def load() -> dict:
    with open(RESUME_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build(out_path, profile: str, data: dict | None = None) -> int:
    """Write the resume PDF. Returns the page count."""
    fonts.register()
    data = data or load()
    c = canvas.Canvas(str(out_path), pagesize=LETTER)
    c.setTitle(f"{data['name']} Resume")
    c.setAuthor(data["name"])
    draw_header(c, data)

    pages = 1
    y = FIRST_HEADING_Y
    first = True
    for block in blocks(data, profile):
        height = sum(r.gap for r in block[1:])
        if not first and y - block[0].gap - height < BOTTOM_Y:
            c.showPage()
            pages += 1
            y = PAGE_TOP_Y + block[0].gap  # first row of the block lands on PAGE_TOP_Y
        for row in block:
            if not first:
                y -= row.gap
            first = False
            draw_row(c, row, y)
    c.save()
    return pages
