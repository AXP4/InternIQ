"""Applications that go by email ("Employer Email" postings).

The agent writes the package and a short email (subject and body), then posts a card in
#approvals with the address, the text, and the PDFs to attach. Ali sends it from his own
Outlook (the agent has no access to his university mailbox) and taps Submit on the card,
which here means "I sent it": the application is recorded as submitted and the Elevate
"I intend to apply" click follows.
"""

import json
import re

import anthropic

from interniq import config, db, discord_bot
from interniq.applier import run
from interniq.elevate import apply as elevate_apply
from interniq.elevate import clean_title
from interniq.writer import package

INSTRUCTIONS = """Write the email that carries a co-op application (resume, cover letter, and
transcript are attached). Sound like a real student writing it himself: warm, plain, short.
Rules:
- Subject: the job title (as the employer wrote it, without Elevate's program codes) and
  "University of Calgary". No dashes or hyphens anywhere in the subject or body: write
  "Co op" as "Coop" and use commas or new sentences instead of dashes.
- Body: a greeting, 2 or 3 short paragraphs, then the signature. Say the documents are
  attached, one sentence on why this role (taken from the cover letter), and the availability
  sentence "I'm available starting January 2027." Never state a term length.
- No contractions anywhere (Ali's preference): write "I am", "I would", "I have", "do not",
  "it is", never "I'm", "I'd", "I've", "don't", "it's".
- One closing line before the signature (for example "Thank you for your time."), never two.
- Claim nothing that is not in the cover letter or the facts given.
- Signature lines, exactly: "Ali Alanizi", "PHONE_REMOVED", "EMAIL_REMOVED".
- The posting's own application instructions win over the rules above: if it names an exact
  subject line, use it word for word (dashes and colons included). If it asks for the
  documents combined into one PDF with a given file name, return that name in
  single_pdf_name, with "Full Name" replaced by "Ali LEGALNAME" (no ".pdf"); otherwise null.
Return JSON: {"subject": "...", "body": "...", "single_pdf_name": "..." or null}"""

DASHES = re.compile(r"[-‐-―]")
# I'm, I'd, I've, I'll, we're, don't, it's, that's... (possessives like "SeisWare's" are fine)
CONTRACTIONS = re.compile(r"\b(?:\w+n['’]t|I['’](?:m|d|ve|ll)|\w+['’](?:re|ve|ll|d)|"
                          r"(?:it|that|there|here|what|who|he|she|let)['’]s)\b", re.I)


def compose(posting: dict, summary: dict) -> dict:
    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))
    letter = "\n\n".join(summary["package"].get("paragraphs", []))
    keys = ("org", "title", "location", "email", "additional_info", "description")
    prompt = (f"<posting>{json.dumps({k: posting[k] for k in keys})}</posting>\n"
              f"<cover_letter>{letter}</cover_letter>")
    posting_text = f"{posting['additional_info'] or ''} {posting['description'] or ''}"
    for _ in range(3):
        response = client.messages.create(model=config.WRITE_MODEL, max_tokens=1500, system=INSTRUCTIONS,
                                          messages=[{"role": "user", "content": prompt}])
        text = next(b.text for b in response.content if b.type == "text")  # skip thinking blocks
        draft = json.loads(text[text.find("{"): text.rfind("}") + 1])
        # A subject the posting dictates word for word may contain dashes; Ali's own words may not.
        subject = "" if draft["subject"].strip() and draft["subject"].strip() in posting_text else draft["subject"]
        visible = subject + draft["body"].replace("EMAIL_REMOVED", "")
        problems = []
        if DASHES.search(visible):
            problems.append("a dash or hyphen")
        if found := CONTRACTIONS.findall(visible):
            problems.append(f"contractions ({', '.join(sorted(set(found)))})")
        if not problems:
            return draft
        prompt += f"\n\nYour last draft had {' and '.join(problems)}. Rewrite it without any."
    raise RuntimeError("The email kept breaking the dash or contraction rule; write it by hand")


def card(posting: dict, draft: dict, files: list) -> discord_bot.Card:
    due = (posting.get("deadline") or "")[:10]
    text = "\n".join([
        f"**Email application**  `{posting['id']}`",
        f"**{posting['org']}**, {clean_title(posting['title'])}" + (f" · deadline {due}" if due else ""),
        f"**To:** {posting['email']}",
        f"**Subject:** {draft['subject']}",
        f"```\n{draft['body']}\n```",
        f"📎 Attach the {len(files)} PDFs below. Send it from your UCalgary Outlook, then tap **✅ Submit** "
        "here to record it as sent (that also queues the Elevate \"I intend to apply\"). Fix rewrites, Skip skips.",
    ])
    return discord_bot.Card(text[:1990], "approve", posting["id"], files)


def run_email(posting_ids: list[int]) -> None:
    conn = db.connect()
    for posting_id in posting_ids:
        posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
        summary = package.load_summary(posting_id)
        if not summary:
            raise SystemExit(f"No package for {posting_id}. Run `python -m interniq write {posting_id}` first.")
        docs = run.documents(posting_id)
        materials = " ".join(json.loads(posting["materials"] or "[]")).lower()
        files = [docs[k] for k in ("cover_letter", "resume", "transcript")
                 if k in docs and (k == "resume" or k.split("_")[0] in materials)]
        draft = compose(posting, summary)
        folder = package.OUTPUT_DIR / str(posting_id)
        if draft.get("single_pdf_name"):  # the posting wants everything in one named PDF
            merged = folder / f"{draft['single_pdf_name'].strip()}.pdf"
            elevate_apply.merge(files, merged)
            files = [merged]
        (folder / "email.txt").write_text(f"To: {posting['email']}\nSubject: {draft['subject']}\n\n{draft['body']}\n",
                                          encoding="utf-8")
        db.propose_application(conn, posting_id, [p.name for p in files], "email")
        db.set_application_notes(conn, posting_id, {"email": draft, "to": posting["email"]})
        discord_bot.call(discord_bot.send_many("DISCORD_APPROVALS_CHANNEL_ID", [card(posting, draft, files)]))
        print(f"{posting_id}: email card posted ({draft['subject']})")
