"""Drives an Oracle Recruiting Cloud application (Seaspan uses *.oraclecloud.com, CX_1).

Flow: job page -> APPLY NOW -> email page (no account; a hidden "honeypot" field is a bot
trap and is never touched) -> usually a one-time code emailed to Ali (asked in
#questions, never saved) -> form sections with NEXT -> the last one has SUBMIT. NEXT only
moves between sections; the page with SUBMIT is the final page: the card goes up there
and only an approved submit() presses it. Dropdowns are comboboxes, handled with the
Eightfold helpers.
"""

import re

from playwright.sync_api import Page

from interniq import db
from interniq.applier import eightfold as ef
from interniq.applier.workday import Blocked, wait_out_bot_check

SITE = "oracle"
SHOWS_FILES_ON_REVIEW = True
NEXT_TEXT = re.compile(r"^(next|continue|verify|confirm)$", re.I)
SUBMIT_TEXT = re.compile(r"^submit( application)?$", re.I)
SUBMITTED_TEXT = ["thank you for applying", "thank you for your application", "application has been submitted",
                  "we received your application", "your application was submitted", "you applied"]
_job_title = {"text": ""}

field, describe, fill_text, select_option = ef.field, ef.describe, ef.fill_text, ef.select_option
select_search, choose_radio, set_checkbox, set_date = ef.select_search, ef.choose_radio, ef.set_checkbox, ef.set_date
press_add = ef.press_add


def buttons(page: Page) -> list:
    return page.locator("button:visible, a[role='button']:visible, input[type=submit]:visible").all()


def button_text(b) -> str:
    return re.sub(r"\s+", " ", (b.inner_text() or b.get_attribute("value") or "")).strip()


def step_name(page: Page) -> str:
    heads = [h.strip() for h in page.locator("h2:visible, h3:visible, legend:visible").all_inner_texts() if h.strip()]
    return heads[0][:80] if heads else "Application"


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    snapshot = ef.read_page(page)
    snapshot["fields"] = [f for f in snapshot["fields"] if "honeypot" not in f["label"].lower()]
    snapshot["step"] = step_name(page)
    return snapshot


def upload(page: Page, field_id: str, path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    box = field(page, field_id)
    box.set_input_files(path)
    for _ in range(10):
        page.wait_for_timeout(1500)
        if name.rsplit(".", 1)[0] in page.locator("body").inner_text():
            return f"ok ({name} uploaded)"
    return "upload didn't show up on the page; do NOT upload again, call record_unsure instead"


def open_application(page: Page, job_url: str) -> None:
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    wait_out_bot_check(page, "opening the job page")
    heads = page.locator("h1:visible").all_inner_texts()
    _job_title["text"] = heads[0].strip() if heads else page.title().split(" - ")[0]
    cookie = page.locator("button:visible", has_text=re.compile(r"^\s*ok\s*$", re.I))
    if cookie.count():
        cookie.first.click()  # the cookie notice only acknowledges necessary cookies
    apply = page.locator("button:visible, a:visible", has_text=re.compile(r"apply now", re.I))
    if not apply.count():
        raise Blocked("The job page has no APPLY NOW button (closed posting?)")
    apply.first.click()
    page.wait_for_timeout(7000)


def page_job_title(page: Page) -> str:
    return _job_title["text"]


def ensure_signed_in(page: Page) -> None:
    """No account: Oracle identifies Ali by email (and an emailed code)."""


def is_review(page: Page) -> bool:
    return any(SUBMIT_TEXT.match(button_text(b)) for b in buttons(page))


def next_page(page: Page) -> str:
    if is_review(page):
        raise Blocked("Refusing to press anything on the final page; only an approved submit may")
    heading = step_name(page)
    before = [f["label"] for f in page.evaluate(ef.READ_FIELDS_JS)]
    candidates = [b for b in buttons(page) if NEXT_TEXT.match(button_text(b))]
    if not candidates:
        raise Blocked(f"No NEXT button on {heading!r}")
    candidates[-1].click()
    page.wait_for_timeout(6000)
    wait_out_bot_check(page, "moving to the next section")
    after = step_name(page)
    changed = [f["label"] for f in page.evaluate(ef.READ_FIELDS_JS)] != before
    # Sections can share a heading: a page whose questions changed is a new step even then.
    return after + " (next)" if changed and after == heading else after


def files_on_review(page: Page, expected: list[str], timeout_s: int = 10) -> list[str]:
    text = page.locator("body").inner_text()
    return [name for name in expected if name.rsplit(".", 1)[0] not in text]


def submitted(page: Page) -> bool:
    text = page.locator("body").inner_text().lower()
    return any(phrase in text for phrase in SUBMITTED_TEXT)


def submit(page: Page, posting_id: int) -> bool:
    """Press SUBMIT once, only if Ali approved (checked here)."""
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"Application {posting_id} is not approved; refusing to submit")
    final = [b for b in buttons(page) if SUBMIT_TEXT.match(button_text(b))]
    if not final:
        raise Blocked("The SUBMIT button is gone (session may have timed out)")
    final[-1].click()
    page.wait_for_timeout(9000)
    wait_out_bot_check(page, "submitting")
    return submitted(page)


def after_submit_page(page: Page) -> dict | None:
    snapshot = read_page(page)
    if snapshot["errors"] and is_review(page):
        raise Blocked(f"Oracle refused the submit: {'; '.join(snapshot['errors'])[:300]}")
    return snapshot if snapshot["fields"] and not submitted(page) and is_review(page) else None


def confirmed_elsewhere(page: Page) -> bool:
    return submitted(page)
