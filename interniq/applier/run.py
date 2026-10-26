"""Application runs: open, sign in, fill every page, stop at Review.

Several postings can run in one session (one browser, one sign-in). Each one stops at
its Review page with an approval card, and the page stays open until Ali approves
(then it's submitted) or skips; then the next posting starts.
Dry run: fill a decoy job, screenshot its Review page to #log, never submit.
"""

import json
import time

from interniq import browser, db, discord_bot
from interniq.applier import icims
from interniq.applier import workday as wd
from interniq.applier.filler import Filler
from interniq.elevate import intent
from interniq.writer import package

MAX_PAGES = 12
RETRIES_PER_PAGE = 2
APPROVAL_WAIT_HOURS = 12

# Site name (from the link check) -> the driver module that knows that site's pages.
DRIVERS = {"workday": wd, "icims": icims}


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


def revisit_skipped(page, filler: Filler, driver, visited: set[str]) -> None:
    """Some sites jump over steps they consider complete (answers from an earlier session).
    Go back to each one this run never saw, fill it, and return to the final step."""
    if not hasattr(driver, "step_names"):
        return
    for name in driver.step_names(page)[:-1]:
        if name in visited:
            continue
        if not driver.open_step(page, name):
            filler.unsure.append(f"**The site skipped the '{name}' step and I couldn't reopen it.** "
                                 "Its answers are from an earlier session; check them before submitting.")
            continue
        print(f"  reopened skipped step: {name}")
        visited.add(name)
        filler.fill_page(driver.read_page(page))
        for _ in range(MAX_PAGES):
            if driver.is_review(page):
                break
            driver.next_page(page)


def fill_until_review(page, filler: Filler, driver) -> None:
    visited: set[str] = set()
    for _ in range(MAX_PAGES):
        if driver.is_review(page):
            revisit_skipped(page, filler, driver, visited)
            # Some sites put questions on the same page as the final Submit (iCIMS); fill
            # them, then stop. A pure review page (Workday) has no questions to fill.
            last = driver.read_page(page)
            if last["fields"]:
                print(f"  final page: {last['step']} ({len(last['fields'])} questions)")
                filler.fill_page(last)
            return
        snapshot = driver.read_page(page)
        visited.add(snapshot["step"])
        print(f"  page: {snapshot['step']} ({len(snapshot['fields'])} questions)")
        filler.fill_page(snapshot)
        for attempt in range(RETRIES_PER_PAGE + 1):
            new_step = driver.next_page(page)
            if new_step != snapshot["step"] or driver.is_review(page):
                break
            errors = driver.read_page(page)["errors"] or ["The site stayed on this page without saying why"]
            if attempt == RETRIES_PER_PAGE:
                raise wd.Blocked(f"Stuck on {snapshot['step']}: {'; '.join(errors)[:300]}")
            print(f"  the site refused: {errors}")
            filler.fill_page(driver.read_page(page), problems=errors)
    raise wd.Blocked("Too many pages without reaching Review")


def duplicates_of(conn, posting_id: int) -> list[int]:
    """Elevate postings marked as duplicates of this one (same employer job listed twice)."""
    return [r["posting_id"] for r in conn.execute(
        "SELECT d.posting_id FROM decisions d JOIN postings p ON p.id = d.posting_id "
        "WHERE p.status = 'duplicate' AND d.reason LIKE ?", (f"%Duplicate of {posting_id}%",))]


def after_submit(context, conn, posting_id: int) -> str:
    """Elevate "I intend to apply" for the posting and any duplicate listing of it."""
    lines = []
    for pid in [posting_id] + duplicates_of(conn, posting_id):
        try:
            lines.append(f"Elevate 'I intend to apply' ({pid}): {intent.express_intent(context, pid, via=posting_id)}")
        except Exception as e:  # never let this hide a successful submit
            lines.append(f"⚠️ Elevate 'I intend to apply' ({pid}) failed ({e}); click it by hand.")
    return "\n".join(lines)


def run_one(context, page, posting_id: int, job_url: str | None, dry: bool) -> None:
    conn = db.connect()
    posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
    decision = conn.execute("SELECT form_term FROM decisions WHERE posting_id = ?", (posting_id,)).fetchone()
    driver = DRIVERS.get(posting["site"] or "")
    if driver is None:
        raise SystemExit(f"No applier for site {posting['site']!r} yet ({posting_id})")
    docs = documents(posting_id)
    url = job_url or posting["link"]
    folder = package.OUTPUT_DIR / str(posting_id)
    shot = folder / ("dry_run_review.png" if dry else "review.png")
    label = f"{posting['org']}, {posting['title']}"  # replaced by the page's own title once it's read

    try:
        driver.open_application(page, url)
        page_title = driver.page_job_title(page)
        label = f"{posting['org']}, {page_title or posting['title']}"
        print(f"  page title: {page_title!r}")
        # Never fill a page for a different job than the posting (a dry run fills a decoy
        # on purpose, so it only reports the page's title).
        if not dry and not wd.titles_match(posting["title"], page_title):
            raise wd.Blocked(f"The page is for {page_title!r}, not this posting ({posting['title']!r}). "
                             "Nothing was filled.")
        driver.ensure_signed_in(page)
        filler = Filler(page, driver.SITE, posting, docs, decision["form_term"] if decision else None, driver)
        fill_until_review(page, filler, driver)
        expected = {"resume"} | filler.uploaded
        if package.load_summary(posting_id).get("cover_letter"):
            expected.add("cover_letter")
        expected_names = sorted(docs[d].name for d in expected if d in docs)
        # A document with no upload slot on the form isn't "missing", just not asked for.
        no_slot = sorted(docs[d].name for d in expected - filler.attempted - {"resume"} if d in docs)
        expected_names = [n for n in expected_names if n not in no_slot]
        if getattr(driver, "SHOWS_FILES_ON_REVIEW", True):
            missing = driver.files_on_review(page, expected_names)
        else:  # the site doesn't list files before submit: trust the confirmed uploads
            missing = sorted(docs[d].name for d in expected - filler.uploaded if d in docs and docs[d].name not in no_slot)
        print(f"  files on Review: expected {expected_names}, missing {missing or 'none'}")
        page.screenshot(path=str(shot), full_page=True)
        print(f"  reached Review; screenshot saved to {shot}")
    except wd.Blocked as e:
        discord_bot.call(discord_bot.send("DISCORD_QUESTIONS_CHANNEL_ID",
                                          f"⛔ Stopped on **{label}** (`{posting_id}`): {e}"))
        raise

    notes = {"legal": filler.legal, "unsure": filler.unsure, "screenshot": shot.name, "page_title": page_title,
             "files_expected": expected_names, "files_missing": missing, "files_no_slot": no_slot}
    if dry:
        card = discord_bot.Card(
            f"🧪 **Dry run reached Review** for {label}\n{url}\n"
            f"Legal items: {len(filler.legal)} · Unsure: {len(filler.unsure)}\n"
            + "\n".join(f"• {u}" for u in filler.unsure[:8])
            + "\nThe window stays open on Review until you close it. Nothing will be submitted.",
            files=[shot])
        discord_bot.call(discord_bot.send_many("DISCORD_LOG_CHANNEL_ID", [card]))
        print(json.dumps(notes, indent=1, ensure_ascii=False))
        print("  dry run done; waiting for Ali to close the window (that abandons the draft)")
        try:
            page.wait_for_event("close", timeout=APPROVAL_WAIT_HOURS * 3600 * 1000)
        except Exception:  # window closed with the whole browser, or the wait ran out
            pass
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
            print("  Ali skipped it")
            return
        if state == "fixing":
            print("  Ali asked for a fix; moving on (run it again after the fixed package is ready)")
            discord_bot.call(discord_bot.send("DISCORD_LOG_CHANNEL_ID",
                                              f"✏️ {label}: run again after the fixed package is ready."))
            return
        if state == "approved":
            confirmed = driver.submit(page, posting_id)
            confirm_shot = shot.with_name("submitted.png")
            page.screenshot(path=str(confirm_shot), full_page=True)
            if confirmed:
                db.mark_submitted(conn, posting_id)
                message = f"📨 **Submitted** {label} (`{posting_id}`).\n" + after_submit(context, conn, posting_id)
            else:
                message = (f"⚠️ Clicked Submit on {label} (`{posting_id}`) but didn't see a confirmation. "
                           "Check the screenshot and your email.")
            discord_bot.call(discord_bot.send_many("DISCORD_LOG_CHANNEL_ID",
                                                   [discord_bot.Card(message, files=[confirm_shot])]))
            page.wait_for_timeout(5000)
            return
    print("  no decision within the wait limit; moving on (the draft stays saved on the site)")


def run(posting_ids: list[int], job_url: str | None = None, dry: bool = False) -> None:
    """One browser session for every posting, in order. A posting that gets stuck is
    reported and the session moves on to the next one."""
    with browser.open_browser() as context:
        page = browser.page_of(context)
        for posting_id in posting_ids:
            print(f"Applying to {posting_id}...")
            try:
                run_one(context, page, posting_id, job_url, dry)
            except wd.Blocked as e:
                print(f"  stopped: {e}")
                if len(posting_ids) == 1:
                    raise
