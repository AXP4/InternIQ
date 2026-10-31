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


def letter_wanted(posting: dict) -> bool:
    """Only write a letter when the posting lists one or mentions one."""
    text = f"{posting.get('description') or ''} {posting.get('additional_info') or ''}".lower()
    return any("cover" in m.lower() for m in posting["materials"]) or "cover letter" in text


def render(package: Package, out_dir, data: dict, with_letter: bool) -> list[str]:
    """Write the PDFs. Returns layout problems (profile too long, letter too long)."""
    problems = []
    try:
        profile = resume.profile_line(package.resume_focus)
        resume.build(out_dir / package.resume_file_name, profile, data)
    except ValueError as e:
        problems.append(f"{e}. Use a shorter resume_focus.")
    if not with_letter:
        return problems
    today = date.today()
    header = dict(
        recipient_bold=package.recipient_team,
        recipient_lines=[package.company_name, package.company_location],
        date=f"{today:%B} {today.day}, {today:%Y}",
        re_line=package.re_line,
        footer=f"{data['name']}  |  Cover Letter  |  {package.footer_label}",
    )
    problems += letter.margin_problems(**header)
    try:
        letter.build(
            out_dir / package.cover_letter_file_name,
            name=data["name"], legal_name=data["legal_name"], contact=data["letter_contact"],
            paragraphs=package.paragraphs,
            title=f"{data['name']} Cover Letter, {package.footer_label}",
            **header,
        )
    except letter.LetterTooLong as e:
        lines = int(e.overflow // 15.5) + 1
        problems.append(f"{e}. Cut about {lines} lines (roughly {lines * 15} words).")
    return problems


def load_summary(posting_id: int) -> dict | None:
    """The package.json written for a posting, or None if there's no package yet."""
    path = OUTPUT_DIR / str(posting_id) / "package.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write(posting_id: int, feedback: str | None = None) -> dict:
    """Draft, check, fix, and render. Returns a summary for the terminal.

    With `feedback` (Ali's Fix request from Discord), the previous draft is revised.
    """
    posting = load_posting(posting_id)
    out_dir = OUTPUT_DIR / str(posting_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = resume.load()

    with_letter = letter_wanted(posting)
    earlier = load_summary(posting_id) if feedback else None
    composer = Composer(posting, with_letter, earlier["package"] if earlier else None, feedback)

    def problems_in(package: Package) -> list[str]:
        if with_letter and package.resume_file_name == package.cover_letter_file_name:
            # A posting that wants both in ONE named file: render them separately (one would
            # overwrite the other); the email step merges them under the required name.
            package.resume_file_name = "Ali_Alanizi_Resume.pdf"
            package.cover_letter_file_name = "Ali_Alanizi_Cover_Letter.pdf"
        found = checks.check(package, posting) if with_letter else []
        found += checks.check_focus(package)
        return found + render(package, out_dir, data, with_letter)

    package = composer.draft()
    problems = problems_in(package)
    fixes = 0
    while problems and fixes < MAX_FIXES:
        fixes += 1
        print(f"  fixing {len(problems)} problem(s): " + " | ".join(p[:80] for p in problems))
        package = composer.fix(problems)
        problems = problems_in(package)

    if any("transcript" in m.lower() for m in posting["materials"]):
        shutil.copyfile(TRANSCRIPT, out_dir / "Ali_Alanizi_Transcript.pdf")

    # Remove files from earlier drafts that used different names.
    keep = {package.resume_file_name, "Ali_Alanizi_Transcript.pdf"}
    if with_letter:
        keep.add(package.cover_letter_file_name)
    for old in out_dir.glob("*.pdf"):
        if old.name not in keep:
            old.unlink()

    summary = {
        "posting_id": posting_id,
        "org": posting["org"],
        "title": posting["title"],
        "folder": str(out_dir),
        "cover_letter": with_letter,
        "files": sorted(p.name for p in out_dir.glob("*.pdf")),
        "feedback": (earlier.get("feedback", []) if earlier else []) + ([feedback] if feedback else []),
        "fix_rounds": fixes,
        "unresolved_problems": problems,
        "package": package.model_dump(),
    }
    (out_dir / "package.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
