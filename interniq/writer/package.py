"""Builds one complete application package: resume, cover letter, transcript."""

import json
import re
import shutil
from datetime import date

import yaml

from interniq import config, db
from interniq.writer import checks, letter, resume
from interniq.writer.compose import Composer, Package

OUTPUT_DIR = config.ROOT / "data" / "output"
TRANSCRIPT = config.ROOT / "data" / "base" / "transcript.pdf"
PAST_APPLICATIONS = config.ROOT / "config" / "past_applications.yaml"
MAX_FIXES = 2


def load_posting(posting_id: int) -> dict:
    row = db.connect().execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    if not row:
        raise SystemExit(f"Posting {posting_id} is not in the database. Run `python -m interniq crawl` first.")
    posting = dict(row)
    posting["materials"] = json.loads(posting["materials"] or "[]")
    return posting


def same_company(org: str, entry: dict) -> bool:
    names = [entry["company"]] + entry.get("aliases", [])
    return any(re.search(rf"\b{re.escape(name)}\b", org, flags=re.IGNORECASE) for name in names)


def earlier_applications(posting: dict) -> list[dict]:
    """Earlier applications to the same employer: Ali's hand-made list plus packages
    InternIQ already made (until Phase 6 records real submissions)."""
    earlier = []
    if PAST_APPLICATIONS.exists():
        for entry in yaml.safe_load(PAST_APPLICATIONS.read_text(encoding="utf-8")) or []:
            if same_company(posting["org"], entry):
                earlier.append({k: v for k, v in entry.items() if k != "aliases"})
    for path in sorted(OUTPUT_DIR.glob("*/package.json")):
        if not path.parent.name.isdigit() or int(path.parent.name) == posting["id"]:
            continue  # skip saved comparison copies like 152559_v1, and this posting itself
        s = json.loads(path.read_text(encoding="utf-8"))
        if s["org"] == posting["org"]:
            p = s["package"]
            earlier.append({
                "posting_id": s["posting_id"], "title": s["title"],
                "term_months": p["term_months"], "term_start": p["term_start"], "term_end": p["term_end"],
            })
    return earlier


def letter_wanted(posting: dict) -> bool:
    """Only write a letter when the posting lists one or mentions one."""
    text = f"{posting.get('description') or ''} {posting.get('additional_info') or ''}".lower()
    return any("cover" in m.lower() for m in posting["materials"]) or "cover letter" in text


def render(package: Package, out_dir, data: dict, with_letter: bool) -> list[str]:
    """Write the PDFs. Returns layout problems (profile too long, letter too long)."""
    problems = []
    try:
        profile = resume.profile_line(package.term_months, package.resume_focus, package.term_start, package.term_end)
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


def write(posting_id: int) -> dict:
    """Draft, check, fix, and render. Returns a summary for the terminal."""
    posting = load_posting(posting_id)
    out_dir = OUTPUT_DIR / str(posting_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = resume.load()

    with_letter = letter_wanted(posting)
    earlier = earlier_applications(posting)
    composer = Composer(posting, with_letter, earlier)

    def problems_in(package: Package) -> list[str]:
        found = checks.check(package, posting) if with_letter else []
        found += checks.same_company_term(package, earlier)
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
        "fix_rounds": fixes,
        "unresolved_problems": problems,
        "package": package.model_dump(),
    }
    (out_dir / "package.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
