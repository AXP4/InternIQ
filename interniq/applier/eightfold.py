"""Drives an Eightfold application (Lockheed Martin uses lockheedmartin.eightfold.ai).

One page, no account: Resume/CV upload (parsed to pre-fill contact details), Contact
Information, Application Questions, a typed legal-name signature (that signature IS the
statement of agreement; there's no checkbox), and "Submit application". So the whole form is the final page: it's filled once, the card goes up,
and only an approved submit() presses the button. Dropdowns are comboboxes whose options
only exist while they're open, so read_page opens each one to list its options. A
welcome popup covers the page on first visit and is closed first.
"""

import re

from playwright.sync_api import Page

from interniq import db
from interniq.applier.workday import Blocked, best_option, wait_out_bot_check

SITE = "eightfold"
SHOWS_FILES_ON_REVIEW = True  # the uploaded resume's name shows in the Resume/CV section
SUBMIT_TEXT = re.compile(r"^submit application$", re.I)
SUBMITTED_TEXT = ["thank you for applying", "thank you for your application","application has been submitted", "application was submitted",
                  "successfully submitted", "we have received your application", "thanks for applying"]
_job_title = {"text": ""}
_uploaded: list[str] = []  # the one resume this application holds (reset per application)

READ_FIELDS_JS = r"""
() => {
  window.__iiq = window.__iiq || 0;
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const labelOf = el => {
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(' ').map(id => text(document.getElementById(id))).join(' ').trim(); if (t) return t; }
    const l = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return text(l) || el.getAttribute('aria-label') || el.placeholder || el.name || '';
  };
  const out = [], radios = new Set();
  const nodes = document.querySelectorAll('input, select, textarea, [role="checkbox"]:not(input)');
  for (const el of nodes) {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (type !== 'file' && !vis(el)) continue;
    if (el.closest('[role="dialog"]')) continue;
    if (type === 'radio') { if (radios.has(el.name)) continue; radios.add(el.name); }
    if (!el.dataset.iiq) el.dataset.iiq = 'f' + (++window.__iiq);
    const label = labelOf(el);
    const f = {id: el.dataset.iiq, label, required: el.required || el.getAttribute('aria-required') === 'true'};
    if (type === 'file') {
      f.kind = 'file';
      let box = el.parentElement;
      for (let i = 0; i < 6 && box && !/resume|cv/i.test(text(box)); i++) box = box.parentElement;
      f.label = 'Resume/CV';
      f.value = text(box).slice(0, 200);
    } else if (el.getAttribute('role') === 'combobox') {
      // Eightfold keeps the choice in the input; react-select (Greenhouse) clears the input
      // and shows the choice in a separate "single value" element.
      const ctl = el.closest('[class*="control"]');
      const shown = ctl && ctl.querySelector('[class*="single-value"], [class*="singleValue"]');
      f.kind = 'select'; f.value = el.value || text(shown); f.list = el.getAttribute('aria-controls');
    } else if (el.tagName === 'SELECT') {
      f.kind = 'select'; f.value = el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : '';
      f.options = [...el.options].map(o => o.text.trim()).filter(Boolean);
    } else if (type === 'radio') {
      const group = [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)];
      const fs = el.closest('fieldset'); f.kind = 'radio';
      f.label = text(fs && fs.querySelector('legend')) || label;
      f.options = group.map(labelOf); const on = group.find(r => r.checked); f.value = on ? labelOf(on) : '';
    } else if (type === 'checkbox' || el.getAttribute('role') === 'checkbox') {
      f.kind = 'checkbox';
      const on = type === 'checkbox' ? el.checked : el.getAttribute('aria-checked') === 'true';
      f.value = on ? 'checked' : 'unchecked';
      if (!label || /^(input|checkbox)/i.test(label)) {
        let box = el.parentElement;
        for (let i = 0; i < 4 && box && text(box).length < 15; i++) box = box.parentElement;
        f.label = text(box).slice(0, 300);
      }
    } else if (el.tagName === 'TEXTAREA') { f.kind = 'textarea'; f.value = el.value; }
    else { f.kind = 'text'; f.value = el.value; }
    out.push(f);
  }
  return out;
}
"""

ERRORS_JS = r"""
() => [...document.querySelectorAll('[role="alert"], [class*="error" i]:not(input), [aria-invalid="true"]')]
  .filter(e => e.offsetWidth && e.innerText).map(e => e.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean)
"""


def close_popups(page: Page) -> None:
    close = page.locator('#welcomeModal button, [role="dialog"] button[aria-label*="lose" i]').filter(visible=True)
    if close.count():
        close.first.click()
        page.wait_for_timeout(1000)


def field(page: Page, field_id: str):
    return page.locator(f'[data-iiq="{field_id}"]')


def combobox_options(page: Page, field_id: str) -> list[str]:
    """Open a combobox, read its options, close it again."""
    box = field(page, field_id)
    box.click()
    page.wait_for_timeout(600)
    listbox = box.get_attribute("aria-controls")
    names = page.locator(f'[id="{listbox}"] [role="option"]').all_inner_texts() if listbox else []
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    return [n.strip() for n in names if n.strip()]


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    close_popups(page)
    fields = page.evaluate(READ_FIELDS_JS)
    for f in fields:
        if f["kind"] == "select" and "options" not in f:
            f["options"] = combobox_options(page, f["id"])[:60]
        f.pop("list", None)
    return {"step": "Application", "fields": fields, "add_buttons": [],
            "errors": sorted(set(page.evaluate(ERRORS_JS)))}


def describe(page: Page, field_id: str) -> dict:
    return next((f for f in page.evaluate(READ_FIELDS_JS) if f["id"] == field_id), {})


# ---------- actions (each one verifies the value stuck) ----------

def fill_text(page: Page, field_id: str, value: str) -> str:
    box = field(page, field_id)
    box.fill(value)
    box.press("Tab")
    got = box.input_value()
    return "ok" if got.strip() == value.strip() else f"value didn't stick (field shows {got!r})"


def select_option(page: Page, field_id: str, value: str) -> str:
    box = field(page, field_id)
    if box.get_attribute("role") != "combobox":
        names = box.evaluate("e => [...e.options].map(o => o.text.trim())")
        choice = best_option(names, value)
        if not choice:
            return f"no option matches {value!r}; options are: {names[:60]}"
        box.select_option(label=choice)
        return f"ok (selected {choice!r})"
    box.click()
    page.wait_for_timeout(600)
    listbox = box.get_attribute("aria-controls")
    options = page.locator(f'[id="{listbox}"] [role="option"]').all()
    names = [o.inner_text().strip() for o in options]
    choice = best_option(names, value)
    if not choice:
        page.keyboard.press("Escape")
        return f"no option matches {value!r}; options are: {names[:60]}"
    options[names.index(choice)].click()
    page.wait_for_timeout(800)
    got = describe(page, field_id).get("value", "")
    return f"ok (selected {choice!r})" if got.strip() == choice else f"selection didn't stick (shows {got!r})"


def select_search(page: Page, field_id: str, value: str) -> str:
    """Comboboxes with long lists filter as you type."""
    box = field(page, field_id)
    box.click()
    box.fill(value)
    page.wait_for_timeout(1200)
    return select_option(page, field_id, value)


def choose_radio(page: Page, field_id: str, value: str) -> str:
    name = field(page, field_id).get_attribute("name")
    group = page.locator(f'input[type="radio"][name="{name}"]').all()
    labels = [page.locator(f'label[for="{r.get_attribute("id")}"]').first.inner_text().strip()
              if r.get_attribute("id") else (r.get_attribute("value") or "") for r in group]
    choice = best_option(labels, value)
    if not choice:
        return f"no choice matches {value!r}; choices are: {labels}"
    group[labels.index(choice)].check(force=True)
    return "ok"


def set_checkbox(page: Page, field_id: str, checked: bool, option: str | None = None) -> str:
    box = field(page, field_id)
    if box.get_attribute("role") == "checkbox":
        if (box.get_attribute("aria-checked") == "true") != checked:
            box.click()
            page.wait_for_timeout(500)
        return "ok" if (box.get_attribute("aria-checked") == "true") == checked else "checkbox didn't change"
    box.set_checked(checked, force=True)
    return "ok" if box.is_checked() == checked else "checkbox didn't change"


def set_date(page: Page, field_id: str, month: str = "", year: str = "", day: str = "") -> str:
    return "this form's dates are text boxes: use fill_text in the format the field shows"


def upload(page: Page, field_id: str, path: str) -> str:
    """Upload the resume; Eightfold parses it and pre-fills contact fields."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if _uploaded:
        return f"the resume field already holds {_uploaded[0]}; not uploading {name} (it would replace it)"
    # The page rebuilds its file box as it re-renders, so find it fresh instead of by id.
    box = page.locator('input[type="file"]').first
    box.set_input_files(path)
    agreed = accept_privacy_notice(page)
    stem = name.rsplit(".", 1)[0]
    for _ in range(15):  # parsing takes a few seconds
        page.wait_for_timeout(2000)
        if stem in page.locator("body").inner_text():
            _uploaded.append(name)
            note = (" Lockheed's Privacy Notice and Security Agreement popup appeared and I Agree was clicked "
                    "(Ali pre-approved it): call record_legal for it so it shows on the card.") if agreed else ""
            return (f"ok ({name} uploaded). The resume was parsed, which can re-fill contact fields: "
                    f"call read_page and check them.{note}")
    return "upload didn't show up on the page; do NOT upload again, call record_unsure instead"


PRIVACY_TITLE = re.compile(r"privacy notice", re.I)


def accept_privacy_notice(page: Page) -> bool:
    """Lockheed only takes the resume after its Privacy Notice and Security Agreement
    popup is accepted. Ali approved clicking I Agree on that popup (and only that one);
    it's listed on the approval card as a legal item."""
    for _ in range(10):
        page.wait_for_timeout(1000)
        dialog = page.locator('[role="dialog"]').filter(visible=True).filter(has_text=PRIVACY_TITLE)
        if dialog.count():
            dialog.first.get_by_role("button", name=re.compile(r"^\s*i agree\s*$", re.I)).click()
            page.wait_for_timeout(1500)
            return True
    return False


def press_add(page: Page, button_id: str) -> str:
    return "this form has no Add buttons"


# ---------- moving through the application ----------

def open_application(page: Page, job_url: str) -> None:
    _uploaded.clear()
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    wait_out_bot_check(page, "opening the job page")
    close_popups(page)
    # "System Engineering Intern | Lockheed Martin" (the page's h1 is just "Single Position")
    _job_title["text"] = page.title().split(" | ")[0].strip()
    apply = page.locator('a[href*="/careers/apply"]', has_text=re.compile(r"apply", re.I))
    if not apply.count():
        raise Blocked("The job page has no Apply Now link (closed posting?)")
    page.goto(page.url.split("/careers")[0] + apply.first.get_attribute("href"), wait_until="domcontentloaded")
    page.wait_for_timeout(7000)
    close_popups(page)
    if not is_review(page):
        raise Blocked(f"Expected the application form but got {page.url[:90]}")


def page_job_title(page: Page) -> str:
    return _job_title["text"]


def ensure_signed_in(page: Page) -> None:
    """No account on this site."""


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
    """After a press without a confirmation: the form's own errors stop the run (nothing
    was sent); otherwise there's nothing more to fill."""
    errors = sorted(set(page.evaluate(ERRORS_JS)))
    if errors and is_review(page):
        raise Blocked(f"Lockheed's form refused the submit: {'; '.join(errors)[:300]}")
    return None


def confirmed_elsewhere(page: Page) -> bool:
    return submitted(page)
