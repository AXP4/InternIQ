"""Drives a Raise application (Enbridge uses enbridgestudents.raise.jobs).

The form is a Salesforce app in an iframe from apply.raise.jobs, and its fields live in
shadow DOM, so they're found with Playwright locators (which see inside it) instead of
page scripts. Nobody can tell from the page which button sends the application ("Apply
with Resume" may only start it), so every page counts as final: it's filled, the card goes
up, and each press needs its own approval (Approval 2, 3, ...). A sign-in page is handled
here, never by Claude: the UCalgary email with password slots 1 to 3 in order, once each.
"""

import re

from playwright.sync_api import Frame, Page

from interniq import config, db, discord_bot
from interniq.applier.workday import Blocked, best_option, password_slots, wait_out_bot_check

SITE = "raise"
SHOWS_FILES_ON_REVIEW = False
PRESS_TEXT = re.compile(r"^(apply with resume|submit( application)?|apply|next|continue|save (and|&) continue|"
                        r"sign in|log in|login)$", re.I)
SUBMITTED_TEXT = ["thank you for applying", "thank you for your application", "application has been submitted",
                  "application was submitted", "successfully applied", "application received"]
TERMS = re.compile(r"terms of service", re.I)  # Ali approved ticking this one; SMS consent stays unticked
_job_title = {"text": ""}
_fields: dict[str, object] = {}  # id -> element handle, rebuilt on every read


def content(page: Page) -> Frame:
    return next((f for f in page.frames if "apply.raise.jobs" in f.url), page.main_frame)


DESCRIBE_JS = r"""
e => {
  const text = n => (n ? n.innerText.replace(/\s+/g, ' ').trim() : '');
  const type = (e.type || '').toLowerCase();
  let label = (e.labels && e.labels[0] && text(e.labels[0])) || e.getAttribute('aria-label') || '';
  if (!label) {
    let box = e.parentElement;
    for (let i = 0; i < 4 && box && !text(box); i++) box = box.parentElement;
    label = text(box).slice(0, 200);
  }
  const f = {label, required: e.required || e.getAttribute('aria-required') === 'true', type,
             visible: !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)};
  if (e.tagName === 'SELECT') {
    f.kind = 'select'; f.options = [...e.options].map(o => o.text.trim()).filter(Boolean);
    f.value = e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : '';
  } else if (type === 'checkbox' || type === 'radio') { f.kind = type; f.value = e.checked ? 'checked' : 'unchecked'; }
  else if (type === 'file') { f.kind = 'file'; f.value = ''; }
  else if (e.tagName === 'TEXTAREA') { f.kind = 'textarea'; f.value = e.value; }
  else { f.kind = 'text'; f.value = e.value; }
  return f;
}
"""


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    frame = content(page)
    _fields.clear()
    fields = []
    for n, handle in enumerate(frame.locator("input, select, textarea").element_handles(), start=1):
        f = handle.evaluate(DESCRIBE_JS)
        if f["type"] in ("hidden", "submit", "button", "password") or (not f["visible"] and f["kind"] != "file"):
            continue
        if TERMS.search(f["label"]) is None and re.search(r"sms|automated voice", f["label"], re.I):
            continue  # Ali's choice: never consent to SMS or automated calls
        if f["kind"] == "file" and "drag" in f["label"].lower():
            f["label"] = "Upload Resume (the resume goes here)"
        f["id"] = f"r{n}"
        _fields[f["id"]] = handle
        fields.append({k: f[k] for k in ("id", "kind", "label", "required", "value") if k in f}
                      | ({"options": f["options"]} if "options" in f else {}))
    errors = [t for t in frame.locator('[role="alert"], .slds-has-error, .error').all_inner_texts() if t.strip()]
    return {"step": "Application", "fields": fields, "add_buttons": [], "errors": sorted(set(errors))}


def handle(field_id: str):
    if field_id not in _fields:
        raise Blocked(f"Field {field_id} is gone; read the page again")
    return _fields[field_id]


def describe(page: Page, field_id: str) -> dict:
    return handle(field_id).evaluate(DESCRIBE_JS)


def fill_text(page: Page, field_id: str, value: str) -> str:
    box = handle(field_id)
    box.fill(value)
    box.press("Tab")
    got = box.input_value()
    return "ok" if got.strip() == value.strip() else f"value didn't stick (field shows {got!r})"


def select_option(page: Page, field_id: str, value: str) -> str:
    box = handle(field_id)
    names = box.evaluate("e => [...e.options].map(o => o.text.trim())")
    choice = best_option(names, value)
    if not choice:
        return f"no option matches {value!r}; options are: {names[:60]}"
    box.select_option(label=choice)
    return f"ok (selected {choice!r})"


select_search = select_option


def choose_radio(page: Page, field_id: str, value: str) -> str:
    handle(field_id).check(force=True)
    return "ok"


def set_checkbox(page: Page, field_id: str, checked: bool, option: str | None = None) -> str:
    box = handle(field_id)
    label = describe(page, field_id)["label"]
    if checked and re.search(r"sms|automated voice", label, re.I):
        return "not ticked: Ali never consents to SMS or automated calls"
    box.set_checked(checked, force=True)
    return "ok" if box.is_checked() == checked else "checkbox didn't change"


def set_date(page: Page, field_id: str, month: str = "", year: str = "", day: str = "") -> str:
    return "use fill_text in the format the field shows"


def upload(page: Page, field_id: str, path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    handle(field_id).set_input_files(path)
    for _ in range(10):
        page.wait_for_timeout(1500)
        if name.rsplit(".", 1)[0] in content(page).locator("body").inner_text():
            return f"ok ({name} uploaded)"
    return "upload didn't show up on the page; do NOT upload again, call record_unsure instead"


def press_add(page: Page, button_id: str) -> str:
    return "this form has no Add buttons"


# ---------- moving through the application ----------

def open_application(page: Page, job_url: str) -> None:
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    wait_out_bot_check(page, "opening the job page")
    heads = page.locator("h1:visible").all_inner_texts()
    _job_title["text"] = heads[0].strip() if heads else ""
    if "apply.raise.jobs" not in content(page).url:
        raise Blocked("The job page has no Raise application form (closed posting?)")
    page.locator("#apply-form").scroll_into_view_if_needed()


def page_job_title(page: Page) -> str:
    return _job_title["text"]


def ensure_signed_in(page: Page) -> None:
    """Sign-in, if Raise asks, happens after a press (see after_submit_page)."""


def next_page(page: Page) -> str:
    raise Blocked("Every Raise page counts as final; only an approved submit may press a button")


def is_review(page: Page) -> bool:
    return True  # see the module docstring


def files_on_review(page: Page, expected: list[str], timeout_s: int = 10) -> list[str]:
    return []


def press_buttons(page: Page) -> list:
    return [b for b in content(page).locator("button:visible").all() if PRESS_TEXT.match(b.inner_text().strip())]


def submitted(page: Page) -> bool:
    text = (content(page).locator("body").inner_text() + page.locator("body").inner_text()).lower()
    return any(phrase in text for phrase in SUBMITTED_TEXT)


def submit(page: Page, posting_id: int) -> bool:
    """Press this page's button once, only if Ali approved (checked here)."""
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"Application {posting_id} is not approved; refusing to submit")
    buttons = press_buttons(page)
    if not buttons:
        raise Blocked("No button to press on this page")
    print(f"  pressing {buttons[0].inner_text().strip()!r}")
    buttons[0].click()
    page.wait_for_timeout(8000)
    wait_out_bot_check(page, "submitting")
    return submitted(page)


def sign_in_if_asked(page: Page) -> None:
    """A sign-in page: UCalgary email, then password slots 1 to 3 in order, once each."""
    frame = content(page)
    password = frame.locator('input[type="password"]:visible')
    if not password.count():
        return
    email = frame.locator('input[type="email"]:visible, input[name*="user" i]:visible, input[name*="email" i]:visible')
    for slot, secret in password_slots()[:3]:
        if email.count():
            email.first.fill(config.require("APPLY_EMAIL"))
        password.first.fill(secret)
        buttons = press_buttons(page)
        if buttons:
            buttons[0].click()
        page.wait_for_timeout(7000)
        if not content(page).locator('input[type="password"]:visible').count():
            db.set_setting(db.connect(), "password_slot:raise.jobs", str(slot))
            print(f"  signed in to Raise with password slot {slot}")
            return
        print(f"  password slot {slot} didn't work on Raise")
    discord_bot.call(discord_bot.send(
        "DISCORD_QUESTIONS_CHANNEL_ID",
        "🔑 None of the 3 site passwords worked for your Raise account (UCalgary email). I stopped and won't "
        "try again. Reset it on Raise or tell me which one to use."))
    raise Blocked("All 3 password slots failed on Raise")


def after_submit_page(page: Page) -> dict | None:
    """What a press led to: sign in if asked, then the next page's questions go to the next
    approval card; None when there's nothing left to fill or press."""
    sign_in_if_asked(page)
    snapshot = read_page(page)
    if snapshot["errors"] and not snapshot["fields"]:
        raise Blocked(f"Raise refused the page: {'; '.join(snapshot['errors'])[:300]}")
    return snapshot if (snapshot["fields"] or press_buttons(page)) and not submitted(page) else None


def confirmed_elsewhere(page: Page) -> bool:
    return submitted(page)
