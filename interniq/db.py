"""SQLite database: the shared folder every worker reads from and writes to."""

import json
import sqlite3
from datetime import datetime, timedelta

from interniq import config

DB_PATH = config.ROOT / "data" / "interniq.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id              INTEGER PRIMARY KEY,  -- Elevate posting ID
    title           TEXT,
    org             TEXT,
    division        TEXT,
    location        TEXT,
    term            TEXT,     -- list column, e.g. "May 2027 - August 2028"
    term_start      TEXT,     -- posting field, e.g. "Spring/Summer 2027"
    duration        TEXT,
    deadline        TEXT,     -- ISO, e.g. "2026-11-11T23:59"
    procedure       TEXT,     -- "Employer Website", "Employer Email", ...
    link            TEXT,
    email           TEXT,
    materials       TEXT,     -- JSON list, e.g. ["Resume", "Transcript"]
    additional_info TEXT,
    description     TEXT,
    qualifications  TEXT,
    fields          TEXT,     -- JSON of every label/value on the posting page
    raw_text        TEXT,
    elevate_status  TEXT,     -- App Status column, e.g. "Expressed Intention to Apply"
    site            TEXT,     -- workday, icims, ... (from the link check)
    link_status     TEXT,     -- ok, dead, down (maintenance), error, or none (no link)
    link_note       TEXT,     -- why a link was flagged, e.g. "HTTP 404"
    link_final_url  TEXT,
    link_checked_at TEXT,
    status          TEXT NOT NULL DEFAULT 'new',
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);

-- One row per screened posting. Ali's answers become examples for the next screening.
CREATE TABLE IF NOT EXISTS decisions (
    posting_id      INTEGER PRIMARY KEY REFERENCES postings(id),
    agent_decision  TEXT NOT NULL,  -- apply, skip, ask, closed, applied
    reason          TEXT,
    flags           TEXT,           -- JSON list, e.g. ["borderline"]
    form_term       TEXT,           -- term to pick if a form forces a choice
    model           TEXT,
    ali_decision    TEXT,           -- apply or skip, from the Discord card
    screened_at     TEXT NOT NULL,
    decided_at      TEXT
);

-- One row per package shown to Ali for approval. Nothing is submitted unless state is
-- 'approved', and only Ali's Submit button sets that.
CREATE TABLE IF NOT EXISTS applications (
    posting_id      INTEGER PRIMARY KEY REFERENCES postings(id),
    state           TEXT NOT NULL,  -- pending, fixing, approved, skipped, submitted
    files           TEXT,           -- JSON list of file names in data/output/<id>/
    fix_requests    TEXT,           -- JSON list of Ali's Fix messages
    site            TEXT,
    account_email   TEXT,
    notes           TEXT,
    proposed_at     TEXT,
    approved_at     TEXT,
    submitted_at    TEXT
);

-- Quirks learned per application system, fed back to the form filler on every run.
CREATE TABLE IF NOT EXISTS site_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    site       TEXT NOT NULL,   -- workday, icims, ...
    note       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

POSTING_COLUMNS = [
    "id", "title", "org", "division", "location", "term", "term_start", "duration",
    "deadline", "procedure", "link", "email", "materials", "additional_info",
    "description", "qualifications", "fields", "raw_text", "elevate_status",
]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def known_ids(conn) -> set[int]:
    return {row["id"] for row in conn.execute("SELECT id FROM postings")}


def save_posting(conn, posting: dict) -> None:
    """Insert a newly seen posting. Lists and dicts are stored as JSON."""
    values = {
        col: json.dumps(posting.get(col)) if isinstance(posting.get(col), (list, dict)) else posting.get(col)
        for col in POSTING_COLUMNS
    }
    values["first_seen"] = values["last_seen"] = now()
    cols = ", ".join(values)
    marks = ", ".join(f":{c}" for c in values)
    with conn:
        conn.execute(f"INSERT INTO postings ({cols}) VALUES ({marks})", values)


def touch(conn, posting_id: int, elevate_status: str) -> None:
    """Mark an already known posting as still listed."""
    with conn:
        conn.execute(
            "UPDATE postings SET last_seen = ?, elevate_status = ? WHERE id = ?",
            (now(), elevate_status, posting_id),
        )


def unchecked_links(conn) -> list[sqlite3.Row]:
    """Postings whose link hasn't been checked yet, or was down for maintenance."""
    return conn.execute(
        "SELECT id, title, org, location, procedure, link FROM postings"
        " WHERE link_checked_at IS NULL OR link_status = 'down' ORDER BY deadline"
    ).fetchall()


def save_link_check(conn, posting_id: int, result: dict) -> None:
    """Store a link check. A job found at a new URL replaces the old link."""
    with conn:
        conn.execute(
            "UPDATE postings SET site = ?, link_status = ?, link_note = ?, link_final_url = ?, link_checked_at = ?,"
            " link = COALESCE(?, link) WHERE id = ?",
            (result["site"], result["link_status"], result.get("note"), result.get("final_url"), now(),
             result.get("new_link"), posting_id),
        )


def status_summary(conn) -> dict:
    """Counts for the Discord !status command."""

    def grouped(column: str) -> dict:
        rows = conn.execute(
            f"SELECT {column} AS k, COUNT(*) AS n FROM postings WHERE {column} IS NOT NULL GROUP BY k ORDER BY n DESC"
        )
        return {row["k"]: row["n"] for row in rows}

    last_crawl = conn.execute("SELECT MAX(last_seen) FROM postings").fetchone()[0]
    soon = (datetime.now() + timedelta(days=7)).isoformat(timespec="minutes")
    return {
        "total": conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0],
        "listed": conn.execute(
            "SELECT COUNT(*) FROM postings WHERE last_seen >= ?", (last_crawl[:10] if last_crawl else "",)
        ).fetchone()[0],
        "due_7_days": conn.execute(
            "SELECT COUNT(*) FROM postings WHERE deadline BETWEEN ? AND ?", (now(), soon)
        ).fetchone()[0],
        "status": grouped("status"),
        "procedure": grouped("procedure"),
        "site": grouped("site"),
        "link_status": grouped("link_status"),
        "last_crawl": last_crawl,
    }


def postings_with_status(conn, status: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM postings WHERE status = ? ORDER BY deadline", (status,)).fetchall()


def save_decision(conn, posting_id: int, decision: dict) -> None:
    """Store the screener's decision. Keeps any answer Ali already gave."""
    with conn:
        conn.execute(
            """INSERT INTO decisions (posting_id, agent_decision, reason, flags, form_term, model, screened_at)
               VALUES (:id, :decision, :reason, :flags, :form_term, :model, :now)
               ON CONFLICT(posting_id) DO UPDATE SET agent_decision = :decision, reason = :reason,
                   flags = :flags, form_term = :form_term, model = :model, screened_at = :now""",
            {"id": posting_id, "now": now(), "decision": decision["decision"], "reason": decision.get("reason"),
             "flags": json.dumps(decision.get("flags", [])), "form_term": decision.get("form_term"),
             "model": decision.get("model")},
        )
        conn.execute("UPDATE postings SET status = ? WHERE id = ?", (decision["decision"], posting_id))


def set_ali_decision(conn, posting_id: int, choice: str) -> None:
    """Ali's answer from Discord wins over the screener's."""
    with conn:
        conn.execute("UPDATE decisions SET ali_decision = ?, decided_at = ? WHERE posting_id = ?",
                     (choice, now(), posting_id))
        conn.execute("UPDATE postings SET status = ? WHERE id = ?", (choice, posting_id))


def ali_examples(conn, limit: int = 40) -> list[sqlite3.Row]:
    """Ali's most recent answers, used as examples in the screening prompt."""
    return conn.execute(
        """SELECT p.org, p.title, d.agent_decision, d.ali_decision, d.reason FROM decisions d
           JOIN postings p ON p.id = d.posting_id WHERE d.ali_decision IS NOT NULL
           ORDER BY d.decided_at DESC LIMIT ?""", (limit,)
    ).fetchall()


def get_setting(conn, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                     (key, value, value))


def agreement(conn) -> dict:
    """How often Ali agreed with the screener, overall and on skips."""
    rows = conn.execute(
        "SELECT agent_decision, ali_decision FROM decisions WHERE ali_decision IS NOT NULL"
    ).fetchall()
    skips = [r for r in rows if r["agent_decision"] == "skip"]
    return {
        "reviewed": len(rows),
        "skips_reviewed": len(skips),
        "skips_agreed": sum(r["ali_decision"] == "skip" for r in skips),
    }


def propose_application(conn, posting_id: int, files: list[str], site: str | None) -> None:
    """Record that a package was sent to Ali for approval."""
    with conn:
        conn.execute(
            """INSERT INTO applications (posting_id, state, files, fix_requests, site, proposed_at)
               VALUES (?, 'pending', ?, '[]', ?, ?)
               ON CONFLICT(posting_id) DO UPDATE SET state = 'pending', files = excluded.files,
                   site = excluded.site, proposed_at = excluded.proposed_at""",
            (posting_id, json.dumps(files), site, now()),
        )


def set_application_state(conn, posting_id: int, state: str) -> None:
    with conn:
        conn.execute("UPDATE applications SET state = ? WHERE posting_id = ?", (state, posting_id))
        if state == "approved":
            conn.execute("UPDATE applications SET approved_at = ? WHERE posting_id = ?", (now(), posting_id))
            conn.execute("UPDATE postings SET status = 'approved' WHERE id = ?", (posting_id,))
        elif state == "skipped":
            # A skip at approval time is also a screening answer the screener learns from.
            conn.execute("UPDATE postings SET status = 'skip' WHERE id = ?", (posting_id,))
            conn.execute("UPDATE decisions SET ali_decision = 'skip', decided_at = ? WHERE posting_id = ?",
                         (now(), posting_id))


def add_fix_request(conn, posting_id: int, text: str) -> None:
    row = conn.execute("SELECT fix_requests FROM applications WHERE posting_id = ?", (posting_id,)).fetchone()
    requests = json.loads(row["fix_requests"] or "[]") + [text]
    with conn:
        conn.execute("UPDATE applications SET fix_requests = ?, state = 'fixing' WHERE posting_id = ?",
                     (json.dumps(requests), posting_id))


def get_application(conn, posting_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE posting_id = ?", (posting_id,)).fetchone()


def add_site_note(conn, site: str, note: str) -> None:
    with conn:
        conn.execute("INSERT OR IGNORE INTO site_notes (site, note, created_at) VALUES (?, ?, ?)",
                     (site, note, now()))


def site_notes(conn, site: str) -> list[str]:
    return [r["note"] for r in conn.execute("SELECT note FROM site_notes WHERE site = ? ORDER BY id", (site,))]


def set_application_notes(conn, posting_id: int, notes: dict) -> None:
    with conn:
        conn.execute("UPDATE applications SET notes = ? WHERE posting_id = ?", (json.dumps(notes), posting_id))


def mark_submitted(conn, posting_id: int) -> None:
    with conn:
        conn.execute("UPDATE applications SET state = 'submitted', submitted_at = ? WHERE posting_id = ?",
                     (now(), posting_id))
        conn.execute("UPDATE postings SET status = 'submitted' WHERE id = ?", (posting_id,))


def set_elevate_status(conn, posting_id: int, status: str) -> None:
    with conn:
        conn.execute("UPDATE postings SET elevate_status = ? WHERE id = ?", (status, posting_id))
