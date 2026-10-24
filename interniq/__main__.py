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


def bot() -> None:
    """Keep the Discord bot online to answer commands (Ctrl+C to stop)."""
    discord_bot.run()


COMMANDS = {
    "ping": ping,
    "login": login,
    "crawl": crawl,
    "bot": bot,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="interniq")
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args()
    COMMANDS[args.command]()


if __name__ == "__main__":
    main()
