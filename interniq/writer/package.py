"""Builds one complete application package: resume, cover letter, transcript."""

import json
import shutil
from datetime import date

from interniq import config, db
from interniq.writer import checks, letter, resume
from interniq.writer.compose import Composer, Package

OUTPUT_DIR = config.ROOT / "data" / "output"
TRANSCRIPT = config.ROOT / "data" / "base" / "transcript.pdf"
MAX_FIXES = 2


def load_posting(posting_id: int) -> dict:
    row = db.connect().execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if not row:
        raise SystemExit(f"Posting {posting_id} is not in the database. Run `python -m interniq crawl` first.")
    posting = dict(row)
    posting["materials"] = json.loads(posting["materials"] or "[]")
    return posting


def render(package: Package, out_dir, data: dict) -> list[str]:
    """Write both PDFs. Returns layout problems (profile too long, letter too long)."""
    problems = []
    try:
        profile = resume.profile_line(package.term_months, package.resume_focus, package.term_start, package.term_end)
        resume.build(out_dir / package.resume_file_name, profile, data)
    except ValueError as e:
        problems.append(f"{e}. Use a shorter resume_focus.")
    today = date.today()
    try:
        letter.build(
            out_dir / package.cover_letter_file_name,
            name=data["name"], legal_name=data["legal_name"], contact=data["letter_contact"],
            recipient_bold=package.recipient_team,
            recipient_lines=[package.company_name, package.company_location],
            date=f"{today:%B} {today.day}, {today:%Y}",
            re_line=package.re_line,
            paragraphs=package.paragraphs,
            footer=f"{data['name']}  |  Cover Letter  |  {package.footer_label}",
            title=f"{data['name']} Cover Letter, {package.footer_label}",
        )
    except letter.LetterTooLong as e:
        lines = int(e.overflow // 15.5) + 1
        problems.append(f"{e}. Cut about {lines} lines (roughly {lines * 15} words).")
    return problems


def write(posting_id: int) -> dict:
    """Draft, check, fix, and render. Returns a summary for the terminal."""
    posting = load_posting(posting_id)
    out_dir = OUTPUT_DIR / str(posting_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = resume.load()

    composer = Composer(posting)
    package = composer.draft()
    problems = checks.check(package, posting) + render(package, out_dir, data)
    fixes = 0
    while problems and fixes < MAX_FIXES:
        fixes += 1
        print(f"  fixing {len(problems)} problem(s): " + " | ".join(p[:80] for p in problems))
        package = composer.fix(problems)
        problems = checks.check(package, posting) + render(package, out_dir, data)

    if any("transcript" in m.lower() for m in posting["materials"]):
        shutil.copyfile(TRANSCRIPT, out_dir / "Ali_Alanizi_Transcript.pdf")

    # Remove files from earlier drafts that used different names.
    keep = {package.resume_file_name, package.cover_letter_file_name, "Ali_Alanizi_Transcript.pdf", "package.json"}
    for old in out_dir.glob("*.pdf"):
        if old.name not in keep:
            old.unlink()

    summary = {
        "posting_id": posting_id,
        "org": posting["org"],
        "title": posting["title"],
        "folder": str(out_dir),
        "fix_rounds": fixes,
        "unresolved_problems": problems,
        "package": package.model_dump(),
    }
    (out_dir / "package.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
