"""One application run: open, sign in, fill every page, stop at Review.

Dry run: screenshot the Review page to #log and abandon the draft.
Real run: post the approval card (with the screenshot) and keep the browser open on the
Review page until Ali approves (then submit) or skips (then close).
"""

import json
import time

from interniq import browser, db, discord_bot
from interniq.applier import workday as wd
from interniq.applier.filler import Filler
from interniq.writer import package

MAX_PAGES = 12
RETRIES_PER_PAGE = 2
APPROVAL_WAIT_HOURS = 12


def documents(posting_id: int) -> dict:
    summary = package.load_summary(posting_id)
    if not summary:
        raise SystemExit(f"No package for {posting_id}. Run `python -m interniq write {posting_id}` first.")
    folder = package.OUTPUT_DIR / str(posting_id)
    p = summary["package"]
    # The transcript is offered even when the posting didn't list it: forms often ask anyway.
    docs = {"resume": folder / p["resume_file_name"], "cover_letter": folder / p["cover_letter_file_name"],
            "transcript": folder / "Ali_Alanizi_Transcript.pdf"}
    if not docs["transcript"].exists():
        docs["transcript"] = package.TRANSCRIPT
    return {name: path for name, path in docs.items() if path.exists()}


def fill_until_review(page, filler: Filler) -> None:
    for _ in range(MAX_PAGES):
        if wd.is_review(page):
            return
        snapshot = wd.read_page(page)
        print(f"  page: {snapshot['step']} ({len(snapshot['fields'])} questions)")
        filler.fill_page(snapshot)
        for attempt in range(RETRIES_PER_PAGE + 1):
            new_step = wd.next_page(page)
            if new_step != snapshot["step"] or wd.is_review(page):
                break
            errors = wd.read_page(page)["errors"] or ["Workday stayed on this page without saying why"]
            if attempt == RETRIES_PER_PAGE:
                raise wd.Blocked(f"Stuck on {snapshot['step']}: {'; '.join(errors)[:300]}")
            print(f"  Workday refused: {errors}")
            filler.fill_page(wd.read_page(page), problems=errors)
    raise wd.Blocked("Too many pages without reaching Review")


def run(posting_id: int, job_url: str | None = None, dry: bool = False) -> None:
    conn = db.connect()
    posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
    decision = conn.execute("SELECT form_term FROM decisions WHERE posting_id = ?", (posting_id,)).fetchone()
    docs = documents(posting_id)
    url = job_url or posting["link"]
    shot = package.OUTPUT_DIR / str(posting_id) / ("dry_run_review.png" if dry else "review.png")
    label = f"{posting['org']}, {posting['title']}"

    with browser.open_browser() as context:
        page = browser.page_of(context)
        try:
            wd.open_application(page, url)
            if wd.current_step(page) == wd.STEPS[0]:
                wd.sign_in(page)
            filler = Filler(page, wd.SITE, posting, docs, decision["form_term"] if decision else None)
            fill_until_review(page, filler)
            page.screenshot(path=str(shot), full_page=True)
            print(f"  reached Review; screenshot saved to {shot}")
        except wd.Blocked as e:
            discord_bot.call(discord_bot.send("DISCORD_QUESTIONS_CHANNEL_ID",
                                              f"⛔ Stopped on **{label}** (`{posting_id}`): {e}"))
            raise

        notes = {"legal": filler.legal, "unsure": filler.unsure, "screenshot": shot.name}
        if dry:
            card = discord_bot.Card(
                f"🧪 **Dry run reached Review** for {label} using {url}\n"
                f"Legal items: {len(filler.legal)} · Unsure: {len(filler.unsure)}\n"
                + "\n".join(f"• {u}" for u in filler.unsure[:8]),
                files=[shot])
            discord_bot.call(discord_bot.send_many("DISCORD_LOG_CHANNEL_ID", [card]))
            print("  dry run done; closing the window abandons the draft")
            print(json.dumps(notes, indent=1, ensure_ascii=False))
            return

        discord_bot.propose([posting_id])  # records the pending application
        db.set_application_notes(conn, posting_id, notes)
        card = discord_bot.approval_card(posting_id)  # now includes the screenshot, legal, and unsure items
        discord_bot.call(discord_bot.send_many("DISCORD_APPROVALS_CHANNEL_ID", [card]))
        print("  approval card posted; keeping the Review page open until Ali decides")

        deadline = time.time() + APPROVAL_WAIT_HOURS * 3600
        while time.time() < deadline:
            page.wait_for_timeout(10_000)
            state = db.get_application(conn, posting_id)["state"]
            if state == "skipped":
                print("  Ali skipped it; closing")
                return
            if state == "fixing":
                print("  Ali asked for a fix; closing so the package can be redone and the run restarted")
                discord_bot.call(discord_bot.send("DISCORD_LOG_CHANNEL_ID",
                                                  f"✏️ {label}: run again after the fixed package is ready."))
                return
            if state == "approved":
                confirmed = wd.submit(page, posting_id)
                confirm_shot = shot.with_name("submitted.png")
                page.screenshot(path=str(confirm_shot), full_page=True)
                if confirmed:
                    db.mark_submitted(conn, posting_id)
                    message = f"📨 **Submitted** {label} (`{posting_id}`)."
                else:
                    message = (f"⚠️ Clicked Submit on {label} (`{posting_id}`) but didn't see a confirmation. "
                               "Check the screenshot and your email.")
                discord_bot.call(discord_bot.send_many("DISCORD_LOG_CHANNEL_ID",
                                                       [discord_bot.Card(message, files=[confirm_shot])]))
                page.wait_for_timeout(5000)
                return
        print("  no decision within the wait limit; closing (the draft stays in Workday)")
