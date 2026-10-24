"""Discord bot: posts to Ali's private server.

Phase 1 only sends one-off messages. Approval cards, question cards, and
commands come in later phases.
"""

import discord

from interniq import config


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
