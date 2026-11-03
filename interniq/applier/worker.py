"""The apply queue: postings Ali taps Apply on, worked through one at a time.

Ali's Apply tap on a posting card adds it to the queue and starts a worker in the
background. The worker writes the package (resume and cover letter) and then applies
the way that posting needs: the employer's website, email, inside Elevate, or a
submit-it-yourself card when there's nothing to fill. Every application still ends at an
approval card; nothing is submitted without Ali's tap there. Only one worker runs at a
time (a lock file), since two browsers can't share the agent's profile.
"""

import json
import msvcrt
import os
import subprocess

from interniq import config, db, discord_bot
from interniq.elevate import clean_title

LOCK = config.ROOT / "data" / "worker.lock"
LOG = config.ROOT / "data" / "worker.log"
PYTHON = config.ROOT / ".venv" / "Scripts" / "python.exe"


def queue(conn) -> list[int]:
    return json.loads(db.get_setting(conn, "apply_queue", "") or "[]")


def enqueue(posting_id: int) -> int:
    """Add a posting to the queue; returns its place in line (1 = next)."""
    conn = db.connect()
    waiting = queue(conn)
    if posting_id not in waiting:
        waiting.append(posting_id)
        db.set_setting(conn, "apply_queue", json.dumps(waiting))
    return waiting.index(posting_id) + 1


def pop(conn) -> int | None:
    waiting = queue(conn)
    if not waiting:
        return None
    db.set_setting(conn, "apply_queue", json.dumps(waiting[1:]))
    return waiting[0]


def spawn() -> None:
    """Start a worker in the background (it exits at once if one is already running)."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        subprocess.Popen([str(PYTHON), "-m", "interniq", "worker"], cwd=str(config.ROOT), stdout=log,
                         stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def apply_one(posting_id: int) -> None:
    from interniq.applier import email_apply, handoff, run
    from interniq.writer import package

    conn = db.connect()
    posting = dict(conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone())
    app = db.get_application(conn, posting_id)
    if app and app["state"] == "submitted":
        print(f"{posting_id}: already submitted")
        return
    if not package.load_summary(posting_id):
        print(f"{posting_id}: writing the resume and cover letter")
        package.write(posting_id)
    if posting["procedure"] == "Employer Email" or (posting["email"] and not posting["link"]):
        email_apply.run_email([posting_id])
    elif posting["link"] or posting["procedure"] == "Accumulate Online":
        run.run([posting_id])
    else:
        handoff.run_handoff([posting_id])


def work() -> None:
    """Take postings off the queue until it's empty. One worker at a time."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK, "a+")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("a worker is already running; it will pick up the queue")
        handle.close()
        return
    try:
        conn = db.connect()
        while (posting_id := pop(conn)) is not None:
            row = conn.execute("SELECT org, title FROM postings WHERE id = ?", (posting_id,)).fetchone()
            label = f"{row['org']}, {clean_title(row['title'])}" if row else str(posting_id)
            print(f"== {posting_id} {label}", flush=True)
            try:
                apply_one(posting_id)
            except BaseException as e:  # one bad posting never stops the queue (Blocked is SystemExit-like)
                print(f"{posting_id}: stopped ({e})", flush=True)
                discord_bot.call(discord_bot.send(
                    "DISCORD_LOG_CHANNEL_ID",
                    f"⚠️ Couldn't finish applying to **{label}** (`{posting_id}`): {str(e)[:300]}. "
                    "It's out of the queue; tell me or tap Apply again to retry."))
    finally:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
