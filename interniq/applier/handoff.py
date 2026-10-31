"""Applications Ali submits himself, with everything prepared by the agent.

Used for "Accumulate Online" postings (applied to inside Elevate) while Elevate blocks the
agent's browser: the agent writes the package and posts a card with the PDFs and the exact
steps. Ali uploads and submits in his own browser, then taps Submit on the card, which
here means "I submitted it" (same as email cards).
"""

import json

from interniq import db, discord_bot
from interniq.applier import run
from interniq.elevate import clean_title
from interniq.writer import package


def card(posting: dict, files: list) -> discord_bot.Card:
    due = (posting.get("deadline") or "")[:10]
    names = ", ".join(p.name for p in files)
    text = "\n".join([
        f"**Submit this one yourself on Elevate**  `{posting['id']}`",
        f"**{posting['org']}**, {clean_title(posting['title'])}" + (f" · deadline {due}" if due else ""),
        "Elevate is blocking the agent's browser, so this posting (applied to inside Elevate) is yours to submit:",
        f"1. Download the PDFs below ({names}).",
        f"2. On Elevate, open posting **{posting['id']}** and press **APPLY**.",
        "3. Choose **Create a customized application package**, upload each PDF as its type "
        "(Resumé, Cover Letter, Unofficial Transcript), and press **Submit Application**.",
        "4. Tap **✅ Submit** here to record it (Skip if you decide not to).",
    ])
    return discord_bot.Card(text[:1990], "approve", posting["id"], files)


def run_handoff(posting_ids: list[int]) -> None:
    conn = db.connect()
    for posting_id in posting_ids:
        posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
        if not package.load_summary(posting_id):
            raise SystemExit(f"No package for {posting_id}. Run `python -m interniq write {posting_id}` first.")
        docs = run.documents(posting_id)
        materials = " ".join(json.loads(posting["materials"] or "[]")).lower()
        kinds = ["resume"] + [k for k, word in (("cover_letter", "cover"), ("transcript", "transcript"))
                              if word in materials and k in docs]
        files = [docs[k] for k in kinds]
        db.propose_application(conn, posting_id, [p.name for p in files], "manual")
        discord_bot.call(discord_bot.send_many("DISCORD_APPROVALS_CHANNEL_ID", [card(posting, files)]))
        print(f"{posting_id}: submit-it-yourself card posted ({', '.join(p.name for p in files)})")
