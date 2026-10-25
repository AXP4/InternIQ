"""Discord bot: posts to Ali's private server and answers commands.

Review cards: every screener skip (in learning mode), every "ask", and every dead
link gets a card in #questions with Apply / Skip buttons. The buttons carry the
posting ID in their custom_id, so they keep working after the bot restarts.
Approval cards for submissions come in Phase 5.
"""

import json
import os
import re
from datetime import datetime

import discord

from interniq import config, db


def decision_view(posting_id: int) -> discord.ui.View:
    """Apply / Skip buttons. Must be built inside the running event loop."""
    view = discord.ui.View(timeout=None)
    view.add_item(DecideButton("apply", posting_id))
    view.add_item(DecideButton("skip", posting_id))
    return view


async def send_many(channel_env: str, messages: list[tuple[str, int | None]]) -> None:
    """Log in once and post every (text, posting_id) to the channel named in .env.

    A posting_id adds Apply / Skip buttons for that posting.
    """
    token = config.require("DISCORD_BOT_TOKEN")
    target = config.channel_id(channel_env)
    client = discord.Client(intents=discord.Intents.none())
    error: Exception | None = None

    @client.event
    async def on_ready():
        nonlocal error
        try:
            channel = await client.fetch_channel(target)
            for text, posting_id in messages:
                if posting_id is None:
                    await channel.send(text)
                else:
                    await channel.send(text, view=decision_view(posting_id))
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


async def send(channel_env: str, text: str) -> None:
    await send_many(channel_env, [(text, None)])

# ---------- review cards ----------

class DecideButton(discord.ui.DynamicItem[discord.ui.Button], template=r"decide:(?P<choice>apply|skip):(?P<pid>\d+)"):
    """An Apply or Skip button for one posting. Ali's tap is saved as his decision."""

    def __init__(self, choice: str, posting_id: int):
        super().__init__(discord.ui.Button(
            label="Apply" if choice == "apply" else "Skip",
            emoji="✅" if choice == "apply" else "❌",
            style=discord.ButtonStyle.success if choice == "apply" else discord.ButtonStyle.secondary,
            custom_id=f"decide:{choice}:{posting_id}",
        ))
        self.choice, self.posting_id = choice, posting_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["choice"], int(match["pid"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild_id != config.channel_id("DISCORD_GUILD_ID"):
            return
        db.set_ali_decision(db.connect(), self.posting_id, self.choice)
        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n{answer_label(self.choice)}", view=None)


def answer_label(choice: str) -> str:
    return {"apply": "✅ **You: apply**", "skip": "❌ **You: skip**", "closed": "🚫 **You: closed**"}[choice]


async def mark_cards(answers: dict[int, str]) -> int:
    """Show answers given outside Discord on their open cards and remove the buttons.

    Returns how many cards were updated.
    """
    token = config.require("DISCORD_BOT_TOKEN")
    target = config.channel_id("DISCORD_QUESTIONS_CHANNEL_ID")
    client = discord.Client(intents=discord.Intents.none())
    updated = 0

    @client.event
    async def on_ready():
        nonlocal updated
        try:
            channel = await client.fetch_channel(target)
            async for message in channel.history(limit=500):
                match = re.search(r"`(\d+)`", message.content)
                if message.author == client.user and message.components and match:
                    choice = answers.get(int(match.group(1)))
                    if choice:
                        await message.edit(content=f"{message.content}\n{answer_label(choice)}", view=None)
                        updated += 1
        finally:
            await client.close()

    await client.start(token)
    return updated


HEADLINES = {"skip": "Skip this one?", "ask": "Apply to this one?", "closed": "Link looks dead. Skip?"}


def review_card(posting: dict, decision: dict) -> tuple[str, int]:
    """Card text and the posting ID its Apply / Skip buttons belong to."""
    title = re.sub(r"\s*-\s*(ALL|EN[A-Z]{2}(-AOS)?)(\s+\(?EN[A-Z]{2}(-AOS)?\)?)*\s*$", "", posting["title"]).strip()
    due = datetime.fromisoformat(posting["deadline"]) if posting.get("deadline") else None
    deadline = f"{due:%b} {due.day}" if due else "no deadline"
    flags = json.loads(decision["flags"]) if isinstance(decision.get("flags"), str) else decision.get("flags") or []
    lines = [
        f"**{HEADLINES[decision['decision']]}**  `{posting['id']}`",
        f"**{posting['org']}**, {title}",
        f"{posting.get('location') or ''} · deadline {deadline} · {posting.get('procedure') or ''}",
        f"> Screener said **{decision['decision']}**: {decision.get('reason') or ''}",
    ]
    if flags:
        lines.append(f"Flags: {', '.join(flags)}")
    if posting.get("link"):
        lines.append(f"<{posting['link']}>")
    return "\n".join(lines)[:1900], posting["id"]


def learning_mode(conn) -> bool:
    return db.get_setting(conn, "learning_mode", os.getenv("LEARNING_MODE", "on")) == "on"


def needs_review(decision: str, learning: bool) -> bool:
    return decision in ("ask", "closed") or (decision == "skip" and learning)


# ---------- commands ----------

def format_counts(counts: dict) -> str:
    return ", ".join(f"{name} {n}" for name, n in counts.items()) or "none"


def status_message(*_) -> str:
    conn = db.connect()
    s = db.status_summary(conn)
    if not s["total"]:
        return "No postings yet. Run `python -m interniq crawl`."
    a = db.agreement(conn)
    skips = (f"you agreed with {a['skips_agreed']} of {a['skips_reviewed']} skips "
             f"({100 * a['skips_agreed'] // a['skips_reviewed']}%)" if a["skips_reviewed"] else "no skips reviewed yet")
    return "\n".join([
        "**InternIQ status**",
        f"Postings saved: {s['total']} ({s['listed']} on the latest crawl)",
        f"Deadlines in the next 7 days: {s['due_7_days']}",
        f"Status: {format_counts(s['status'])}",
        f"Screener: {skips}",
        f"Learning mode: {'on' if learning_mode(conn) else 'off'}",
        f"Apply by: {format_counts(s['procedure'])}",
        f"Sites: {format_counts(s['site'])}",
        f"Links: {format_counts(s['link_status'])}",
        f"Last crawl: {s['last_crawl'].replace('T', ' ')}",
    ])


def learning_command(*args) -> str:
    conn = db.connect()
    if args and args[0] in ("on", "off"):
        db.set_setting(conn, "learning_mode", args[0])
    state = "on" if learning_mode(conn) else "off"
    meaning = "every skip comes here for review" if state == "on" else "only asks and dead links come here"
    return f"Learning mode is **{state}**: {meaning}."


COMMANDS = {
    "!status": status_message,
    "!learning": learning_command,
}


def run() -> None:
    """Stay online, answer commands, and handle card buttons, only in Ali's server."""
    guild_id = config.channel_id("DISCORD_GUILD_ID")
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    client.add_dynamic_items(DecideButton)

    @client.event
    async def on_ready():
        print(f"Bot online as {client.user}. Commands: {', '.join(COMMANDS)}")

    @client.event
    async def on_message(message: discord.Message):
        if message.author.bot or not message.guild or message.guild.id != guild_id:
            return
        words = message.content.strip().lower().split()
        command = COMMANDS.get(words[0]) if words else None
        if command:
            await message.channel.send(command(*words[1:]))

    try:
        client.run(config.require("DISCORD_BOT_TOKEN"), log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Turn on Message Content Intent in the Developer Portal (Bot tab), then try again")
