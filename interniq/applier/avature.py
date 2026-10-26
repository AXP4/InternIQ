"""Drives an Avature application site (EA uses jobs.ea.com).

Three steps: Select Your Resume, Personal Information (which also creates the EA
Careers account), and EEO. The last step's button sends the application, so next_page
refuses there and only an approved submit() may click it. Dropdowns are select2
widgets; some only load their options after typing (Country, Province, University).
The account password comes from a SITE_PASSWORD slot and is filled here, never by
Claude, and password boxes are hidden from read_page.
"""

import re

from playwright.sync_api import Page

from interniq import config, db
from interniq.applier.workday import Blocked, best_option, password_slots, wait_out_bot_check

SITE = "avature"
SHOWS_FILES_ON_REVIEW = False  # the EEO step doesn't list the uploaded files
_new_slot = {"slot": 0}  # the password slot used for a new account in this run

READ_FIELDS_JS = r"""
() => {
  window.__iiq = window.__iiq || 0;
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const labelOf = el => {
    const l = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (!l) return el.getAttribute('aria-label') || el.name || el.id || '';
    const copy = l.cloneNode(true);
    copy.querySelectorAll('.screenReaderVisibility').forEach(s => s.remove());
    return copy.innerText.replace(/\s+/g, ' ').trim();
  };
  const out = [], radios = new Set();
  for (const el of document.querySelectorAll('main input, main select, main textarea, form input, form select, form textarea')) {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].includes(type)) continue;
    if (el.closest('.loginForm, form[name*="login" i], [class*="Login" i]')) continue;
    if (/^(username|password)$/i.test(labelOf(el))) continue;
    const select2 = el.classList.contains('select2-hidden-accessible');
    if (type !== 'file' && !select2 && !vis(el)) continue;
    if (type === 'radio') { if (radios.has(el.name)) continue; radios.add(el.name); }
    if (!el.dataset.iiq) el.dataset.iiq = 'f' + (++window.__iiq);
    if (out.some(f => f.id === el.dataset.iiq)) continue;
    const label = labelOf(el);
    const box = el.closest('.fieldSpec');
    const f = {id: el.dataset.iiq, name: el.name || el.id, label,
               required: el.required || (box && box.classList.contains('isRequired')) || /\*/.test(label)};
    if (type === 'file') {
      f.kind = 'file';
      f.value = text(box || el.parentElement).replace(label, '').slice(0, 200);
    } else if (el.tagName === 'SELECT') {
      const chosen = [...el.selectedOptions].map(o => o.text.trim()).filter(t => t && t !== 'Select an option');
      f.kind = el.multiple ? 'multiselect' : 'select';
      f.value = chosen.join('; ');
      f.options = [...el.options].map(o => o.text.trim()).filter(t => t && t !== 'Select an option').slice(0, 80);
      if (!f.options.length || (el.classList.contains('AutoCompleteField') && el.options.length <= 2))
        f.note = 'searchable: options load after typing; use select_search with the wanted text';
      if (el.multiple) f.note = 'multiple choice: select_option with "A; B; C" sets exactly that list';
    } else if (type === 'radio') {
      const group = [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)];
      const fs = el.closest('fieldset'); const legend = fs && fs.querySelector('legend');
      f.kind = 'radio'; f.label = text(legend) || text(el.closest('.fieldSpec') && el.closest('.fieldSpec').querySelector('label, .WizardFieldLabel')) || label;
      f.options = group.map(labelOf);
      const on = group.find(r => r.checked); f.value = on ? labelOf(on) : '';
    } else if (type === 'checkbox') { f.kind = 'checkbox'; f.value = el.checked ? 'checked' : 'unchecked'; }
    else if (el.tagName === 'TEXTAREA') { f.kind = 'textarea'; f.value = el.value; }
    else { f.kind = 'text'; f.value = el.value; }
    out.push(f);
  }
  return out;
}
"""

ERRORS_JS = r"""
() => [...document.querySelectorAll('.errorMessage, .fieldError, .error, [role="alert"], .formErrors')]
  .filter(e => e.offsetWidth).map(e => e.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean)
"""

NEXT_TEXT = re.compile(r"^(next|continue|save and continue)$", re.I)
SUBMIT_TEXT = re.compile(r"^(submit|submit application|apply|send application)$", re.I)
_job_title = {"text": ""}


def host() -> str:
    return "jobs.ea.com"


# ---------- reading ----------

def step_position(page: Page) -> tuple[int, int]:
    items = page.locator(".list--steps li").all()
    for index, item in enumerate(items, start=1):
        if "list__item--current" in (item.get_attribute("class") or ""):
            return index, len(items)
    return 0, len(items)


def current_step(page: Page) -> str:
    title = page.locator(".list--steps li.list__item--current .list__item__text__title")
    return title.first.inner_text().strip() if title.count() else ""


def page_job_title(page: Page) -> str:
    return _job_title["text"]


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    fields = page.evaluate(READ_FIELDS_JS)
    if "ProfileEdit" in page.url:
        # The profile visit only swaps the resume; other uploads there (Additional
        # Documentation) show up above the resume and travel with every application.
        fields = [f for f in fields if f["kind"] != "file" or f["label"].strip().lower() == "resume"]
    return {
        "step": current_step(page),
        "fields": fields,
        "add_buttons": [],
        "errors": sorted(set(page.evaluate(ERRORS_JS))),
    }


def field(page: Page, field_id: str):
    return page.locator(f'[data-iiq="{field_id}"]')


def describe(page: Page, field_id: str) -> dict:
    return next((f for f in page.evaluate(READ_FIELDS_JS) if f["id"] == field_id), {})


# ---------- actions (each one verifies the value stuck) ----------

def fill_text(page: Page, field_id: str, value: str) -> str:
    box = field(page, field_id)
    box.fill(value)
    box.press("Tab")
    got = box.input_value()
    return "ok" if got.strip() == value.strip() else f"value didn't stick (field shows {got!r})"


def set_select2(page: Page, field_id: str, labels: list[str]) -> None:
    """Set a select2 field's underlying select to these option labels and tell select2."""
    page.evaluate("""([id, labels]) => {
        const s = document.querySelector(`[data-iiq="${id}"]`);
        for (const o of s.options) o.selected = labels.includes(o.text.trim());
        if (window.jQuery) window.jQuery(s).trigger('change'); else s.dispatchEvent(new Event('change', {bubbles: true}));
    }""", [field_id, labels])


def select_option(page: Page, field_id: str, value: str) -> str:
    info = describe(page, field_id)
    names = info.get("options", [])
    if info.get("kind") == "multiselect":
        wanted = [v.strip() for v in value.split(";") if v.strip()]
        keep = [best_option(names, w) for w in wanted]
        missing = [w for w, k in zip(wanted, keep) if not k]
        set_select2(page, field_id, [k for k in keep if k])
        for w in missing:  # not loaded yet: search for it
            select_search(page, field_id, w)
        return f"ok (now: {describe(page, field_id).get('value', '')!r})"
    if not value.strip():
        set_select2(page, field_id, [])
        return "ok (cleared)"
    choice = best_option(names, value)
    if not choice:
        return select_search(page, field_id, value)
    set_select2(page, field_id, [choice])
    page.wait_for_timeout(1200)  # dependent fields (Province after Country) reload
    got = describe(page, field_id).get("value", "")
    return f"ok (selected {choice!r})" if got == choice else f"selection didn't stick (shows {got!r})"


def select_search(page: Page, field_id: str, value: str) -> str:
    """Type into a select2 search box and pick the matching result."""
    sel_id = field(page, field_id).get_attribute("id")
    container = page.locator(f"#fieldSpecContainer{sel_id} .select2-selection, "
                             f"[id='{sel_id}'] + .select2 .select2-selection").first
    container.click()
    page.wait_for_timeout(500)
    search = page.locator(".select2-container--open .select2-search__field").first
    if not search.count():
        search = page.locator(f"#fieldSpecContainer{sel_id} .select2-search__field").first
    search.fill(value)
    page.wait_for_timeout(2500)  # results come from the server
    results = page.locator(".select2-container--open .select2-results__option[role='option'], "
                           ".select2-container--open .select2-results__option[aria-selected]").all()
    names = [r.inner_text().strip() for r in results]
    choice = best_option(names, value)
    if not choice:
        page.keyboard.press("Escape")
        return f"no result matches {value!r}; results are: {names[:30]}"
    results[names.index(choice)].click()
    page.wait_for_timeout(1500)
    got = describe(page, field_id).get("value", "")
    return f"ok (selected {got!r})" if choice in got else f"selection didn't stick (shows {got!r})"


def choose_radio(page: Page, field_id: str, value: str) -> str:
    name = field(page, field_id).get_attribute("name")
    group = page.locator(f'input[type="radio"][name="{name}"]').all()
    labels = []
    for r in group:
        rid = r.get_attribute("id")
        lab = page.locator(f'label[for="{rid}"]') if rid else None
        labels.append(lab.first.inner_text().strip() if lab and lab.count() else (r.get_attribute("value") or ""))
    choice = best_option(labels, value)
    if not choice:
        return f"no choice matches {value!r}; choices are: {labels}"
    target = group[labels.index(choice)]
    target.check(force=True)
    return "ok" if target.is_checked() else "choice didn't stick"


def set_checkbox(page: Page, field_id: str, checked: bool, option: str | None = None) -> str:
    box = field(page, field_id)
    box.set_checked(checked, force=True)
    return "ok" if box.is_checked() == checked else "checkbox didn't change"


def set_date(page: Page, field_id: str, month: str = "", year: str = "", day: str = "") -> str:
    return "Avature dates are text boxes in YYYY-MM form; use fill_text"


def upload(page: Page, field_id: str, path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    box = field(page, field_id)
    if box.get_attribute("data-iiq-uploaded") == name:
        return f"ok ({name} is already uploaded)"
    earlier = box.get_attribute("data-iiq-uploaded")
    if earlier and box.get_attribute("multiple") is None:
        return f"this field takes one file and already holds {earlier}; not uploading {name}"
    box.set_input_files(path)
    page.wait_for_timeout(5000)
    if name not in describe(page, field_id).get("value", ""):
        return "upload didn't show up next to the field"
    box.evaluate("(e, n) => e.setAttribute('data-iiq-uploaded', n)", name)
    return f"ok ({name} uploaded)"


def press_add(page: Page, button_id: str) -> str:
    return "this site has no Add buttons"


# ---------- moving through the application ----------

def buttons(page: Page) -> list:
    return page.locator("main button:visible, form button:visible, main input[type=submit]:visible").all()


def button_text(b) -> str:
    return (b.inner_text() or b.get_attribute("value") or "").strip()


def open_application(page: Page, job_url: str) -> None:
    """Open the job page, then its application (first-time applicant path, or signed in)."""
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    wait_out_bot_check(page, "opening the job page")
    # "AI Engineer Intern - 216236 - Electronic Arts": drop the job number and company.
    _job_title["text"] = re.sub(r"\s+-\s+\d+\s+-\s+.*$", "", page.title()).strip()
    # Signed out: ApplicationMethods; signed in: ApplicationGeneralInformation. A draft
    # from an earlier run turns the button into "Finish your application".
    apply = page.locator('a[href*="/careers/Application"]',
                         has_text=re.compile(r"^\s*(apply|finish your application|continue( application)?)\s*$", re.I))
    if not apply.count():
        raise Blocked("The job page has no Apply link (closed posting?)")
    page.goto(apply.first.get_attribute("href"), wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    login = page.locator('input[name="username"]:visible')
    remembered = db.get_setting(db.connect(), f"password_slot:{host()}", "")
    if login.count() and remembered:
        sign_in(page, int(remembered))
    upload_method = page.locator("a.methodButton--file:visible")
    if upload_method.count():
        upload_method.first.click()
        page.wait_for_timeout(1500)
    if not current_step(page):
        raise Blocked(f"Expected the application form but got {page.url[:90]}")


def sign_in(page: Page, slot: int) -> None:
    password = dict(password_slots()).get(slot)
    if not password:
        raise Blocked(f"SITE_PASSWORD_{slot} is empty")
    page.locator('input[name="username"]').fill(config.require("APPLY_EMAIL"))
    page.locator('input[type="password"]').first.fill(password)
    page.locator("button:has-text('Log in')").first.click()
    page.wait_for_timeout(6000)
    if page.locator('input[name="username"]:visible').count():
        raise Blocked(f"Signing in to {host()} with password slot {slot} didn't work; stopped (no retries)")
    print(f"  signed in to {host()}")


def fill_new_account_password(page: Page) -> bool:
    """The Personal Information step creates the account: both password boxes get the first strong slot.
    Returns True when it filled them (the slot is remembered once the step is left)."""
    if page.locator('input[name="username"]:visible').count():
        return False  # step 1's sign-in box for existing accounts, not a new account
    boxes = page.locator('input[type="password"]:visible').all()
    if not boxes or all(b.input_value() for b in boxes):
        return False
    slot, password = new_account_password()
    for b in boxes:
        b.fill(password)
    _new_slot["slot"] = slot
    print(f"  new account password: slot {slot}")
    return True


def strong(password: str) -> bool:
    """EA's rule: 8+ characters, upper and lower case, a number, and a symbol."""
    return (len(password) >= 8 and all(re.search(p, password) for p in ("[a-z]", "[A-Z]", "[0-9]"))
            and bool(re.search(r"[^A-Za-z0-9]", password)))


def new_account_password() -> tuple[int, str]:
    """The first SITE_PASSWORD slot that meets the usual strength rules."""
    for slot, password in password_slots():
        if strong(password):
            return slot, password
    raise Blocked("No SITE_PASSWORD slot meets EA's password rules (8+ chars, upper, lower, number, symbol)")


def next_page(page: Page) -> str:
    if is_review(page):
        raise Blocked("Refusing to click on the final step; only an approved submit may")
    before = current_step(page)
    new_account = fill_new_account_password(page)
    candidates = [b for b in buttons(page) if NEXT_TEXT.match(button_text(b))]
    if not candidates:
        raise Blocked(f"No Next button on {before!r}")
    candidates[-1].click()
    page.wait_for_load_state()
    page.wait_for_timeout(6000)
    wait_out_bot_check(page, f"leaving {before}")
    after = current_step(page)
    if new_account and after != before:  # the account now exists
        db.set_setting(db.connect(), f"password_slot:{host()}", str(_new_slot["slot"]))
    return after


def is_review(page: Page) -> bool:
    """The last step, or any application page without the step bar: on EA the page after
    the self disclosure had no step bar and its Next sent the application, so a page we
    can't place is treated as final (the card goes up there and only submit() clicks)."""
    position, total = step_position(page)
    return not total or position == total


def files_on_review(page: Page, expected: list[str], timeout_s: int = 30) -> list[str]:
    return []


SUBMITTED_TEXT = ["thank you", "application has been submitted", "successfully submitted",
                  "received your application", "application was submitted"]


def posting_listed(page: Page) -> bool:
    """True when the account's Job Applications list shows this job (EA confirms nothing
    on the page itself)."""
    job_id = re.search(r"jobId=(\d+)", page.url)
    page.goto(f"https://{host()}/en_US/careers/ProfileJobApplications", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    text = page.locator("body").inner_text()
    return bool(job_id and job_id.group(1) in text) or (_job_title["text"] and _job_title["text"] in text)


SUBMIT_FILLS_PAGES = True  # the last step has several pages; submit() fills the rest
MAX_FINAL_PAGES = 8


def submitted(page: Page) -> bool:
    text = page.locator("body").inner_text().lower()
    return any(phrase in text for phrase in SUBMITTED_TEXT)


def submit(page: Page, posting_id: int, filler=None) -> bool:
    """Send the application, only if Ali approved it (checked here).

    EA's last step (EEO) is several pages (NDA, voluntary self disclosure, ...) and there
    is no telling which Next sends the application, so the card is posted on its first
    page and Ali's approval covers the rest: keep pressing Next/Submit, filling any page
    that asks something (the filler asks Ali when the bank has no answer), until EA
    confirms."""
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"Application {posting_id} is not approved; refusing to submit")
    if not is_review(page):
        raise Blocked("Not on the final step anymore (session may have timed out)")
    if filler is not None:
        filler.after_approval = True
    for _ in range(MAX_FINAL_PAGES):
        names = [button_text(b) for b in buttons(page)]
        final = [b for b, n in zip(buttons(page), names) if SUBMIT_TEXT.match(n) or NEXT_TEXT.match(n)]
        if not final:
            # EA's last page (after the post-approval questions) has no buttons in the form:
            # the application list is the real check.
            return submitted(page) or posting_listed(page)
        final[-1].click()
        page.wait_for_load_state()
        page.wait_for_timeout(6000)
        wait_out_bot_check(page, "submitting")
        if submitted(page):
            return True
        snapshot = read_page(page)
        if snapshot["errors"]:
            raise Blocked(f"EA refused a final page: {'; '.join(snapshot['errors'])[:300]}")
        if snapshot["fields"] and filler is not None:
            print(f"  final step page: {len(snapshot['fields'])} questions")
            filler.fill_page(snapshot)
    return submitted(page)


def ensure_signed_in(page: Page) -> None:
    """Avature signs in (or creates the account) inside the application itself."""


# ---------- the account's stored resume ----------
# A signed-in applicant skips Select Your Resume and the account's resume goes with the
# application, so it's replaced on the profile page first (like iCIMS).

PROFILE_URL = "https://jobs.ea.com/en_US/careers/Profile"


def stored_resume(page: Page) -> str:
    """The stored resume's file name, from the profile page's Preview link. Returns to the
    edit page if that's where it was called from."""
    came_from = page.url
    page.goto(PROFILE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    # Newest resume first; other documents (Additional Documentation) are listed too.
    href = page.evaluate(r"""() => { const a = [...document.querySelectorAll('a[href*="htmlPreview.php"]')]
        .find(a => { let b = a.parentElement;
                     for (let i = 0; i < 8 && b; i++, b = b.parentElement)
                       if (/\(Resume\)/.test(b.innerText.slice(0, 80))) return true;
                     return false; });
        return a ? a.href : ''; }""")
    match = re.search(r"file=[^/]*/([^&]+)", href or "")
    if "ProfileEdit" in came_from:
        page.goto(came_from, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
    return match.group(1) if match else "(none)"


def open_profile(page: Page, job_url: str) -> bool:
    """Open the profile edit page when the account exists; False for a first application."""
    slot = db.get_setting(db.connect(), f"password_slot:{host()}", "")
    if not slot:
        return False
    page.goto(PROFILE_URL + "Edit", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    if page.locator('input[name="username"]:visible').count():
        sign_in(page, int(slot))
        page.goto(PROFILE_URL + "Edit", wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
    return True


def save_profile(page: Page, before: str) -> str:
    """Save the profile, then check the stored resume really changed."""
    page.locator("button:has-text('Submit'):visible").first.click()
    page.wait_for_load_state()
    page.wait_for_timeout(5000)
    errors = page.evaluate(ERRORS_JS)
    if errors:
        return f"⚠️ The profile didn't save: {'; '.join(errors)[:200]}"
    after = stored_resume(page)
    if after == before:
        return f"⚠️ The stored resume didn't change ({after}); this job may go out with the previous job's resume"
    return f"stored resume is now {after}"
