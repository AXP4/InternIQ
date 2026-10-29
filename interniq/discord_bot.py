"""Discord bot: posts to Ali's private server and answers commands.

Review cards (#questions): every screener skip (in learning mode), every "ask", and
every dead link, with Apply / Skip buttons.
Approval cards (#approvals): a finished package with its PDFs attached, with Submit /
Fix / Skip buttons. Nothing is submitted without Ali's Submit tap.
Buttons carry the posting ID in their custom_id, so they keep working after the bot
restarts.
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import discord

from interniq import config, db
from interniq.elevate import clean_title
from interniq.writer import package


@dataclass
class Card:
    """One Discord message: text, optional buttons ("decide" or "approve"), attached files."""
    text: str
    buttons: str | None = None
    posting_id: int | None = None
    files: list[Path] = field(default_factory=list)


def view_for(card: Card) -> discord.ui.View | None:
    """The card's buttons. Views must be built inside the running event loop."""
    if not card.buttons:
        return None
    view = discord.ui.View(timeout=None)
    if card.buttons == "decide":
        view.add_item(DecideButton("apply", card.posting_id))
        view.add_item(DecideButton("skip", card.posting_id))
    else:
        for action in ("submit", "fix", "skip"):
            view.add_item(ApprovalButton(action, card.posting_id))
    return view


async def post(channel: discord.abc.Messageable, card: Card) -> discord.Message:
    kwargs = {"files": [discord.File(p) for p in card.files]}
    view = view_for(card)
    if view:
        kwargs["view"] = view
    return await channel.send(card.text, **kwargs)


async def send_many(channel_env: str, cards: list[Card]) -> None:
    """Log in once and post every card to the channel named in .env."""
    token = config.require("DISCORD_BOT_TOKEN")
    target = config.channel_id(channel_env)
    client = discord.Client(intents=discord.Intents.none())
    error: Exception | None = None

    @client.event
    async def on_ready():
        nonlocal error
        try:
            channel = await client.fetch_channel(target)
            for card in cards:
                await post(channel, card)
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
    await send_many(channel_env, [Card(text)])


def call(coro):
    """Run a Discord coroutine from blocking code (the applier and crawler).

    It runs in its own thread with its own event loop, so it never collides with the
    event loop Playwright's sync API uses in the main thread.
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def ask(question: str, timeout_minutes: int = 60) -> str | None:
    """Post a question in #questions and wait for Ali's answer.

    His answer is a reply to the question, or the next message he sends in the channel.
    Returns None if nobody answers in time.
    """
    token = config.require("DISCORD_BOT_TOKEN")
    target = config.channel_id("DISCORD_QUESTIONS_CHANNEL_ID")
    client = discord.Client(intents=discord.Intents.none())
    answer: str | None = None

    @client.event
    async def on_ready():
        nonlocal answer
        try:
            channel = await client.fetch_channel(target)
            asked = await channel.send(f"❓ {question}\n*Reply to this message with your answer.*")
            for _ in range(timeout_minutes * 6):
                await asyncio.sleep(10)
                async for message in channel.history(after=asked, oldest_first=True, limit=20):
                    if message.author.bot:
                        continue
                    replying = message.reference and message.reference.message_id == asked.id
                    if replying or not message.reference:
                        answer = message.content.strip()
                        await message.add_reaction("✅")
                        return
        finally:
            await client.close()

    await client.start(token)
    return answer

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


def review_card(posting: dict, decision: dict) -> Card:
    """A #questions card with Apply / Skip buttons for one screened posting."""
    title = clean_title(posting["title"])
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
    return Card("\n".join(lines)[:1900], "decide", posting["id"])


def learning_mode(conn) -> bool:
    return db.get_setting(conn, "learning_mode", os.getenv("LEARNING_MODE", "on")) == "on"


def needs_review(decision: str, learning: bool) -> bool:
    return decision in ("ask", "closed") or (decision == "skip" and learning)


# ---------- approval cards ----------

def legal_question(reason: str) -> str:
    """The part of a screening reason that's a legal question ("... Legal: confirm X.")."""
    return reason.split("Legal:", 1)[1].strip() if "Legal:" in reason else reason


def approval_card(posting_id: int) -> Card:
    """A #approvals card for a written package, with its PDFs attached."""
    conn = db.connect()
    posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
    decision = conn.execute("SELECT * FROM decisions WHERE posting_id = ?", (posting_id,)).fetchone()
    summary = package.load_summary(posting_id)
    flags = json.loads(decision["flags"] or "[]") if decision else []
    due = datetime.fromisoformat(posting["deadline"]) if posting.get("deadline") else None

    head = [
        f"**Ready to submit?**  `{posting_id}`",
        f"**{posting['org']}**, {clean_title(posting['title'])}",
        " · ".join(x for x in [posting.get("location"), f"deadline {due:%b} {due.day}" if due else None,
                               posting.get("procedure"), posting.get("site")] if x),
    ]
    if decision and decision["form_term"]:
        head.append(f"Term if the form asks: {decision['form_term']}")
    if "legal" in flags:
        head.append(f"⚖️ **Legal, you must confirm:** {legal_question(decision['reason'])}")
    other = [f for f in flags if f != "legal"]
    if other:
        head.append(f"Flags: {', '.join(other)}")
    for problem in summary.get("unresolved_problems", []):
        head.append(f"⚠️ Check: {problem}")
    app = db.get_application(conn, posting_id)
    run_notes = json.loads(app["notes"]) if app and app["notes"] else {}
    if run_notes.get("page_title"):
        # The title read from the application page itself, which the run checked against the posting.
        head[1] = f"**{posting['org']}**, {run_notes['page_title']}"
    if run_notes.get("files_missing"):
        head.append(f"🚫 **Missing on the Review page:** {', '.join(run_notes['files_missing'])}. Don't submit "
                    "until this is fixed.")
    elif run_notes.get("files_expected"):
        head.append(f"📎 Uploaded: {', '.join(run_notes['files_expected'])}")
    if run_notes.get("files_no_slot"):
        head.append(f"📎 The form has no place for: {', '.join(run_notes['files_no_slot'])}")
    for item in run_notes.get("legal", []):
        head.append(f"⚖️ **Confirm:** {item['question']} → **{item['answer']}**")
    for item in run_notes.get("unsure", []):
        head.append(f"❔ {item}")
    if run_notes.get("screenshot"):
        head.append("📸 Screenshots of every filled step are attached. Nothing is sent until you tap Submit; "
                    "the browser stays open on the last step until you decide.")
    if summary.get("feedback"):
        head.append(f"Your fixes so far: {' | '.join(summary['feedback'])}")
    folder = package.OUTPUT_DIR / str(posting_id)
    files = [folder / name for name in summary["files"]] if "files" in summary else sorted(folder.glob("*.pdf"))
    # Every filled step (sites without a review page send from the last step), else the
    # single Review screenshot. Discord allows 10 files per message.
    shots = [folder / name for name in run_notes.get("step_shots", []) if (folder / name).exists()]
    if not shots and run_notes.get("screenshot") and (folder / run_notes["screenshot"]).exists():
        shots = [folder / run_notes["screenshot"]]
    report = folder / run_notes["answers_report"] if run_notes.get("answers_report") else None
    if report and report.exists():
        head.append(f"📝 **{report.name}** lists every question and its final value. Open it first.")
        files = [report] + files
    files = (files + shots)[:10]
    tail = [f"Attached: {', '.join(p.name for p in files)}"]
    if posting.get("link"):
        tail.append(f"<{posting['link']}>")

    # Notes are the first thing to trim so the card fits Discord's 2000 character limit.
    notes = [f"• {n}" for n in summary["package"].get("notes_for_ali", [])]
    while notes and len("\n".join(head + ["Notes:"] + notes + tail)) > 1900:
        notes.pop()
    body = head + (["Notes:"] + notes if notes else []) + tail
    return Card("\n".join(body)[:1990], "approve", posting_id, files)


APPROVAL_LABELS = {
    "submit": ("Submit", "✅", discord.ButtonStyle.success),
    "fix": ("Fix", "✏️", discord.ButtonStyle.primary),
    "skip": ("Skip", "❌", discord.ButtonStyle.secondary),
}


class ApprovalButton(discord.ui.DynamicItem[discord.ui.Button],
                     template=r"approval:(?P<action>submit|fix|skip):(?P<pid>\d+)"):
    """Submit, Fix, or Skip for one finished package."""

    def __init__(self, action: str, posting_id: int):
        label, emoji, style = APPROVAL_LABELS[action]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style,
                                           custom_id=f"approval:{action}:{posting_id}"))
        self.action, self.posting_id = action, posting_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["pid"]))

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild_id != config.channel_id("DISCORD_GUILD_ID"):
            return
        if self.action == "fix":
            await interaction.response.send_modal(FixModal(self.posting_id))
            return
        conn = db.connect()
        app = db.get_application(conn, self.posting_id)
        if self.action == "submit" and app and app["site"] == "email" and app["state"] != "submitted":
            # Email applications are sent by Ali himself; Submit means "I sent it".
            db.mark_submitted(conn, self.posting_id)
            result = ("📨 **Recorded as sent.** Elevate \"I intend to apply\" is queued: "
                      "`python -m interniq intend` (after `unblock` while Elevate is blocked).")
        elif self.action == "submit":
            db.set_application_state(conn, self.posting_id, "approved")
            result = "✅ **Approved.** It will be submitted when the applier runs."
        else:
            db.set_application_state(conn, self.posting_id, "skipped")
            result = "❌ **Skipped.** Saved as a skip for the screener too."
        await interaction.response.edit_message(content=f"{interaction.message.content}\n{result}", view=None)


class FixModal(discord.ui.Modal, title="What should change?"):
    change = discord.ui.TextInput(
        label="Your change",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Open with the CourseIQ testing story instead of Aislez.",
        max_length=1000,
    )

    def __init__(self, posting_id: int):
        super().__init__()
        self.posting_id = posting_id

    async def on_submit(self, interaction: discord.Interaction):
        text = str(self.change.value).strip()
        db.add_fix_request(db.connect(), self.posting_id, text)
        await interaction.response.edit_message(
            content=f"{interaction.message.content}\n✏️ **Fix requested:** {text}\nRewriting, a new card follows.",
            view=None,
        )
        try:
            # The writer makes blocking API calls and renders PDFs; keep the bot responsive.
            await asyncio.to_thread(package.write, self.posting_id, text)
            await post(interaction.channel, propose([self.posting_id])[0])
        except Exception as e:  # tell Ali instead of failing silently
            await interaction.followup.send(f"Fix for `{self.posting_id}` failed: {e}")


def propose(posting_ids: list[int]) -> list[Card]:
    """Approval cards for packages already written, recorded as pending."""
    conn = db.connect()
    cards = []
    for posting_id in posting_ids:
        card = approval_card(posting_id)
        site = conn.execute("SELECT site FROM postings WHERE id = ?", (posting_id,)).fetchone()["site"]
        db.propose_application(conn, posting_id, [p.name for p in card.files], site)
        cards.append(card)
    return cards


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
    client.add_dynamic_items(DecideButton, ApprovalButton)

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
