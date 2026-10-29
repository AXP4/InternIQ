"""Drives a Greenhouse job board application (Astera Labs uses job-boards.greenhouse.io).

The form sits on the job page itself: one page, no account, Resume/CV upload, contact
details, education, and the employer's questions, then "Submit application". Like
Eightfold, the whole form is the final page and only an approved submit() presses the
button. Dropdowns are react-select comboboxes, so the Eightfold helpers for opening and
picking them are reused. Some boards email a security code after Submit; that page is
filled through Approval 2 (the filler asks Ali for the code).
"""

import re

from playwright.sync_api import Page

from interniq import db
from interniq.applier import eightfold as ef
from interniq.applier.workday import Blocked, best_option, wait_out_bot_check

SITE = "greenhouse"
SHOWS_FILES_ON_REVIEW = True
SUBMIT_TEXT = re.compile(r"^submit application$", re.I)
SUBMITTED_TEXT = ["thank you for applying", "thank you for your application", "application has been submitted",
                  "application was submitted", "we have received your application", "received your application"]
_job_title = {"text": ""}
_uploaded: list[str] = []

# Shared with Eightfold: same field tagging, same combobox handling.
field, describe, fill_text, select_option = ef.field, ef.describe, ef.fill_text, ef.select_option
choose_radio, set_checkbox, set_date, press_add = ef.choose_radio, ef.set_checkbox, ef.set_date, ef.press_add


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    fields = []
    for f in page.evaluate(ef.READ_FIELDS_JS):
        if f["kind"] == "text" and not f["label"].strip():
            continue  # react-select's hidden helper input next to each dropdown
        if f["kind"] == "select" and "options" not in f:
            options = ef.combobox_options(page, f["id"])
            # Long lists (School, Discipline) load as you type: say so instead of listing 60.
            f["options"] = options[:40]
            if len(options) > 40 or not options:
                f["note"] = "searchable: use select_search with the wanted text"
        f.pop("list", None)
        fields.append(f)
    return {"step": "Application", "fields": fields, "add_buttons": [],
            "errors": sorted(set(page.evaluate(ef.ERRORS_JS)))}


def select_search(page: Page, field_id: str, value: str) -> str:
    """Type into a react-select box and pick the best matching result."""
    box = field(page, field_id)
    box.click()
    box.fill(value)
    page.wait_for_timeout(2000)  # results can come from the server (School)
    listbox = box.get_attribute("aria-controls")
    options = page.locator(f'[id="{listbox}"] [role="option"]').all() if listbox else []
    names = [o.inner_text().strip() for o in options]
    choice = best_option(names, value)
    if not choice:
        page.keyboard.press("Escape")
        return f"no result matches {value!r}; results are: {names[:30]}"
    options[names.index(choice)].click()
    page.wait_for_timeout(800)
    shown = describe(page, field_id).get("value", "")
    return f"ok (selected {choice!r})" if choice in shown else f"selection didn't stick (shows {shown!r})"


def upload(page: Page, field_id: str, path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if _uploaded:
        return f"the resume field already holds {_uploaded[0]}; not uploading {name} (it would replace it)"
    box = page.locator('input[type="file"]#resume')
    if not box.count():
        box = page.locator('input[type="file"]').first
    box.set_input_files(path)
    for _ in range(10):
        page.wait_for_timeout(1500)
        if name in page.locator("body").inner_text():
            _uploaded.append(name)
            return f"ok ({name} uploaded)"
    return "upload didn't show up on the page; do NOT upload again, call record_unsure instead"


def open_application(page: Page, job_url: str) -> None:
    """The form is on the job page; the h1 is the job title."""
    _uploaded.clear()
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    wait_out_bot_check(page, "opening the job page")
    heads = page.locator("h1:visible").all_inner_texts()
    _job_title["text"] = heads[0].strip() if heads else ""
    if not is_review(page):
        raise Blocked("The job page has no application form (closed posting?)")


def page_job_title(page: Page) -> str:
    return _job_title["text"]


def ensure_signed_in(page: Page) -> None:
    """No account on Greenhouse job boards."""


def next_page(page: Page) -> str:
    raise Blocked("This application is a single page; only an approved submit may press its button")


def submit_button(page: Page):
    return page.locator("button:visible", has_text=SUBMIT_TEXT)


def is_review(page: Page) -> bool:
    return submit_button(page).count() > 0


def files_on_review(page: Page, expected: list[str], timeout_s: int = 10) -> list[str]:
    text = page.locator("body").inner_text()
    return [name for name in expected if name not in text]


def submitted(page: Page) -> bool:
    text = page.locator("body").inner_text().lower()
    return any(phrase in text for phrase in SUBMITTED_TEXT)


def submit(page: Page, posting_id: int) -> bool:
    """Press "Submit application" once, only if Ali approved (checked here)."""
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"Application {posting_id} is not approved; refusing to submit")
    if not is_review(page):
        raise Blocked("The Submit application button is gone (session may have timed out)")
    submit_button(page).first.click()
    page.wait_for_timeout(8000)
    wait_out_bot_check(page, "submitting")
    return submitted(page)


def after_submit_page(page: Page) -> dict | None:
    """After a press without a confirmation: a security code box (some boards email one)
    goes to Approval 2; form errors stop the run (nothing was sent)."""
    snapshot = read_page(page)
    code_box = [f for f in snapshot["fields"] if re.search(r"code", f["label"], re.I)]
    if code_box:
        return {**snapshot, "fields": code_box}
    if snapshot["errors"] and is_review(page):
        raise Blocked(f"The form refused the submit: {'; '.join(snapshot['errors'])[:300]}")
    return None


def confirmed_elsewhere(page: Page) -> bool:
    return submitted(page)
