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


COMMANDS = {
    "ping": ping,
    "login": login,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="interniq")
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args()
    COMMANDS[args.command]()


if __name__ == "__main__":
    main()
