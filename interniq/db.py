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
        "SELECT id, title, org, link FROM postings"
        " WHERE link_checked_at IS NULL OR link_status = 'down' ORDER BY deadline"
    ).fetchall()


def save_link_check(conn, posting_id: int, result: dict) -> None:
    with conn:
        conn.execute(
            "UPDATE postings SET site = ?, link_status = ?, link_note = ?, link_final_url = ?, link_checked_at = ?"
            " WHERE id = ?",
            (result["site"], result["link_status"], result.get("note"), result.get("final_url"), now(), posting_id),
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
