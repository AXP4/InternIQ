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
from interniq.applier.workday import Blocked, best_option, wait_out_bot_check

SITE = "oracle"
SHOWS_FILES_ON_REVIEW = True
NEXT_TEXT = re.compile(r"^(next|continue|verify|confirm)$", re.I)
SUBMIT_TEXT = re.compile(r"^submit( application)?$", re.I)
SUBMITTED_TEXT = ["thank you for applying", "thank you for your application", "application has been submitted",
                  "we received your application", "your application was submitted", "you applied"]
_job_title = {"text": ""}

field, describe = ef.field, ef.describe
set_date = ef.set_date


def buttons(page: Page) -> list:
    return page.locator("button:visible, a[role='button']:visible, input[type=submit]:visible").all()


def button_text(b) -> str:
    return re.sub(r"\s+", " ", (b.inner_text() or b.get_attribute("value") or "")).strip()


def step_name(page: Page) -> str:
    heads = [h.strip() for h in page.locator("h2:visible, h3:visible, legend:visible").all_inner_texts() if h.strip()]
    return heads[0][:80] if heads else "Application"


CODE_ID = "verification_code"
# Inputs tagged before the snapshot: the emailed PIN boxes, the "Import your profile"
# resume box at the top (no label of its own), and type-to-search boxes (address).
TAG_JS = r"""
() => {
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  for (const e of document.querySelectorAll('input[type=file]')) {
    let b = e; for (let i = 0; i < 6 && b && !text(b); i++) b = b.parentElement;
    const t = text(b);
    if (e.classList.contains('apply-flow-profile-import-awli__file-upload') || /import your profile|apply with resume/i.test(t)) e.setAttribute('aria-label', 'Resume (import at the top: upload the resume here)');
    else if (!e.getAttribute('aria-label')) e.setAttribute('aria-label', t.slice(0, 120) || 'Supporting document');
  }
}
"""


# Questions answered with pill buttons (Yes/No, Mr./Ms., 3rd Year, 8 Months...): an
# input-row whose answers are buttons and which has no input of its own.
PILLS_JS = r"""
() => {
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const on = b => b.getAttribute('aria-pressed') === 'true' || b.getAttribute('aria-checked') === 'true'
                  || b.getAttribute('aria-selected') === 'true' || /(^|[\s_-])(selected|active|checked)/i.test(b.className || '');
  const out = [];
  let n = 0;
  document.querySelectorAll('[data-iiq-pill]').forEach(e => e.removeAttribute('data-iiq-pill'));
  for (const row of document.querySelectorAll('.input-row')) {
    if (!row.offsetWidth) continue;
    if ([...row.querySelectorAll('input, select, textarea')].some(i => i.offsetWidth && i.type !== 'hidden')) continue;
    const pills = [...row.querySelectorAll('button, [role="radio"], [role="option"], [role="button"]')]
      .filter(b => b.offsetWidth && text(b));
    if (pills.length < 2) continue;
    row.dataset.iiqPill = 'p' + (++n);
    const label = text(row.querySelector('label, .input-row__label')) || '';
    const chosen = pills.filter(on).map(text);
    out.push({id: row.dataset.iiqPill, kind: 'radio', label, required: /required/.test(row.innerHTML),
              options: pills.map(text), value: chosen.join('; ')});
  }
  return out;
}
"""


# Experience / Education entries (the resume import makes them; some come back with
# "Fields to fix"), their Edit / Delete buttons, and the editor's SAVE / CANCEL.
ENTRY_BUTTONS_JS = r"""
() => {
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const out = [];
  let n = 0;
  document.querySelectorAll('[data-iiq-btn]').forEach(e => e.removeAttribute('data-iiq-btn'));
  for (const b of document.querySelectorAll('.apply-flow-block button')) {
    if (!b.offsetWidth) continue;
    const name = text(b) || b.getAttribute('aria-label') || '';
    if (!/^(edit|delete|save|cancel|add .+)$/i.test(name)) continue;
    const block = b.closest('.apply-flow-block');
    const section = text(block.querySelector('h2, h3')) || 'Section';
    let entry = b; for (let i = 0; i < 6 && entry && !/fields to fix|\d{2}\/\d{4}/i.test(text(entry)); i++) entry = entry.parentElement;
    const what = /^(edit|delete)$/i.test(name) && entry ? text(entry).replace(/\b(edit|delete)\b/gi, '').trim().slice(0, 120) : '';
    b.dataset.iiqBtn = 'b' + (++n);
    out.push({id: b.dataset.iiqBtn, label: `${section}: ${name}${what ? ' (' + what + ')' : ''}`});
  }
  return out;
}
"""


def press_add(page: Page, button_id: str) -> str:
    button = page.locator(f'[data-iiq-btn="{button_id}"]')
    if not button.count():
        return "that button is gone; read the page again"
    button.scroll_into_view_if_needed()
    button.click()
    page.wait_for_timeout(2500)
    confirm = page.locator("[role=dialog] button:visible, .oj-dialog button:visible", has_text=re.compile(r"^\s*(delete|yes|ok)\s*$", re.I))
    if confirm.count():
        confirm.first.click()  # "Delete this item?"
        page.wait_for_timeout(2000)
    return "ok"


def pin_boxes(page: Page) -> list:
    return page.locator("input.pin-code-input__input:visible").all()


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    pins = pin_boxes(page)
    if pins:  # "Confirm Your Identity": one field for the whole emailed code
        return {"step": "Confirm Your Identity", "add_buttons": [], "errors": [],
                "fields": [{"id": CODE_ID, "kind": "text", "required": True, "value": "",
                            "label": f"Verification code ({len(pins)} digits, just emailed to Ali; ask him for it)"}]}
    page.evaluate(TAG_JS)
    ef.close_popups(page)
    fields = page.evaluate(ef.READ_FIELDS_JS)
    for f in fields:
        box = field(page, f["id"])
        cls = (box.get_attribute("class") or "") if f["kind"] == "select" else ""
        f.pop("list", None)
        if f["kind"] == "select" and "options" not in f and "cx-select-input" not in cls:
            # Never open the address search boxes just to read them: touching postal code,
            # city or province makes Oracle wipe Address Line 1.
            f["options"] = ef.combobox_options(page, f["id"])[:60]
    snapshot = {"step": "", "fields": fields, "add_buttons": [], "errors": sorted(set(page.evaluate(ef.ERRORS_JS)))}
    for f in snapshot["fields"]:
        box = field(page, f["id"])
        cls = (box.get_attribute("class") or "") if f["kind"] == "select" else ""
        if "cx-select-input" in cls:
            f["note"] = "type-to-search box: use select_search with the value (e.g. the address or city)"
            if box.get_attribute("name") == "addressLine1":
                f["note"] = ("street address search: select_search it LAST in the address block; picking the "
                             "suggestion also fills postal code, city and province, so leave those alone after")
        elif "cx-multi-select-input" in cls:
            f["value"] = "; ".join(multi_values(page, f["id"]))
            f["note"] = "multi-select: each select_option adds one choice; never select one that is already chosen"
    snapshot["fields"] = [f for f in snapshot["fields"] if "honeypot" not in f["label"].lower()]
    snapshot["fields"] += page.evaluate(PILLS_JS)  # Yes/No and other button choices
    snapshot["add_buttons"] = page.evaluate(ENTRY_BUTTONS_JS)
    for b in snapshot["add_buttons"]:
        if "fields to fix" in b["label"].lower() and ": Edit" in b["label"]:
            snapshot["errors"].append(f"{b['label']}: press Edit (press_add), fill the missing fields, then press SAVE")
    for f in snapshot["fields"]:
        if f["label"].lower().startswith("full name"):
            f["note"] = "e-signature: type the name exactly as in First, Middle and Last Name above"
    snapshot["step"] = step_name(page)
    return snapshot


def multi_values(page: Page, field_id: str) -> list[str]:
    """A multi-select keeps its choices as pills next to an always-empty text box."""
    pills = field(page, field_id).locator("xpath=ancestor::div[contains(@class,'input-row')][1]")
    return [t.strip() for t in pills.locator("ul.cx-multi-select-pill__values li").all_inner_texts() if t.strip()]


def select_option(page: Page, field_id: str, value: str) -> str:
    if "cx-multi-select-input" not in (field(page, field_id).get_attribute("class") or ""):
        return ef.select_option(page, field_id, value)
    if any(value.lower() in v.lower() for v in multi_values(page, field_id)):
        return f"ok ({value!r} is already chosen; clicking it again would remove it)"
    ef.select_option(page, field_id, value)  # its own check reads the empty text box, so check the pills
    page.keyboard.press("Escape")
    chosen = multi_values(page, field_id)
    return f"ok (chosen: {chosen})" if any(value.lower() in v.lower() for v in chosen) else f"didn't stick; chosen: {chosen}"


def choose_radio(page: Page, field_id: str, value: str) -> str:
    if not field_id.startswith("p"):
        return ef.choose_radio(page, field_id, value)
    row = page.locator(f'[data-iiq-pill="{field_id}"]')
    if not row.count():
        return "that question is gone; read the page again"
    pills = [b for b in row.locator('button, [role="radio"], [role="option"], [role="button"]').all()
             if b.is_visible() and b.inner_text().strip()]
    names = [re.sub(r"\s+", " ", b.inner_text()).strip() for b in pills]
    choice = best_option(names, value)
    if not choice:
        return f"no choice matches {value!r}; choices are: {names}"
    target = pills[names.index(choice)]
    target.scroll_into_view_if_needed()
    target.click()
    page.wait_for_timeout(700)
    chosen = next((p for p in page.evaluate(PILLS_JS) if p["id"] == field_id), {}).get("value", "")
    return f"ok (selected {choice!r})" if choice in chosen else f"clicked {choice!r}; the page shows {chosen!r}"


def set_checkbox(page: Page, field_id: str, checked: bool) -> str:
    """Oracle hides the real checkbox (input-row__hidden-control) under a styled label."""
    box = field(page, field_id)
    if "hidden-control" not in (box.get_attribute("class") or ""):
        return ef.set_checkbox(page, field_id, checked)
    if box.is_checked() != checked:
        label = page.locator(f'label[for="{box.get_attribute("id")}"]')
        target = label.first if label.count() else page.locator(f'[id="{box.get_attribute("aria-labelledby")}"]').first
        target.scroll_into_view_if_needed()
        target.click()
        page.wait_for_timeout(800)
    return "ok" if box.is_checked() == checked else f"didn't change (still {'checked' if box.is_checked() else 'unchecked'})"


def fill_text(page: Page, field_id: str, value: str) -> str:
    if field_id != CODE_ID:
        return ef.fill_text(page, field_id, value)
    code = re.sub(r"\s", "", value)
    pins = pin_boxes(page)
    if len(code) != len(pins):
        return f"the code needs {len(pins)} digits, got {len(code)}"
    pins[0].click()
    page.keyboard.type(code, delay=150)  # each box moves the focus to the next one
    got = "".join(p.input_value() for p in pin_boxes(page))
    return "ok" if got == code else f"the code boxes show {got!r}, not {code!r}"


def select_search(page: Page, field_id: str, value: str) -> str:
    """Oracle's type-to-search boxes (address, postal code, city, province): type, then
    pick the matching suggestion; if none appears, keep the typed text. Changing postal
    code, city or province wipes Address Line 1, so a box that already holds the value is
    left alone."""
    box = field(page, field_id)
    if not box.count():  # picking an address rebuilds the postal code, city and province boxes
        return "that box was rebuilt by the page; call read_page and check its value before touching it"
    squash = lambda t: re.sub(r"[\s,.]", "", t).lower()
    if box.input_value().strip() and squash(box.input_value()) == squash(value):
        return f"ok (already shows {box.input_value()!r}; left alone)"
    address = box.get_attribute("name") == "addressLine1"
    box.click()
    box.fill("")
    box.press_sequentially(value, delay=60)
    page.wait_for_timeout(3000)  # suggestions come from a map service
    listbox = box.get_attribute("aria-controls")
    options = page.locator(f'[id="{listbox}"] [role="option"], [id="{listbox}"] [role="gridcell"]').all() if listbox else []
    names = [re.sub(r"\s+", " ", o.inner_text()).strip() for o in options if o.inner_text().strip()]
    if address:  # suggestions read "123 MAIN STREET NW, CALGARY, ALBERTA, T2X"
        choice = next((n for n in names if squash(n).startswith(squash(value.split(",")[0]))), None)
    else:
        choice = best_option(names, value) if names else None
    if choice:
        options[names.index(choice)].click()
    else:
        page.keyboard.press("Tab")
    page.wait_for_timeout(2000)
    got = box.input_value()
    street = page.locator("input[name=addressLine1]")
    if not address and street.count() and not street.input_value().strip():
        return (f"ok (shows {got!r}), but Oracle cleared Address Line 1: select_search Address Line 1 again "
                "and don't touch postal code, city or province after it")
    if address and got.strip():
        rest = page.evaluate("""() => ['postalCode','city','region3'].map(n => (document.querySelector('input[name='+n+']')||{}).value).join(', ')""")
        return f"ok (shows {got!r}; the suggestion also set {rest}: leave those)"
    return f"ok (shows {got!r})" if got.strip() else f"nothing stuck; suggestions were {names[:10]}"


def upload(page: Page, field_id: str, path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    box = field(page, field_id)
    importer = (box.get_attribute("aria-label") or "").startswith("Resume (import")
    if importer and "resume" not in name.lower():
        # Oracle rebuilds the whole profile from whatever lands here (it once imported the
        # profile from the cover letter), so only the resume ever goes in this box.
        return f"refused: the Import your profile box takes only the resume; put {name} in its own box below"
    box.set_input_files(path)
    if importer:  # the page reloads with "Profile successfully imported"; values set before are gone
        page.wait_for_timeout(8000)
        return (f"ok ({name} imported; the page reloaded and re-filled the profile from it). "
                "Now call read_page and set every other answer again: nothing set before the import survived.")
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
