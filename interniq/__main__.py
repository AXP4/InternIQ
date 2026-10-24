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
        print(f"  term: {p['term_months']} months, {p['term_start']} to {p['term_end']} | focus: {p['resume_focus']}")
        if not s["cover_letter"]:
            print("  cover letter: not requested by the posting, resume only")
        print(f"  learning gaps: {', '.join(p['learning_gaps']) or 'none'}")
        for note in p["notes_for_ali"]:
            print(f"  note: {note}")
        for warning in s["warnings"]:
            print(f"  WARNING: {warning}")
        for problem in s["unresolved_problems"]:
            print(f"  STILL FAILING: {problem}")
        print(f"  files: {s['folder']}")


def bot() -> None:
    """Keep the Discord bot online to answer commands (Ctrl+C to stop)."""
    discord_bot.run()


COMMANDS = {
    "ping": ping,
    "login": login,
    "crawl": crawl,
    "write": write,
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
