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

    with browser.open_browser() as context:
        page = browser.page_of(context)
        page.goto(elevate.JOBS_URL)
        print("Log in to Elevate in the browser window, then close the window.")
        page.wait_for_event("close", timeout=0)
    print("Login saved")


def crawl() -> None:
    """Save new ENSF postings, check each new employer link once, report to #log."""
    from interniq import browser, db, sites
    from interniq.elevate import crawler

    with browser.open_browser() as context:
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
    run.run(int(posting_id), job_url, dry=True)


def apply(posting_id: str = "") -> None:
    """Fill the application, stop at Review, post the approval card, submit only after approval."""
    from interniq.applier import run

    if not posting_id:
        raise SystemExit("Usage: python -m interniq apply <posting id>")
    run.run(int(posting_id))


def intend(posting_id: str = "") -> None:
    """Click "I intend to apply" on Elevate for an application that's already submitted."""
    from interniq import browser
    from interniq.elevate import intent

    if not posting_id:
        raise SystemExit("Usage: python -m interniq intend <posting id>")
    with browser.open_browser() as context:
        print(intent.express_intent(context, int(posting_id)))


def bot() -> None:
    """Keep the Discord bot online to answer commands (Ctrl+C to stop)."""
    discord_bot.run()


COMMANDS = {
    "ping": ping,
    "login": login,
    "crawl": crawl,
    "screen": screen,
    "decide": decide,
    "write": write,
    "propose": propose,
    "dryrun": dryrun,
    "apply": apply,
    "intend": intend,
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
