"""Command line entry point: python -m interniq <command>"""

import argparse
import asyncio

from interniq import discord_bot


def ping() -> None:
    asyncio.run(discord_bot.send("DISCORD_LOG_CHANNEL_ID", "InternIQ is alive"))
    print("Posted to #log")


COMMANDS = {
    "ping": ping,
}


def main() -> None:
    parser = argparse.ArgumentParser(prog="interniq")
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args()
    COMMANDS[args.command]()


if __name__ == "__main__":
    main()
