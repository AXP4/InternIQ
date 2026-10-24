"""Discord bot: posts to Ali's private server and answers commands.

Approval cards and question cards come in later phases.
"""

import discord

from interniq import config, db


async def send(channel_env: str, text: str) -> None:
    """Log in, post `text` to the channel named in .env, and log out."""
    token = config.require("DISCORD_BOT_TOKEN")
    target = config.channel_id(channel_env)
    client = discord.Client(intents=discord.Intents.none())
    error: Exception | None = None

    @client.event
    async def on_ready():
        nonlocal error
        try:
            channel = await client.fetch_channel(target)
            await channel.send(text)
        except Exception as e:  # reported after the client closes
            error = e
        finally:
            await client.close()

    try:
        await client.start(token)
    except discord.LoginFailure:
        raise SystemExit("Discord rejected DISCORD_BOT_TOKEN. Reset it in the Developer Portal and update .env")
    if isinstance(error, discord.NotFound):
        raise SystemExit(f"{channel_env} not found. Check the ID and that the bot is in your server")
    if isinstance(error, discord.Forbidden):
        raise SystemExit(f"The bot can't post in {channel_env}. Give it View Channel and Send Messages")
    if error:
        raise error


def format_counts(counts: dict) -> str:
    return ", ".join(f"{name} {n}" for name, n in counts.items()) or "none"


def status_message() -> str:
    s = db.status_summary(db.connect())
    if not s["total"]:
        return "No postings yet. Run `python -m interniq crawl`."
    return "\n".join([
        "**InternIQ status**",
        f"Postings saved: {s['total']} ({s['listed']} on the latest crawl)",
        f"Deadlines in the next 7 days: {s['due_7_days']}",
        f"Status: {format_counts(s['status'])}",
        f"Apply by: {format_counts(s['procedure'])}",
        f"Sites: {format_counts(s['site'])}",
        f"Links: {format_counts(s['link_status'])}",
        f"Last crawl: {s['last_crawl'].replace('T', ' ')}",
    ])


COMMANDS = {
    "!status": status_message,
}


def run() -> None:
    """Stay online and answer commands, only in Ali's server."""
    guild_id = config.channel_id("DISCORD_GUILD_ID")
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Bot online as {client.user}. Commands: {', '.join(COMMANDS)}")

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot or not message.guild or message.guild.id != guild_id:
            return
        command = COMMANDS.get(message.content.strip().lower())
        if command:
            await message.channel.send(command())

    try:
        client.run(config.require("DISCORD_BOT_TOKEN"), log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Turn on Message Content Intent in the Developer Portal (Bot tab), then try again")
