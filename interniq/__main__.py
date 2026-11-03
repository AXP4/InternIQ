"""Command line entry point: python -m interniq <command>"""

import argparse
import asyncio

from interniq import discord_bot


def ping() -> None:
    asyncio.run(discord_bot.send("DISCORD_LOG_CHANNEL_ID", "InternIQ is alive"))
    print("Posted to #log")


def login() -> None:
    """Open Elevate so Ali can log in once; the profile remembers it."""
    from interniq import browser, elevate

    with browser.open_browser(elevate=True) as context:
        page = browser.page_of(context)
        page.goto(elevate.JOBS_URL)
        print("Log in to Elevate in the browser window, then close the window.")
        page.wait_for_event("close", timeout=0)
    print("Login saved")


def crawl() -> None:
    """Save new ENSF postings, check each new employer link once, report to #log."""
    from interniq import browser, db, sites
    from interniq.elevate import crawler

    with browser.open_browser(elevate=True) as context:
        print("Reading Elevate...")
        counts = crawler.crawl(context)
        flagged = sites.check_links(context, db.connect())

    lines = [f"Crawl done: {counts['listed']} ENSF postings listed, {counts['new']} new."]
    lines += [
        f"Link {f['link_status']}: {f['id']} {f['org']}, {f['title']} ({f['note']})"
        for f in flagged
    ]
    message = "\n".join(lines)
    print(message)
    asyncio.run(discord_bot.send("DISCORD_LOG_CHANNEL_ID", message[:2000]))


def daily() -> None:
    """The once-a-day check: a slow crawl, screening of anything new, and one summary in
    #log ("0 new postings today" included)."""
    from datetime import date, datetime, timedelta

    from interniq import browser, db, screener, sites
    from interniq.elevate import crawler

    today = date.today().strftime("%b %d").replace(" 0", " ")
    if db.get_setting(db.connect(), "paused", ""):
        print("paused; skipping")
        asyncio.run(discord_bot.send("DISCORD_LOG_CHANNEL_ID", f"⏸️ Daily check, {today}: paused (type `!resume`)."))
        return
    conn = db.connect()
    started = db.get_setting(conn, "daily_running", "")
    if started and datetime.now() - datetime.fromisoformat(started) < timedelta(hours=1):
        print("another daily check is running; not starting a second one")
        return  # never two Elevate sessions at once
    db.set_setting(conn, "daily_running", datetime.now().isoformat(timespec="seconds"))
    try:
        with browser.open_browser(elevate=True) as context:
            counts = crawler.crawl(context)
            sites.check_links(context, db.connect())
    except crawler.ElevateBlocked as e:
        asyncio.run(discord_bot.send("DISCORD_LOG_CHANNEL_ID", f"📋 Daily check, {today}: stopped, {e}"))
        raise SystemExit(1)
    finally:
        db.set_setting(conn, "daily_running", "")  # the Elevate session is closed
    results = screener.screen_new() if counts["new"] else []
    if results:  # one card per new posting: Ali's Apply tap starts the application
        asyncio.run(discord_bot.send_many("DISCORD_QUESTIONS_CHANNEL_ID",
                                          [discord_bot.review_card(r["posting"], r) for r in results]))
    if not results:
        message = f"📋 Daily check, {today}: **0 new postings today** ({counts['listed']} ENSF postings listed)."
    else:
        lines = [f"📋 Daily check, {today}: **{len(results)} new posting{'s' if len(results) != 1 else ''} today**"]
        for r in results:
            p = r["posting"]
            due = (p.get("deadline") or "")[:10]
            lines.append(f"• `{p['id']}` **{p['org']}**, {p['title'][:70]}" + (f" (due {due})" if due else "")
                         + f": {r['decision']}")
        lines.append("Tap **Apply** or **Skip** on each card in #questions; Apply starts the application.")
        message = "\n".join(lines)
    if counts.get("waiting"):
        message += f"\n{counts['waiting']} more are waiting; they come in tomorrow (max {crawler.MAX_NEW_PER_RUN} a day)."
    print(message)
    asyncio.run(discord_bot.send("DISCORD_LOG_CHANNEL_ID", message[:2000]))


def worker() -> None:
    """Apply to the queued postings (the ones Ali tapped Apply on), one at a time."""
    from interniq.applier import worker as apply_worker

    apply_worker.work()


def queue(*posting_ids: str) -> None:
    """Queue postings for applying, the same as tapping Apply on their cards: queue 153063"""
    from interniq.applier import worker as apply_worker

    if not posting_ids:
        raise SystemExit("Usage: python -m interniq queue <posting id> [more ids]")
    for posting_id in posting_ids:
        print(f"{posting_id}: place {apply_worker.enqueue(int(posting_id))}")
    apply_worker.spawn()


def write(*posting_ids: str) -> None:
    """Build resume + cover letter (+ transcript) for each posting ID."""
    from interniq.writer import package

    if not posting_ids:
        raise SystemExit("Usage: python -m interniq write <posting id> [more ids]")
    for posting_id in posting_ids:
        print(f"Writing {posting_id}...")
        s = package.write(int(posting_id))
        p = s["package"]
        print(f"  {s['org']}: {p['re_line']}")
        print(f"  resume focus: {p['resume_focus']}")
        if not s["cover_letter"]:
            print("  cover letter: not requested by the posting, resume only")
        print(f"  learning gaps: {', '.join(p['learning_gaps']) or 'none'}")
        for note in p["notes_for_ali"]:
            print(f"  note: {note}")
        for problem in s["unresolved_problems"]:
            print(f"  STILL FAILING: {problem}")
        print(f"  files: {s['folder']}")


def inbox() -> None:
    """Import postings Ali pasted into data/inbox/, check their employer links, then screen."""
    from interniq import browser, db, sites
    from interniq.elevate import paste

    results = paste.import_inbox()
    print("\n".join(results) or "data/inbox/ is empty")
    if any("imported" in r for r in results):
        with browser.open_browser() as context:
            sites.check_links(context, db.connect())  # employer sites only, never Elevate
        screen()


def screen() -> None:
    """Decide apply / skip / ask for every new posting."""
    from interniq import screener

    from interniq import db

    print("Screening new postings...")
    results = screener.screen_new()
    counts = {}
    for r in results:
        counts[r["decision"]] = counts.get(r["decision"], 0) + 1

    learning = discord_bot.learning_mode(db.connect())
    cards = [discord_bot.review_card(r["posting"], r) for r in results
             if discord_bot.needs_review(r["decision"], learning)]
    if cards:
        asyncio.run(discord_bot.send_many("DISCORD_QUESTIONS_CHANNEL_ID", cards))
    summary = ("Screening done: " + (", ".join(f"{n} {d}" for d, n in counts.items()) or "nothing new")
               + f". {len(cards)} cards waiting in #questions.")
    print(summary)
    asyncio.run(discord_bot.send("DISCORD_LOG_CHANNEL_ID", summary))


def decide(choice: str = "", *posting_ids: str) -> None:
    """Save Ali's answer for postings without tapping each card: decide skip 152617 152089"""
    from interniq import db

    if choice not in ("apply", "skip", "closed") or not posting_ids:
        raise SystemExit("Usage: python -m interniq decide <apply|skip|closed> <posting id> [more ids]")
    conn = db.connect()
    answers = {}
    for posting_id in map(int, posting_ids):
        if not conn.execute("SELECT 1 FROM decisions WHERE posting_id = ?", (posting_id,)).fetchone():
            print(f"  {posting_id}: not screened yet, skipped")
            continue
        db.set_ali_decision(conn, posting_id, choice)
        answers[posting_id] = choice
    updated = asyncio.run(discord_bot.mark_cards(answers))
    print(f"Saved {len(answers)} answers ({choice}); updated {updated} cards in #questions.")


def propose(*posting_ids: str) -> None:
    """Post approval cards to #approvals, writing any package that doesn't exist yet."""
    from interniq.writer import package

    if not posting_ids:
        raise SystemExit("Usage: python -m interniq propose <posting id> [more ids]")
    ids = [int(p) for p in posting_ids]
    for posting_id in ids:
        if not package.load_summary(posting_id):
            print(f"Writing {posting_id} first...")
            package.write(posting_id)
    asyncio.run(discord_bot.send_many("DISCORD_APPROVALS_CHANNEL_ID", discord_bot.propose(ids)))
    print(f"Posted {len(ids)} approval card(s) to #approvals.")


def dryrun(posting_id: str = "", job_url: str = "") -> None:
    """Fill another job on the same site with this posting's package, stop at Review, abandon."""
    from interniq.applier import run

    if not posting_id or not job_url:
        raise SystemExit("Usage: python -m interniq dryrun <posting id> <job url on the same site>")
    run.run([int(posting_id)], job_url, dry=True)


def apply(*posting_ids: str) -> None:
    """Fill each application in one session, stop at Review with a card, submit only after approval."""
    from interniq.applier import run

    if not posting_ids:
        raise SystemExit("Usage: python -m interniq apply <posting id> [more ids]")
    run.run([int(p) for p in posting_ids])


def intend(posting_id: str = "") -> None:
    """Click "I intend to apply" on Elevate: for one submitted posting, or (no id) every pending one."""
    from interniq import browser
    from interniq.elevate import intent

    from interniq import db

    with browser.open_browser(elevate=True) as context:
        if posting_id:
            print(intent.express_intent(context, int(posting_id)))
        else:  # every submitted application still missing the click, in one visit
            print("\n".join(intent.express_all(context)))
        if db.get_setting(db.connect(), "elevate_blocked", ""):
            # Leave the page up so Ali can see what Elevate showed; close the window to finish.
            print("Elevate looks blocked; the window stays open until you close it.")
            try:
                browser.page_of(context).wait_for_event("close", timeout=30 * 60 * 1000)
            except Exception:
                pass


def email(*posting_ids: str) -> None:
    """Draft email applications and post them as cards; Ali sends them from his Outlook."""
    from interniq.applier import email_apply

    if not posting_ids:
        raise SystemExit("Usage: python -m interniq email <posting id> [more ids]")
    email_apply.run_email([int(p) for p in posting_ids])


def handoff(*posting_ids: str) -> None:
    """Prepare applications Ali submits himself (inside Elevate while it blocks the agent)."""
    from interniq.applier import handoff as handoff_cards

    if not posting_ids:
        raise SystemExit("Usage: python -m interniq handoff <posting id> [more ids]")
    handoff_cards.run_handoff([int(p) for p in posting_ids])


def unblock() -> None:
    """Allow the agent back on Elevate after a security block (only when Ali says so)."""
    from interniq import db

    conn = db.connect()
    print(f"Elevate was blocked at: {db.get_setting(conn, 'elevate_blocked', '') or '(not blocked)'}")
    db.set_setting(conn, "elevate_blocked", "")
    print("Cleared. The next Elevate action will try once; a new block stops everything again.")


def bot() -> None:
    """Keep the Discord bot online to answer commands (Ctrl+C to stop)."""
    discord_bot.run()


COMMANDS = {
    "ping": ping,
    "login": login,
    "crawl": crawl,
    "daily": daily,
    "worker": worker,
    "queue": queue,
    "inbox": inbox,
    "screen": screen,
    "decide": decide,
    "write": write,
    "propose": propose,
    "dryrun": dryrun,
    "apply": apply,
    "intend": intend,
    "email": email,
    "handoff": handoff,
    "unblock": unblock,
    "bot": bot,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="interniq")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("args", nargs="*", help="extra arguments, e.g. posting IDs for write")
    args = parser.parse_args()
    COMMANDS[args.command](*args.args)


if __name__ == "__main__":
    main()
