"""Application runs: open, sign in, fill every page, stop at Review.

Several postings can run in one session (one browser, one sign-in). Each one stops at
its Review page with an approval card, and the page stays open until Ali approves
(then it's submitted) or skips; then the next posting starts.
Dry run: fill a decoy job, screenshot its Review page to #log, never submit.
"""

import json
import re
import time

from interniq import browser, db, discord_bot
from interniq.applier import avature, eightfold, generic, greenhouse, icims, oracle, raise_jobs
from interniq.applier import workday as wd
from interniq.applier.filler import Filler
from interniq.elevate import apply as elevate_apply
from interniq.elevate import crawler, intent
from interniq.writer import package

MAX_PAGES = 12
RETRIES_PER_PAGE = 2
APPROVAL_WAIT_HOURS = 12

# Site name (from the link check) -> the driver module that knows that site's pages.
DRIVERS = {"workday": wd, "icims": icims, "avature": avature, "eightfold": eightfold, "greenhouse": greenhouse, "raise": raise_jobs, "oracle": oracle}


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
        snap_step(page, filler, name)
        check_names(filler)
        for _ in range(MAX_PAGES):
            if driver.is_review(page):
                break
            driver.next_page(page)


def snap_step(page, filler: Filler, name: str) -> None:
    """Screenshot a step right after it's filled, and read back every question's final
    value. Sites without a review page (iCIMS) send the application from the last step,
    so these are Ali's only full view of his answers."""
    folder = filler.screenshot_dir
    if not folder:
        return
    number = len(filler.step_shots) + 1
    path = folder / f"step{number}_{re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')}.png"
    page.screenshot(path=str(path), full_page=True)
    filler.step_shots.append(path)
    filler.step_answers.append({"step": name, "fields": filler.driver.read_page(page)["fields"]})


def answers_report(filler: Filler) -> str:
    """Every question of every step with its final value, and who put it there."""
    lines, empty_required = [], []
    for number, step in enumerate(filler.step_answers, start=1):
        lines.append(f"\nSTEP {number}  {step['step']}")
        for f in step["fields"]:
            value = (f.get("value") or "").strip()
            label = re.sub(r"\s+", " ", f.get("label") or f.get("name") or "")[:110]
            empty = not value or value.lower().startswith(("— make", "-- make", "make a selection", "select one"))
            how = filler.touched.get(f["id"], "left empty" if empty else "kept what was already there")
            if f["kind"] == "file":
                value = value[:80]
            mark = "⚠️ " if f.get("required") and empty else "   "
            if mark.strip():
                empty_required.append(f"{step['step']}: {label}")
            lines.append(f"{mark}{label}: {value or '(empty)'}   [{how}]")
    head = ["ANSWERS AS SUBMITTED (read back from the form after filling)"]
    if empty_required:
        head.append("⚠️ REQUIRED QUESTIONS STILL EMPTY: " + "; ".join(empty_required))
    return "\n".join(head + lines) + "\n"


def check_names(filler: Filler) -> None:
    """Stop before leaving a step if a form field still holds Ali's resume name. Forms
    always get his legal name (answers.yaml); "Alanizi" belongs only on the resume
    and cover letter. Runs before Next, since leaving a step can save it (EA creates the
    account when Personal Information is left)."""
    wrong = [f"{step['step']}: {f.get('label')} = {f.get('value')!r}"
             for step in filler.step_answers for f in step["fields"]
             if f["kind"] != "file" and "alanizi" in (f.get("value") or "").lower()
             and "linkedin" not in (f.get("value") or "").lower()
             and "preferred" not in (f.get("label") or "").lower()]
    if wrong:
        raise wd.Blocked("A form field has 'Alanizi' instead of the legal name "
                         "(nothing past this step was saved): " + "; ".join(wrong)[:600])


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
                snap_step(page, filler, last['step'])
            check_names(filler)
            return
        snapshot = driver.read_page(page)
        visited.add(snapshot["step"])
        print(f"  page: {snapshot['step']} ({len(snapshot['fields'])} questions)")
        filler.fill_page(snapshot)
        snap_step(page, filler, snapshot["step"])
        check_names(filler)
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
    """Elevate "I intend to apply" for the posting and any duplicate listing of it. While
    Elevate is blocked the clicks wait: `python -m interniq intend` does every pending one
    in a single Elevate visit once Ali has run `unblock`."""
    if db.get_setting(conn, "elevate_blocked", ""):
        return "Elevate 'I intend to apply': queued (Elevate is blocked); `python -m interniq intend` after `unblock`."
    if not getattr(context, "interniq_elevate", False):
        # Employer sites use their own browser; Elevate runs in Chrome. The click happens
        # in one Chrome visit at the end of this run (see run()).
        return "Elevate 'I intend to apply': queued for the Chrome visit at the end of this run."
    lines = []
    for pid in [posting_id] + duplicates_of(conn, posting_id):
        try:
            lines.append(f"Elevate 'I intend to apply' ({pid}): {intent.express_intent(context, pid, via=posting_id)}")
        except crawler.ElevateBlocked:
            lines.append(f"⛔ Elevate blocked the browser; 'I intend to apply' ({pid}) is queued for later.")
            break
        except Exception as e:  # never let this hide a successful submit
            lines.append(f"⚠️ Elevate 'I intend to apply' ({pid}) failed ({e}); click it by hand.")
    return "\n".join(lines)


def run_one(context, page, posting_id: int, job_url: str | None, dry: bool) -> None:
    conn = db.connect()
    posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
    decision = conn.execute("SELECT form_term FROM decisions WHERE posting_id = ?", (posting_id,)).fetchone()
    if posting["site"] in ("email", "manual", None) and not job_url:
        raise SystemExit(f"{posting_id} has no application website (site {posting['site']!r})")
    driver = DRIVERS.get(posting["site"] or "", generic)  # any other site: the catch-all driver
    docs = documents(posting_id)
    url = job_url or posting["link"]
    folder = package.OUTPUT_DIR / str(posting_id)
    shot = folder / ("dry_run_review.png" if dry else "review.png")
    label = f"{posting['org']}, {posting['title']}"  # replaced by the page's own title once it's read

    try:
        filler = Filler(page, driver.SITE, posting, docs, decision["form_term"] if decision else None, driver)
        filler.screenshot_dir = folder
        # Sites with a shared profile that the application skips (iCIMS resumes at step 3):
        # update the profile first so this job gets its own resume.
        if hasattr(driver, "open_profile") and driver.open_profile(page, url):
            before = driver.stored_resume(page)
            print(f"  profile: replacing the stored resume ({before})")
            filler.fill_page(driver.read_page(page))
            snap_step(page, filler, "Candidate Profile")
            check_names(filler)
            result = driver.save_profile(page, before)
            print(f"  profile saved: {result}")
            if result.startswith("⚠️"):
                filler.unsure.append(f"**{result}**")
            else:
                filler.uploaded.add("resume")
        driver.open_application(page, url)
        page_title = driver.page_job_title(page)
        label = f"{posting['org']}, {page_title or posting['title']}"
        print(f"  page title: {page_title!r}")
        # Never fill a page for a different job than the posting (a dry run fills a decoy
        # on purpose, so it only reports the page's title).
        if not dry and not wd.titles_match(posting["title"], page_title) and not (
                hasattr(driver, "mentions_title") and driver.mentions_title(page, posting["title"])):
            raise wd.Blocked(f"The page is for {page_title!r}, not this posting ({posting['title']!r}). "
                             "Nothing was filled.")
        driver.ensure_signed_in(page)
        fill_until_review(page, filler, driver)
        if hasattr(driver, "card_notes"):
            filler.unsure[:0] = driver.card_notes(page)
        if hasattr(driver, "after_submit_page"):
            filler.unsure.append("Submit presses this page's button once. If the site shows another page "
                                 "instead of a confirmation, you'll get an Approval 2 card for it first.")
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
             "files_expected": expected_names, "files_missing": missing, "files_no_slot": no_slot,
             "step_shots": [p.name for p in filler.step_shots]}
    report = folder / ("dry_run_answers.txt" if dry else "answers.txt")
    report.write_text(answers_report(filler), encoding="utf-8")
    notes["answers_report"] = report.name
    if dry:
        card = discord_bot.Card(
            f"🧪 **Dry run reached Review** for {label}\n{url}\n"
            f"Legal items: {len(filler.legal)} · Unsure: {len(filler.unsure)}\n"
            + "\n".join(f"• {u}" for u in filler.unsure[:8])
            + "\nThe window stays open on Review until you close it. Nothing will be submitted.",
            files=[report] + filler.step_shots[:9])
        discord_bot.call(discord_bot.send_many("DISCORD_LOG_CHANNEL_ID", [card]))
        print(json.dumps(notes, indent=1, ensure_ascii=False))
        print("  dry run done; waiting for Ali to close the window (that abandons the draft)")
        try:
            page.wait_for_event("close", timeout=APPROVAL_WAIT_HOURS * 3600 * 1000)
        except Exception:  # window closed with the whole browser, or the wait ran out
            pass
        return

    def form_fix(note: str) -> dict:
        """Ali's Fix on a form card: apply his note to the open page, re-read it, new card."""
        filler.fill_page(driver.read_page(page), ali_note=note)
        snap_step(page, filler, "after your fix")
        check_names(filler)
        fixed = folder / "after_fix.png"
        page.screenshot(path=str(fixed), full_page=True)
        report.write_text(answers_report(filler), encoding="utf-8")
        notes.update({"legal": filler.legal, "unsure": [f"**Fixed as you asked:** {note}"] + filler.unsure,
                      "screenshot": fixed.name, "step_shots": [p.name for p in filler.step_shots[-9:]]})
        return notes

    print("  posting the approval card; keeping the Review page open until Ali decides")
    if wait_for_decision(page, conn, posting_id, label, notes, on_fix=form_fix) != "approved":
        return
    confirmed = driver.submit(page, posting_id)
    # Some sites (EA) show another page after the press that was supposed to send it.
    # Each surprise page is filled (asking Ali when the bank has no answer) and gets its
    # own approval card before anything is pressed again.
    for round_number in range(2, MAX_PAGES):
        if confirmed or not hasattr(driver, "after_submit_page"):
            break
        snapshot = driver.after_submit_page(page)
        if snapshot is None:  # nothing left to fill: the site's own list decides
            confirmed = driver.confirmed_elsewhere(page)
            break
        print(f"  another page after Submit: {snapshot['step'] or '(no step name)'} "
              f"({len(snapshot['fields'])} questions); approval {round_number} needed")
        filler.legal, filler.unsure = [], []
        filler.fill_page(snapshot)
        snap_step(page, filler, f"after approval {round_number - 1}")
        check_names(filler)
        filler.unsure.insert(0, f"**Approval {round_number}:** the page after your last Submit wasn't the final "
                                "one. These answers are new; nothing was pressed after filling them.")
        shot = folder / f"approval{round_number}.png"
        page.screenshot(path=str(shot), full_page=True)
        report.write_text(answers_report(filler), encoding="utf-8")
        notes.update({"legal": filler.legal, "unsure": filler.unsure, "screenshot": shot.name,
                      "step_shots": [p.name for p in filler.step_shots[-9:]]})
        if wait_for_decision(page, conn, posting_id, f"{label} (approval {round_number})", notes,
                             on_fix=form_fix) != "approved":
            return
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


def wait_for_decision(page, conn, posting_id: int, label: str, notes: dict, on_fix=None) -> str | None:
    """Post the approval card, then wait for Ali's tap. Returns "approved", or None when
    he skipped or didn't decide in time.

    With `on_fix`, the form is open behind the card: a Fix tap is a correction to the form
    (the bot sees the `live_form` setting and doesn't rewrite the package). on_fix(note)
    applies it and returns the updated notes, and a new card goes up."""
    live = f"live_form:{posting_id}"
    try:
        while True:
            discord_bot.propose([posting_id])  # records the pending application
            db.set_application_notes(conn, posting_id, notes)
            if on_fix:
                db.set_setting(conn, live, "1")
            card = discord_bot.approval_card(posting_id)  # includes the screenshots, legal, and unsure items
            discord_bot.call(discord_bot.send_many("DISCORD_APPROVALS_CHANNEL_ID", [card]))
            state = None
            deadline = time.time() + APPROVAL_WAIT_HOURS * 3600
            while time.time() < deadline:
                page.wait_for_timeout(10_000)
                state = db.get_application(conn, posting_id)["state"]
                if state in ("approved", "skipped", "fixing"):
                    break
            if state == "approved":
                return state
            if state == "skipped":
                print("  Ali skipped it")
                return None
            if state == "fixing" and on_fix:
                note = json.loads(db.get_application(conn, posting_id)["fix_requests"] or "[]")[-1]
                print(f"  Ali asked for a change on the form: {note[:100]}")
                notes = on_fix(note)
                continue  # a new card for the corrected page
            if state == "fixing":
                print("  Ali asked for a fix; moving on (run it again after the fixed package is ready)")
                discord_bot.call(discord_bot.send("DISCORD_LOG_CHANNEL_ID",
                                                  f"✏️ {label}: run again after the fixed package is ready."))
                return None
            print("  no decision within the wait limit; moving on")
            return None
    finally:
        db.set_setting(conn, live, "")


def run_elevate(context, page, posting_id: int) -> None:
    """An "Accumulate Online" posting: the package is built and submitted inside Elevate."""
    conn = db.connect()
    posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
    label = f"{posting['org']}, {posting['title']}"
    docs = documents(posting_id)
    materials = " ".join(json.loads(posting["materials"] or "[]")).lower()
    kinds = ["resume"]
    if "cover" in materials and package.load_summary(posting_id).get("cover_letter"):
        kinds.append("cover_letter")
    if "transcript" in materials:
        kinds.append("transcript")
    folder = package.OUTPUT_DIR / str(posting_id)
    if elevate_apply.ONE_DOCUMENT.search(f"{posting['description']} {posting['additional_info'] or ''}"):
        # Letter first, then resume, then transcript, as one PDF.
        docs["combined"] = folder / "Ali_Alanizi_Application.pdf"
        order = [k for k in ("cover_letter", "resume", "transcript") if k in kinds]
        elevate_apply.merge([docs[k] for k in order], docs["combined"])
        print(f"  the posting wants one document: merged {', '.join(order)}")
        kinds = ["combined"]
    shot = folder / "elevate_package.png"

    try:
        if posting_id in elevate_apply.submitted_jobs(page):
            raise elevate_apply.Blocked("Elevate already lists this job under Application Submitted")
        tab = elevate_apply.open_apply_form(context, page, posting_id)
        library = elevate_apply.library_options(tab)
        missing = [k for k in kinds if not any(o.startswith(elevate_apply.doc_name(k, posting_id) + " (")
                                               for o in library[k] or [])]
        if missing:
            tab.close()
            for kind in missing:
                print(f"  uploading {elevate_apply.doc_name(kind, posting_id)} to the Elevate library")
                elevate_apply.upload_document(page, docs[kind], elevate_apply.doc_name(kind, posting_id), kind)
            tab = elevate_apply.open_apply_form(context, page, posting_id)
        chosen = elevate_apply.build_package(tab, posting_id, posting["org"], kinds)
        print(f"  package: {chosen}")
        tab.screenshot(path=str(shot), full_page=True)
        tab.close()  # nothing is kept open: the form is rebuilt after approval
    except elevate_apply.Blocked as e:
        discord_bot.call(discord_bot.send("DISCORD_QUESTIONS_CHANNEL_ID",
                                          f"⛔ Stopped on **{label}** (`{posting_id}`): {e}"))
        raise wd.Blocked(str(e))

    notes = {"legal": [], "unsure": [], "screenshot": shot.name, "step_shots": [shot.name],
             "files_expected": [chosen[k] for k in kinds], "files_missing": [], "files_no_slot": []}
    if wait_for_decision(page, conn, posting_id, label, notes) != "approved":
        return
    tab = elevate_apply.open_apply_form(context, page, posting_id)
    again = elevate_apply.build_package(tab, posting_id, posting["org"], kinds)
    if again != chosen:
        tab.close()
        raise wd.Blocked(f"The package changed since you approved it ({again} vs {chosen}); nothing was sent.")
    elevate_apply.submit(tab, posting_id)
    confirm_shot = folder / "submitted.png"
    tab.screenshot(path=str(confirm_shot), full_page=True)
    tab.close()
    if posting_id in elevate_apply.submitted_jobs(page):
        db.mark_submitted(conn, posting_id)
        db.set_elevate_status(conn, posting_id, elevate_apply.SUBMITTED)
        message = f"📨 **Submitted on Elevate** {label} (`{posting_id}`); it's listed under Application Submitted."
    else:
        message = (f"⚠️ Pressed Submit Application on {label} (`{posting_id}`) but it isn't listed under "
                   "Application Submitted. Check the screenshot and Elevate.")
    discord_bot.call(discord_bot.send_many("DISCORD_LOG_CHANNEL_ID",
                                           [discord_bot.Card(message, files=[confirm_shot])]))


def run(posting_ids: list[int], job_url: str | None = None, dry: bool = False) -> None:
    """One browser session for every posting, in order. A posting that gets stuck is
    reported and the session moves on to the next one."""
    conn = db.connect()
    procedures = {r["id"]: r["procedure"] for r in conn.execute(
        f"SELECT id, procedure FROM postings WHERE id IN ({','.join('?' * len(posting_ids))})", posting_ids)}
    inside_elevate = [p for p in posting_ids if procedures.get(p) == "Accumulate Online" and not dry]
    if inside_elevate and len(inside_elevate) != len(posting_ids):
        raise SystemExit("Run postings applied to inside Elevate separately from employer-site ones "
                         f"(Elevate: {inside_elevate})")
    with browser.open_browser(elevate=bool(inside_elevate)) as context:
        page = browser.page_of(context)
        for posting_id in posting_ids:
            print(f"Applying to {posting_id}...")
            procedure = db.connect().execute("SELECT procedure FROM postings WHERE id = ?",
                                             (posting_id,)).fetchone()["procedure"]
            try:
                if procedure == "Accumulate Online" and not dry:
                    run_elevate(context, page, posting_id)
                else:
                    run_one(context, page, posting_id, job_url, dry)
            except wd.Blocked as e:
                print(f"  stopped: {e}")
                if len(posting_ids) == 1:
                    raise
    if not inside_elevate and not dry:
        intent_visit(conn)


def intent_visit(conn) -> None:
    """One Chrome visit for every "I intend to apply" this run queued."""
    if not intent.pending(conn) or db.get_setting(conn, "elevate_blocked", ""):
        return
    with browser.open_browser(elevate=True) as context:
        lines = intent.express_all(context)
    print("\n".join(lines))
    discord_bot.call(discord_bot.send("DISCORD_LOG_CHANNEL_ID", "Elevate 'I intend to apply':\n" + "\n".join(lines)))
