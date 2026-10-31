"""Postings Ali copies from Elevate himself (Ctrl+A, Ctrl+C on a posting page).

While the agent's browser is blocked from Elevate, the agent never opens Elevate: Ali
pastes a posting into a text file in data/inbox/ (or into chat), and this turns the
text into the same record the crawler builds, using the crawler's own field mapping.
Imported files move to data/inbox/done/.
"""

import re

from interniq import config, db
from interniq.elevate import crawler

INBOX = config.ROOT / "data" / "inbox"
LABEL = re.compile(r"^([A-Z][^:\t\n]{1,90}):\t?(.*)$")
HEADER = re.compile(r"^(\d{6}) - (.+)$")
BLOCK_LABELS = {"Job Description", "Job Qualifications", "Application Material Required",
                "Additional Application Information", "Application Information"}


def parse(raw: str) -> dict:
    """Pasted posting text -> crawler-style posting dict."""
    lines = [l.rstrip() for l in raw.replace("\r", "").split("\n")]
    head = next((HEADER.match(l.strip()) for l in lines if HEADER.match(l.strip())), None)
    if not head:
        raise ValueError("No '123456 - Job Title' line: paste the whole posting page")
    posting_id, title = int(head.group(1)), head.group(2).strip()
    after = lines[lines.index(head.group(0)) + 1:] if head.group(0) in lines else lines
    org_line = next((l.strip() for l in after if l.strip()), "")
    org, _, division = org_line.partition(" - ")

    fields, key = {}, None
    for line in lines:
        m = LABEL.match(line)
        # Elevate's labels end with a tab ("Placement Details:" inside a description doesn't);
        # labels whose value starts on the next line can lose that tab when copied.
        if m and ("\t" in line or (not m.group(2).strip() and m.group(1).strip() in BLOCK_LABELS)):
            key = m.group(1).strip()
            if key not in fields:
                fields[key] = m.group(2).strip()
            else:
                key = None  # a repeated label (Salary, Division) keeps its first value
            continue
        if key and line.strip():
            fields[key] = (fields[key] + "\n" + line.strip()).strip()

    start = raw.find(f"{posting_id} - ")
    row = {"id": posting_id, "elevate_status": "", "term": "", "title": title, "org": org.strip(),
           "division": division.strip(), "location": fields.get("Job Location"), "deadline": None}
    return crawler.to_posting(row, {"fields": fields, "raw_text": raw[start if start >= 0 else 0:].strip()})


def import_text(raw: str) -> str:
    conn = db.connect()
    posting = parse(raw)
    if posting["id"] in db.known_ids(conn):
        return f"{posting['id']} is already known ({posting['org']}, {posting['title']})"
    db.save_posting(conn, posting)
    return f"{posting['id']} imported: {posting['org']}, {posting['title']}"


def import_inbox() -> list[str]:
    """Every .txt in data/inbox/ (one posting each), then move it to data/inbox/done/."""
    done = INBOX / "done"
    done.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(INBOX.glob("*.txt")):
        try:
            results.append(import_text(path.read_text(encoding="utf-8")))
            path.rename(done / path.name)
        except ValueError as e:
            results.append(f"{path.name}: {e}")
    return results
