"""Talking to the agent in plain words on Discord.

Ali mentions the bot ("@InternIQ I applied to GeoComply, click I intend") and a small
Claude model turns the message into one of a few safe actions: record an application he
made himself (plus Elevate's "I intend to apply"), queue a posting for applying, show
status, pause or resume the daily check, or run it now. Nothing here can submit an
application: submitting still only happens from an approval card.
"""

import json
import subprocess

import anthropic

from interniq import config, db
from interniq.elevate import clean_title

PYTHON = config.ROOT / ".venv" / "Scripts" / "python.exe"

SYSTEM = """You are InternIQ's Discord helper for Ali, a student whose agent applies to
internships for him. Turn his message into tool calls. Postings can be named by company
or by their 6 digit id; pass whatever he said to the tools, which look them up.
- He says he applied, submitted, or finished an application himself, or asks you to click
  "I intend to apply": call mark_applied.
- He wants the agent to apply to a posting, or to retry one that failed: call apply_to.
- Status, pause, resume, or "check for new postings now": the matching tool.
If his message isn't one of these, answer in one or two short sentences without tools and
say what you can do. Reply in a casual, short tone. Never claim something happened that a
tool result doesn't show."""

TOOLS = [
    {"name": "mark_applied", "description": "Ali applied to this posting himself: record it as submitted and click "
     "'I intend to apply' on Elevate.", "input_schema": {"type": "object", "properties": {
         "posting": {"type": "string", "description": "company name or posting id, as Ali said it"}},
         "required": ["posting"]}},
    {"name": "apply_to", "description": "Queue a posting for the agent to write and fill (ends at an approval card).",
     "input_schema": {"type": "object", "properties": {"posting": {"type": "string"}}, "required": ["posting"]}},
    {"name": "status", "description": "Show the InternIQ status summary.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "pause", "description": "Pause the nightly check.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "resume", "description": "Resume the nightly check.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_now", "description": "Run the daily check now.", "input_schema": {"type": "object", "properties": {}}},
]


def find(conn, text: str) -> list[dict]:
    """Postings matching an id or a company name (newest first)."""
    text = text.strip()
    if text.isdigit():
        rows = conn.execute("SELECT id, org, title FROM postings WHERE id = ?", (int(text),)).fetchall()
    else:
        rows = conn.execute("SELECT id, org, title FROM postings WHERE org LIKE ? ORDER BY id DESC",
                            (f"%{text}%",)).fetchall()
    return [dict(r) for r in rows]


def pick(conn, text: str) -> tuple[dict | None, str]:
    found = find(conn, text)
    if not found:
        return None, f"I can't find a posting matching {text!r}."
    if len(found) > 1:
        options = "; ".join(f"`{p['id']}` {p['org']}, {clean_title(p['title'])[:50]}" for p in found[:6])
        return None, f"{text!r} matches {len(found)} postings: {options}. Which id?"
    return found[0], ""


def mark_applied(text: str) -> tuple[str, list[int]]:
    conn = db.connect()
    posting, problem = pick(conn, text)
    if not posting:
        return problem, []
    app = db.get_application(conn, posting["id"])
    if not app:
        db.propose_application(conn, posting["id"], [], "manual")
    db.mark_submitted(conn, posting["id"])
    total = conn.execute("SELECT count(*) FROM applications WHERE state = 'submitted'").fetchone()[0]
    return (f"Recorded **{posting['org']}** (`{posting['id']}`) as submitted ({total} total). Clicking "
            "\"I intend to apply\" on Elevate now; the result comes in a minute.", [posting["id"]])


def apply_to(text: str) -> str:
    from interniq.applier import worker

    conn = db.connect()
    posting, problem = pick(conn, text)
    if not posting:
        return problem
    db.set_ali_decision(conn, posting["id"], "apply")
    place = worker.enqueue(posting["id"])
    worker.spawn()
    return f"Queued **{posting['org']}** (`{posting['id']}`), place {place}. The final card comes to #approvals."


def intend_in_background(posting_ids: list[int]) -> str:
    """Run the Elevate click in its own process (one Chrome session) and return its output."""
    if db.get_setting(db.connect(), "daily_running", ""):
        return "The daily check is using Elevate right now; try again in a few minutes."
    lines = []
    for posting_id in posting_ids:
        done = subprocess.run([str(PYTHON), "-m", "interniq", "intend", str(posting_id)], cwd=str(config.ROOT),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        lines.append((done.stdout.strip().splitlines() or [done.stderr.strip()[-200:] or "no output"])[-1])
    return "Elevate: " + "; ".join(lines)


def reply(message: str, commands: dict) -> tuple[str, list[int]]:
    """The answer to Ali's message, plus postings whose Elevate click should run next."""
    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))
    response = client.messages.create(model=config.SCREEN_MODEL, max_tokens=500, system=SYSTEM, tools=TOOLS,
                                      messages=[{"role": "user", "content": message}])
    texts, intents = [], []
    for block in response.content:
        if block.type == "text" and block.text.strip():
            texts.append(block.text.strip())
        elif block.type == "tool_use":
            args = block.input or {}
            if block.name == "mark_applied":
                text, ids = mark_applied(args.get("posting", ""))
                texts.append(text)
                intents += ids
            elif block.name == "apply_to":
                texts.append(apply_to(args.get("posting", "")))
            elif block.name == "run_now":
                texts.append(commands["!run"]())
            else:
                texts.append(commands["!" + block.name]())
    return "\n".join(texts) or "Not sure what you mean. I can record an application, apply, pause, resume, or show status.", intents
