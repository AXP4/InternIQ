"""Clicks "I intend to apply" on Elevate after an application is submitted.

This is the only click InternIQ ever makes on Elevate that changes anything. It runs
only for postings whose application is recorded as submitted, and it confirms the
result by reading the posting's row afterwards ("Expressed Intention to Apply").
"""

import re

from playwright.sync_api import BrowserContext

from interniq import browser, db
from interniq.elevate import crawler

APPLIED = "Expressed Intention to Apply"
INTENT = re.compile(r"I intend to apply", re.IGNORECASE)


def row_status(context: BrowserContext, posting_id: int) -> str | None:
    """The App Status column for a posting in the ENSF list (None if it isn't listed)."""
    page = browser.page_of(context)
    crawler.ensure_logged_in(page)
    browser.pause()
    rows = crawler.open_quick_search(page)
    return next((r["elevate_status"] for r in rows if r["id"] == posting_id), None)


def express_intent(context: BrowserContext, posting_id: int, via: int | None = None) -> str:
    """Click "I intend to apply" for a submitted application. Returns what happened.

    `via` is the posting whose application was submitted when `posting_id` is a duplicate
    Elevate listing of the same employer job.
    """
    conn = db.connect()
    app = db.get_application(conn, via or posting_id)
    if not app or app["state"] != "submitted":
        raise PermissionError(f"{via or posting_id} isn't recorded as submitted; not touching Elevate")

    status = row_status(context, posting_id)
    if status is None:
        return "not in the ENSF list anymore; click it by hand if the posting still exists"
    if status == APPLIED:
        db.set_elevate_status(conn, posting_id, APPLIED)
        return "already showed Expressed Intention to Apply; nothing clicked"

    page = browser.page_of(context)
    link = page.locator(f"tr#posting{posting_id} td[data-totitle] a").first
    with context.expect_page() as new_tab:
        link.click()
    tab = new_tab.value
    try:
        tab.wait_for_load_state()
        tab.wait_for_selector("text=JOB POSTING INFORMATION")
        browser.pause()
        # The page has it twice: a visible button and a copy inside a hidden Actions menu.
        button = tab.get_by_role("button", name=INTENT).or_(tab.get_by_role("link", name=INTENT))
        button.filter(visible=True).first.click()
        tab.wait_for_timeout(4000)
    finally:
        tab.close()

    browser.pause()
    status = row_status(context, posting_id)
    if status == APPLIED:
        db.set_elevate_status(conn, posting_id, APPLIED)
        return "clicked; Elevate now shows Expressed Intention to Apply"
    return f"clicked, but the list shows {status!r}; check it by hand"
