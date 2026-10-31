"""Reads ENSF postings from Elevate into the database.

Read only. It clicks login links, the ENSF Jobs quick search, and job titles.
It never clicks Apply, Shortlist, Not Interested, or "I intend to apply".
"""

import re
from datetime import datetime

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from interniq import browser, config, db, discord_bot
from interniq.elevate import JOBS_URL

QUICK_SEARCH = "ENSF Jobs"
LOGIN_WAIT_MS = 15 * 60 * 1000  # how long to wait for Ali to finish a manual login
FORBIDDEN_CLICKS = {"apply", "applied", "shortlist", "unshortlist", "not interested", "i intend to apply"}

# One dict per results row. Columns are found relative to the ID cell, because
# the header row has fewer cells than the body rows.
LIST_JS = """
() => [...document.querySelectorAll('tr.searchResult')].map(tr => {
    const id = tr.id.replace('posting', '');
    const tds = [...tr.children];
    const text = tds.map(td => td.innerText.replace(/\\s+/g, ' ').trim());
    const i = text.indexOf(id);
    return {
        id: id,
        elevate_status: text[i - 2],
        term: text[i - 1],
        title: tds[i + 1].dataset.totitle || text[i + 1],
        org: text[i + 2],
        division: text[i + 3],
        location: text[i + 6],
        deadline: text[i + 7],
    };
})
"""

# Every "Label: value" row on a posting page (label cell is 25% wide with a bold label).
FIELDS_JS = """
() => {
    const out = {};
    for (const tr of document.querySelectorAll('tr')) {
        const tds = [...tr.children];
        if (tds.length !== 2 || tds[0].tagName !== 'TD') continue;
        const label = tds[0].querySelector('strong');
        if (!label || !(tds[0].getAttribute('style') || '').includes('25%')) continue;
        const key = label.innerText.trim().replace(/:$/, '');
        if (!(key in out)) out[key] = tds[1].innerText.trim();
    }
    return out;
}
"""


def parse_deadline(text: str | None) -> str | None:
    """'November 11, 2026 11:59 PM' or 'Nov 6, 2026 11:59 PM' -> '2026-11-11T23:59'."""
    if not text:
        return None
    text = " ".join(text.split())
    for fmt in ("%B %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).isoformat(timespec="minutes")
        except ValueError:
            pass
    return None


def ensure_logged_in(page: Page) -> None:
    """Open the jobs page, logging in through UCalgary SSO if needed.

    Microsoft usually remembers Ali, so one click is enough. If it asks for a
    password or 2FA, ping Ali on Discord and wait for him in the open window.
    """
    if db.get_setting(db.connect(), "elevate_blocked", ""):
        raise ElevateBlocked("Elevate blocked the browser earlier; run `python -m interniq unblock` once Ali "
                             "says it's OK to use Elevate again")
    page.goto(JOBS_URL)
    stop_if_blocked(page)
    if "/myAccount/" in page.url:
        return
    page.get_by_role("link", name="Student Login").click()
    try:
        page.wait_for_url("**/myAccount/**", timeout=30_000)
    except PlaywrightTimeout:
        stop_if_blocked(page)
        discord_bot.call(discord_bot.send(
            "DISCORD_QUESTIONS_CHANNEL_ID", "Log in to Elevate in the InternIQ browser window"
        ))
        page.wait_for_url("**/myAccount/**", timeout=LOGIN_WAIT_MS)
    browser.pause()
    page.goto(JOBS_URL)
    stop_if_blocked(page)


BLOCKED_TEXT = ["you have been blocked", "attention required", "access denied", "too many requests"]


class ElevateBlocked(RuntimeError):
    """A security block on Elevate. Never retried or worked around: everything stops and
    Ali decides when (and whether) the agent touches Elevate again."""


def stop_if_blocked(page: Page) -> None:
    text = page.locator("body").inner_text().lower()[:3000]
    if any(phrase in text for phrase in BLOCKED_TEXT) or "attention required" in page.title().lower():
        db.set_setting(db.connect(), "elevate_blocked", page.url[:200])
        shot = config.ROOT / "data" / "elevate_block.png"
        page.screenshot(path=str(shot))
        found = next((p for p in BLOCKED_TEXT if p in text), "attention required")
        discord_bot.call(discord_bot.send_many("DISCORD_QUESTIONS_CHANNEL_ID", [discord_bot.Card(
            f"⛔ Elevate looks blocked (the page says \"{found}\"). I stopped everything on Elevate and won't "
            "retry; the window stays open so you can see it. Nothing else runs on Elevate until "
            "`python -m interniq unblock`.", files=[shot])]))
        raise ElevateBlocked(f"Elevate blocked the browser (page says {found!r}); stopped (no retries)")


def open_quick_search(page: Page) -> list[dict]:
    """Click the ENSF Jobs quick search and return its rows, nearest deadline first."""
    page.get_by_role("link", name=QUICK_SEARCH, exact=True).click()
    page.wait_for_selector("tr.searchResult")
    rows = page.evaluate(LIST_JS)
    for row in rows:
        row["id"] = int(row["id"])
        row["deadline"] = parse_deadline(row["deadline"])
    return sorted(rows, key=lambda r: r["deadline"] or "9999")


def read_posting(context: BrowserContext, page: Page, posting_id: int) -> dict:
    """Open a posting from the results list in a new tab and read its fields."""
    link = page.locator(f"tr#posting{posting_id} td[data-totitle] a").first
    if link.inner_text().strip().lower() in FORBIDDEN_CLICKS:
        raise RuntimeError(f"Refusing to click '{link.inner_text().strip()}' on posting {posting_id}")
    with context.expect_page() as new_tab:
        link.click()
    tab = new_tab.value
    try:
        tab.wait_for_load_state()
        tab.wait_for_selector("text=JOB POSTING INFORMATION")
        fields = tab.evaluate(FIELDS_JS)
        body = tab.locator("body").inner_text()
    finally:
        tab.close()

    # Keep only the posting itself, not the site menu and footer.
    start = body.find(f"{posting_id} - ")
    end = body.rfind("© ")
    raw_text = body[start if start >= 0 else 0 : end if end > start else None].strip()
    return {"fields": fields, "raw_text": raw_text}


def field(fields: dict, *prefixes: str) -> str | None:
    """The first field whose label starts with one of the prefixes."""
    for prefix in prefixes:
        for label, value in fields.items():
            if label.lower().startswith(prefix.lower()) and value:
                return value
    return None


def to_posting(row: dict, details: dict) -> dict:
    f = details["fields"]
    link = field(f, "If by Website")
    email = field(f, "If by Email", "If by E-mail")
    if link and "@" in link and "://" not in link:
        # An employer typed an email address into the website field.
        email, link = email or link, None
    elif link and "://" not in link:
        link = "https://" + link
    if not link and not email and field(f, "Application Procedure") == "Employer Website":
        # Some employers only put the link in the description or additional info.
        urls = re.findall(r"https?://\S+", f"{field(f, 'Additional Application') or ''} {field(f, 'Job Description') or ''}")
        link = urls[0].rstrip(".,)") if urls else None
    materials = field(f, "Application Material")
    return {
        **row,
        "title": field(f, "Job Title") or row["title"],
        "location": field(f, "Job Location") or row["location"],
        "term_start": field(f, "Work Term Start"),
        "duration": field(f, "Work Term Duration"),
        "deadline": parse_deadline(field(f, "Application Deadline")) or row["deadline"],
        "procedure": field(f, "Application Procedure"),
        "link": link,
        "email": email,
        "materials": [m.strip() for m in materials.splitlines() if m.strip()] if materials else [],
        "additional_info": field(f, "Additional Application"),
        "description": field(f, "Job Description"),
        "qualifications": field(f, "Job Qualifications"),
        "fields": f,
        "raw_text": details["raw_text"],
    }


def crawl(context: BrowserContext) -> dict:
    """Save every new ENSF posting. Returns counts for the summary message."""
    conn = db.connect()
    page = browser.page_of(context)
    ensure_logged_in(page)
    browser.pause()
    rows = open_quick_search(page)
    known = db.known_ids(conn)

    new = 0
    for row in rows:
        if row["id"] in known:
            db.touch(conn, row["id"], row["elevate_status"])
            continue
        browser.pause()
        posting = to_posting(row, read_posting(context, page, row["id"]))
        db.save_posting(conn, posting)
        new += 1
        print(f"  saved {row['id']}  {posting['org']}: {posting['title'][:60]}")
    return {"listed": len(rows), "new": new}
