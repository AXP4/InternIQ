"""Ali's UCalgary Outlook, used in the agent's browser (Outlook on the web, his saved login).

Two jobs only:
- latest_code: an employer site just emailed a one-time code; read it from the newest
  matching message in the inbox list (the preview line holds the code). Only messages that
  arrived in the last few minutes and look like code emails are looked at.
- send: an email application Ali approved with Submit on its card; compose, attach, send,
  then confirm it is in Sent Items.
It never deletes, forwards, or replies to anything, and logs no message text.
"""

import re
from datetime import datetime, timedelta

from playwright.sync_api import BrowserContext, Page

MAIL_URL = "https://outlook.office.com/mail/"
SENT_URL = "https://outlook.office.com/mail/sentitems"
CODE_WORDS = re.compile(r"verification|one[- ]time|pass ?code|security code|confirm your (identity|email)|"
                        r"verify your|your code", re.I)
# "pass code : 752250", "Your code is 73737170", "code: AB12CD"
CODE = re.compile(r"(?:code|passcode)\b\D{0,25}?\b([A-Z0-9]{4,8})\b", re.I)
LIST_TIME = re.compile(r"\b(\d{1,2}):(\d{2}) ?([AP]M)\b")


class OutlookError(RuntimeError):
    """Outlook didn't do what was asked; the caller falls back to Ali."""


def open_mail(context: BrowserContext, url: str = MAIL_URL) -> Page:
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_selector('[role="listbox"] [role="option"], button[aria-label="New mail"]', timeout=45_000)
    except Exception:
        page.close()
        raise OutlookError("Outlook didn't open signed in; Ali needs to log in once in the agent's browser")
    return page


def _arrived(label: str, now: datetime) -> datetime | None:
    """Today's messages show a time ("3:37 AM"); older ones show a day or date."""
    match = LIST_TIME.search(label)
    if not match:
        return None
    hour, minute, half = int(match[1]) % 12, int(match[2]), match[3].upper()
    when = now.replace(hour=hour + (12 if half == "PM" else 0), minute=minute, second=0, microsecond=0)
    return when - timedelta(days=1) if when > now + timedelta(minutes=2) else when


def latest_code(context: BrowserContext, max_age_minutes: int = 10, wait_seconds: int = 120) -> str | None:
    """The code in the newest code email from the last few minutes, waiting for it to arrive."""
    page = open_mail(context)
    try:
        for _ in range(max(1, wait_seconds // 10)):
            now = datetime.fromisoformat(page.evaluate(
                "() => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19); }"))
            for item in page.locator('[role="listbox"] [role="option"]').all()[:8]:
                label = re.sub(r"\s+", " ", item.get_attribute("aria-label") or "")
                when = _arrived(label, now)
                if not when or now - when > timedelta(minutes=max_age_minutes) or not CODE_WORDS.search(label):
                    continue
                found = CODE.search(label.split("[△EXTERNAL]")[-1])
                if not found:  # the preview was cut short: open the message
                    item.click()
                    page.wait_for_timeout(2500)
                    body = page.locator('[aria-label="Message body"]').first
                    found = CODE.search(body.inner_text()) if body.count() else None
                if found and re.search(r"\d", found[1]):
                    return found[1]
            page.wait_for_timeout(10_000)
        return None
    finally:
        page.close()


def send(context: BrowserContext, to: str, subject: str, body: str, files: list) -> None:
    """Send one email with attachments; raises OutlookError unless it shows up in Sent Items."""
    page = open_mail(context)
    try:
        page.get_by_role("button", name="New mail").first.click()
        page.wait_for_selector('div[aria-label="To"]', timeout=20_000)
        to_box = page.locator('div[aria-label="To"]').first
        to_box.click()
        page.keyboard.type(to, delay=30)
        page.wait_for_timeout(1500)
        page.keyboard.press("Enter")  # turns the address into a recipient
        page.wait_for_timeout(1000)
        # One valid recipient pill. Directory contacts show a name; anyone else shows the address.
        pills = to_box.locator("._EType_RECIPIENT_ENTITY")
        shown = pills.first.inner_text().strip() if pills.count() else ""
        if pills.count() != 1 or not to_box.locator("[class*=validPill]").count() or ("@" in shown and shown.lower() != to.lower()):
            raise OutlookError(f"the To field doesn't hold exactly {to} (shows {shown!r})")
        page.locator('input[aria-label="Subject"]').first.fill(subject)
        message = page.locator('div[role="textbox"][aria-label="Message body"]').first
        message.click()
        page.keyboard.press("Control+Home")
        for i, line in enumerate(body.strip().split("\n")):
            if i:
                page.keyboard.press("Enter")
            if line:
                page.keyboard.insert_text(line)
        page.get_by_role("button", name="Attach file").first.click()
        with page.expect_file_chooser() as chooser:
            page.get_by_text("Browse this computer").first.click()
        chooser.value.set_files([str(f) for f in files])
        names = [f.name for f in files]
        for _ in range(60):  # uploads finish when every file name shows without a progress bar
            page.wait_for_timeout(1000)
            text = page.locator("body").inner_text()
            if all(n in text for n in names) and not page.locator('[role="progressbar"]:visible').count():
                break
        else:
            raise OutlookError(f"attachments didn't finish uploading: {names}")
        page.get_by_role("button", name="Send", exact=True).first.click()
        page.wait_for_timeout(6000)
        if page.locator('input[aria-label="Subject"]:visible').count():
            raise OutlookError("the draft is still open after Send (a warning or a missing field?)")
    finally:
        page.close()
    if not sent(context, subject):
        raise OutlookError("Send was pressed but the email isn't in Sent Items")


def sent(context: BrowserContext, subject: str) -> bool:
    page = open_mail(context, SENT_URL)
    try:
        for _ in range(6):
            labels = [item.get_attribute("aria-label") or "" for item in page.locator('[role="listbox"] [role="option"]').all()[:5]]
            if any(subject in re.sub(r"\s+", " ", label) for label in labels):
                return True
            page.wait_for_timeout(5000)
        return False
    finally:
        page.close()
