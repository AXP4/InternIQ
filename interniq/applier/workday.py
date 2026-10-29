"""Drives a Workday application in the visible browser.

Workday pages are built from containers marked `data-automation-id`, which stay the
same across employers: `formField-<name>` wraps one question, `progressBarActiveStep`
names the current step, and the footer Next button is Save and Continue (and
Submit on the Review step). This module only moves around and reads; deciding what to
type is the form filler's job, and submitting is gated on Ali's approval.
"""

import os
import re
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from interniq import browser, config, db, discord_bot
from interniq.elevate import clean_title

SITE = "workday"
STEPS = ["Create Account/Sign In", "My Information", "My Experience", "Application Questions",
         "Voluntary Disclosures", "Self Identify", "Review"]


class Blocked(Exception):
    """The run can't continue without Ali (sign-in failed, bot check not solved, ...)."""


def auto(page: Page, automation_id: str):
    return page.locator(f'[data-automation-id="{automation_id}"]')


def click(locator, timeout: int = 45_000) -> None:
    """Wait for the element (Workday renders slowly), then force the click: many buttons are
    covered by a transparent click_filter div."""
    locator.first.wait_for(state="visible", timeout=timeout)
    locator.first.scroll_into_view_if_needed()
    locator.first.click(force=True)


# ---------- bot checks ----------

BOT_CHECK_FRAMES = ["recaptcha", "hcaptcha", "turnstile", "challenges.cloudflare", "arkoselabs", "funcaptcha"]
BOT_CHECK_TEXT = ["verify you are human", "i'm not a robot", "i am not a robot", "complete the security check"]


def bot_check_visible(page: Page) -> bool:
    for frame in page.frames:
        url = (frame.url or "").lower()
        if "size=invisible" in url:
            continue  # an invisible reCAPTCHA's badge; a real challenge opens its own frame
        if any(marker in url for marker in BOT_CHECK_FRAMES):
            element = frame.frame_element() if frame != page.main_frame else None
            if element is None or element.is_visible():
                return True
    text = page.locator("body").inner_text().lower()
    return any(phrase in text for phrase in BOT_CHECK_TEXT)


def wait_out_bot_check(page: Page, what: str) -> None:
    """Never solve or bypass a bot check: ping Ali and wait for him to do it."""
    if not bot_check_visible(page):
        return
    discord_bot.call(discord_bot.send(
        "DISCORD_QUESTIONS_CHANNEL_ID",
        f"🤖 A bot check appeared during {what}. Please solve it in the InternIQ browser window."))
    for _ in range(180):  # up to 30 minutes
        page.wait_for_timeout(10_000)
        if not bot_check_visible(page):
            return
    raise Blocked(f"Bot check during {what} was not solved within 30 minutes")


# ---------- start and sign in ----------

def open_application(page: Page, job_url: str) -> None:
    """Job page -> Apply -> Apply Manually (never Autofill with Resume)."""
    page.goto(job_url, wait_until="domcontentloaded")
    try:
        # "Continue Application" replaces "Apply" when a saved draft exists.
        page.wait_for_selector('[data-automation-id="adventureButton"]:visible, '
                               '[data-automation-id="continueButton"]:visible', timeout=45_000)
    except PlaywrightTimeout:
        wait_out_bot_check(page, "opening the job page")
        raise Blocked("The job page never showed an Apply button (closed posting or slow site)")
    page.wait_for_timeout(1500)
    if auto(page, "legalNoticeDeclineButton").count():
        click(auto(page, "legalNoticeDeclineButton"))
    if page.locator('[data-automation-id="continueButton"]:visible').count():
        click(page.locator('[data-automation-id="continueButton"]:visible'))
        page.wait_for_timeout(6000)
        wait_out_bot_check(page, "reopening the saved draft")
        return
    click(auto(page, "adventureButton"))
    page.wait_for_timeout(3000)
    manual = auto(page, "applyManually")
    if not manual.count():
        raise Blocked("Workday didn't offer 'Apply Manually' after clicking Apply")
    click(manual)
    page.wait_for_timeout(5000)
    wait_out_bot_check(page, "opening the application")


def page_job_title(page: Page) -> str:
    """The job title Workday shows on the page being filled."""
    for automation_id in ("jobTitleHeading", "jobPostingHeader"):
        heading = auto(page, automation_id)
        if heading.count() and heading.first.inner_text().strip():
            return heading.first.inner_text().strip()
    return ""


def titles_match(posting_title: str, page_title: str) -> bool:
    """True when the page is the same job: nearly all words of the posting's title (without
    Elevate's program codes) appear in the page's title."""
    same = lambda t: re.sub(r"\bco[\s-]?op\b", "coop", t or "", flags=re.I)  # Co-op = Coop = co op
    wanted = set(words(same(clean_title(posting_title))).split())
    have = set(words(same(page_title)).split())
    # Words that tell sibling postings apart (the Fall and the Spring co-op of the same
    # job) must all match; the 80% rule alone let "Fall" match a "Spring" page.
    key = re.compile(r"^(fall|spring|summer|winter|autumn|\d+)$")
    if {w for w in wanted if key.match(w)} - have or {w for w in have if key.match(w)} - wanted:
        return False
    return bool(wanted) and len(wanted & have) >= 0.8 * len(wanted)


def recover_error_page(page: Page) -> None:
    """Workday sometimes shows 'Something went wrong. Please refresh the page'. Do what it
    says, at most twice, then give up instead of hammering the site."""
    for _ in range(2):
        if "something went wrong" not in page.locator("body").inner_text().lower():
            return
        print("  Workday error page; refreshing")
        page.wait_for_timeout(5000)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
    if "something went wrong" in page.locator("body").inner_text().lower():
        raise Blocked("Workday keeps showing 'Something went wrong' after two refreshes")


def current_step(page: Page) -> str:
    step = auto(page, "progressBarActiveStep")
    if not step.count():
        return ""
    text = step.first.inner_text()
    return next((s for s in STEPS if s.lower() in text.lower()), text.split("\n")[-1].strip())


def password_slots() -> list[tuple[int, str]]:
    """SITE_PASSWORD_1, _2, ... from .env, as many as are set (values are never printed)."""
    slots = []
    for n in range(1, 10):
        value = os.getenv(f"SITE_PASSWORD_{n}", "").strip()
        if value:
            slots.append((n, value))
    return slots


def sign_in(page: Page) -> None:
    """Sign in with APPLY_EMAIL, trying password slots in order (the remembered one first).

    Passwords are never printed. If every slot fails, stop and ask Ali; never retry.
    """
    host = urlparse(page.url).netloc
    conn = db.connect()
    key = f"password_slot:{host}"
    remembered = db.get_setting(conn, key, "")
    slots = password_slots()
    slots.sort(key=lambda s: str(s[0]) != remembered)
    if not slots:
        raise Blocked("No SITE_PASSWORD values in .env")

    if auto(page, "signInLink").count():
        click(auto(page, "signInLink"))
        page.wait_for_timeout(2000)
    for slot, password in slots:
        auto(page, "email").first.fill(config.require("APPLY_EMAIL"))
        auto(page, "password").first.fill(password)
        click(auto(page, "signInSubmitButton"))
        page.wait_for_timeout(6000)
        wait_out_bot_check(page, "sign in")
        if current_step(page) not in ("", STEPS[0]) or not auto(page, "signInSubmitButton").count():
            db.set_setting(conn, key, str(slot))
            print(f"  signed in to {host} with password slot {slot}")
            return
        print(f"  password slot {slot} didn't work on {host}")
    discord_bot.call(discord_bot.send(
        "DISCORD_QUESTIONS_CHANNEL_ID",
        f"🔑 None of the 3 site passwords worked for {host}. I stopped and won't try again. "
        "Please check the account (or reset its password) and tell me when to retry."))
    raise Blocked(f"All password slots failed on {host}")


# ---------- reading a page ----------

# Gives every visible question container a stable id (data-iiq) and describes it. Ids
# survive re-renders because already tagged containers keep theirs.
READ_FIELDS_JS = r"""
() => {
  window.__iiq = window.__iiq || 0;
  const visible = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const section = el => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const h = p.querySelector(':scope > h2, :scope > h3, :scope > h4, :scope > div > h3, :scope > div > h4');
      if (h && text(h)) return text(h);
    }
    return '';
  };
  const out = [];
  for (const box of document.querySelectorAll('[data-automation-id^="formField-"]')) {
    if (!visible(box) || box.closest('[data-automation-id="beecatcher"]')) continue;
    if (!box.dataset.iiq) box.dataset.iiq = 'f' + (++window.__iiq);
    const label = text(box.querySelector('label, legend')) || box.dataset.automationId.replace('formField-', '');
    const f = {id: box.dataset.iiq, name: box.dataset.automationId.replace('formField-', ''), label,
               section: section(box), required: /\*/.test(label) || !!box.querySelector('[aria-required="true"]')};
    const file = box.querySelector('input[type="file"]');
    const listbox = box.querySelector('button[aria-haspopup="listbox"]');
    const multi = box.querySelector('[data-automation-id="multiselectInputContainer"], [data-uxi-widget-type="multiselect"]');
    const radios = [...box.querySelectorAll('input[type="radio"]')];
    const checks = [...box.querySelectorAll('input[type="checkbox"]')];
    const date = box.querySelector('[data-automation-id^="dateSection"]');
    const area = box.querySelector('textarea');
    const input = box.querySelector('input[type="text"], input[type="email"], input[type="tel"], input[type="number"], input:not([type])');
    if (file) { f.kind = 'file';  // every uploaded file, not just the first
      f.value = [...box.querySelectorAll('[data-automation-id="file-upload-item"]')].map(text).join(' | '); }
    else if (listbox) { f.kind = 'select'; f.value = text(listbox); }
    else if (multi) { f.kind = 'multiselect'; f.value = [...box.querySelectorAll('[data-automation-id="selectedItem"]')].map(text).join('; '); }
    else if (radios.length) { f.kind = 'radio';
      f.options = radios.map(r => text(box.querySelector(`label[for="${r.id}"]`)) || r.value);
      const on = radios.find(r => r.checked); f.value = on ? (text(box.querySelector(`label[for="${on.id}"]`)) || on.value) : ''; }
    else if (checks.length > 1) { f.kind = 'checkboxes';
      f.options = checks.map(c => text(box.querySelector(`label[for="${c.id}"]`)) || c.value);
      f.value = checks.filter(c => c.checked).map(c => text(box.querySelector(`label[for="${c.id}"]`))).join('; '); }
    else if (checks.length === 1) { f.kind = 'checkbox'; f.value = checks[0].checked ? 'checked' : 'unchecked'; }
    else if (date) { f.kind = 'date';
      f.value = [...box.querySelectorAll('input[data-automation-id^="dateSection"]')].map(i => i.value).filter(Boolean).join('/'); }
    else if (area) { f.kind = 'textarea'; f.value = area.value; }
    else if (input) { f.kind = 'text'; f.value = input.value; }
    else { f.kind = 'other'; f.value = text(box).slice(0, 120); }
    out.push(f);
  }
  return out;
}
"""

ADD_BUTTONS_JS = r"""
() => [...document.querySelectorAll('button')].filter(b => /^add( another)?$/i.test(b.innerText.trim()) &&
  (b.offsetWidth || b.offsetHeight)).map((b, i) => {
    if (!b.dataset.iiqAdd) b.dataset.iiqAdd = 'add' + i + '_' + Math.random().toString(36).slice(2, 6);
    let s = ''; for (let p = b.parentElement; p && !s; p = p.parentElement) {
      const h = p.querySelector('h3, h4'); if (h) s = h.innerText.trim(); }
    return {id: b.dataset.iiqAdd, section: s, label: b.innerText.trim()};
  })
"""

ERRORS_JS = r"""
() => [...document.querySelectorAll('[data-automation-id="errorMessage"], [data-automation-id="inputAlert"], [role="alert"]')]
  .map(e => e.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean)
"""


def read_page(page: Page) -> dict:
    """Everything the form filler needs to see about the current step."""
    page.wait_for_timeout(1500)
    recover_error_page(page)
    return {
        "step": current_step(page),
        "fields": page.evaluate(READ_FIELDS_JS),
        "add_buttons": page.evaluate(ADD_BUTTONS_JS),
        # role=alert also carries success toasts ("... successfully uploaded"); those aren't errors.
        "errors": sorted({e for e in page.evaluate(ERRORS_JS) if "successfully" not in e.lower()}),
    }


# Save and Continue (and Submit on Review). Newer Workday sites use pageFooterNextButton,
# older ones bottom-navigation-next-button.
NEXT_BUTTON = '[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"]'


def next_button(page: Page):
    return page.locator(NEXT_BUTTON)


def field(page: Page, field_id: str):
    return page.locator(f'[data-iiq="{field_id}"]')


def next_page(page: Page) -> str:
    """Save and Continue. Returns the new step name (unchanged if Workday showed errors)."""
    before = current_step(page)
    click(next_button(page))
    try:
        page.wait_for_function(
            "s => { const e = document.querySelector('[data-automation-id=\"progressBarActiveStep\"]');"
            " return e && !e.innerText.includes(s); }", arg=before, timeout=15_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(2500)
    wait_out_bot_check(page, f"leaving {before}")
    return current_step(page)


def is_review(page: Page) -> bool:
    if current_step(page) == "Review":
        return True
    button = next_button(page)
    return bool(button.count() and re.search(r"^\s*submit\s*$", button.first.inner_text(), re.I))


# ---------- actions (each one verifies the value stuck) ----------

def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+]+", (text or "").lower()))


def best_option(options: list[str], wanted: str) -> str | None:
    """Exact match first, then an option containing the wanted text, then the reverse."""
    w = words(wanted)
    for test in (lambda o: words(o) == w, lambda o: w and w in words(o), lambda o: words(o) and words(o) in w):
        hit = next((o for o in options if test(o)), None)
        if hit:
            return hit
    return None


def describe(page: Page, field_id: str) -> dict:
    return next((f for f in page.evaluate(READ_FIELDS_JS) if f["id"] == field_id), {})


def listbox_options(page: Page) -> list:
    """Options of the dropdown that is open right now.

    Workday renders open dropdowns in a popup outside the question containers; selected
    pills inside a container also use role=option, so those are left out.
    """
    return [o for o in page.locator('[role="listbox"]:visible [role="option"]').all()
            if o.evaluate("e => !e.closest('[data-automation-id^=\"formField-\"]')")]


def prompt_options(page: Page) -> list:
    """Suggestions under a searchable prompt field (not the already selected pills)."""
    return page.locator('[data-automation-id="promptOption"]:visible').all()


def fill_text(page: Page, field_id: str, value: str) -> str:
    box = field(page, field_id).locator("textarea, input").first
    box.fill("")
    box.fill(value)
    box.press("Tab")
    got = describe(page, field_id).get("value", "")
    return "ok" if got.strip() == value.strip() else f"value didn't stick (field shows {got!r})"


def select_option(page: Page, field_id: str, value: str) -> str:
    button = field(page, field_id).locator('button[aria-haspopup="listbox"]').first
    click(button)
    page.wait_for_timeout(800)
    options = listbox_options(page)
    names = [o.inner_text().strip() for o in options]
    choice = best_option(names, value)
    if not choice:
        page.keyboard.press("Escape")
        return f"no option matches {value!r}; options are: {names[:60]}"
    options[names.index(choice)].click()
    page.wait_for_timeout(800)
    got = describe(page, field_id).get("value", "")
    return f"ok (selected {choice!r})" if words(choice) in words(got) else f"selection didn't stick (shows {got!r})"


def select_search(page: Page, field_id: str, value: str) -> str:
    """Searchable prompt fields: type, press Enter, pick the matching suggestion."""
    current = describe(page, field_id).get("value", "")
    if current and (words(value) in words(current) or words(current) in words(value)):
        return f"ok (already selected {current!r})"
    box = field(page, field_id).locator("input").first
    box.click()
    box.fill(value)
    box.press("Enter")
    page.wait_for_timeout(1500)
    selected = {p.inner_text().strip() for p in field(page, field_id).locator('[data-automation-id="selectedItem"]').all()}
    options = [o for o in prompt_options(page) if o.inner_text().strip() not in selected]
    names = [o.inner_text().strip() for o in options]
    choice = best_option(names, value)
    if choice:
        options[names.index(choice)].click()
        page.wait_for_timeout(1000)
    elif names:
        page.keyboard.press("Escape")
        return f"no suggestion matches {value!r}; suggestions are: {names[:40]}"
    got = describe(page, field_id).get("value", "")
    return f"ok (selected {got!r})" if got else "nothing selected"


def choose_radio(page: Page, field_id: str, value: str) -> str:
    box = field(page, field_id)
    labels = box.locator("label").all()
    names = [lab.inner_text().strip() for lab in labels]
    choice = best_option(names, value)
    if not choice:
        return f"no choice matches {value!r}; choices are: {names}"
    labels[names.index(choice)].click()
    got = describe(page, field_id).get("value", "")
    return "ok" if words(choice) == words(got) else f"choice didn't stick (shows {got!r})"


def set_checkbox(page: Page, field_id: str, checked: bool, option: str | None = None) -> str:
    """Tick or untick one checkbox; `option` picks one box in a group by its label."""
    box = field(page, field_id)
    boxes = box.locator('input[type="checkbox"]').all()
    names = [box.locator(f'label[for="{b.get_attribute("id")}"]').first.inner_text().strip()
             if b.get_attribute("id") and box.locator(f'label[for="{b.get_attribute("id")}"]').count() else ""
             for b in boxes]
    target = boxes[0]
    if option:
        choice = best_option(names, option)
        if not choice:
            return f"no checkbox matches {option!r}; options are: {names}"
        target = boxes[names.index(choice)]
    if target.is_checked() != checked:
        target.click(force=True)
        page.wait_for_timeout(500)
    return "ok" if target.is_checked() == checked else "checkbox didn't change"


def set_date(page: Page, field_id: str, month: str = "", year: str = "", day: str = "") -> str:
    box = field(page, field_id)
    for part, value in (("Month", month), ("Day", day), ("Year", year)):
        section = box.locator(f'[data-automation-id="dateSection{part}-input"]')
        display = box.locator(f'[data-automation-id="dateSection{part}-display"]')
        if value and section.count():
            # The real input is a zero-size box under a visible display layer: click the
            # layer (or focus the input directly), then type.
            if display.count() and display.first.is_visible():
                display.first.click()
            else:
                section.first.focus()
            page.keyboard.type(str(value).zfill(4 if part == "Year" else 2))
    page.keyboard.press("Tab")
    got = describe(page, field_id).get("value", "")
    wanted = [str(int(v)) if v.isdigit() else v for v in (month, day, year) if v]
    if all(w in got.split("/") for w in wanted):
        return f"ok (shows {got!r})"
    return f"date didn't stick (shows {got!r})"


def upload(page: Page, field_id: str, path: str) -> str:
    """Upload one file. In a multi-file field the earlier files stay."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    current = describe(page, field_id).get("value", "")
    if name in current:
        return f"ok ({name} is already uploaded)"
    file_input = field(page, field_id).locator('input[type="file"]').first
    if current and file_input.get_attribute("multiple") is None:
        return f"this field takes one file and already holds {current!r}; uploading would replace it"
    file_input.set_input_files(path)
    # Workday lists a new file only after the upload finishes, which can take a while;
    # checking too early once made a good upload look failed.
    got = ""
    for _ in range(15):
        page.wait_for_timeout(2000)
        got = describe(page, field_id).get("value", "")
        if name in got:
            return f"ok (field now shows {got!r})"
    return f"upload didn't show up after 30 seconds (field shows {got!r})"


def press_add(page: Page, button_id: str) -> str:
    button = page.locator(f'[data-iiq-add="{button_id}"]')
    if not button.count():
        return "that Add button is gone; read the page again"
    click(button)
    page.wait_for_timeout(1500)
    return "ok, new fields added"


# ---------- review and submit ----------

SUBMITTED_TEXT = ["application submitted", "thank you for applying", "successfully submitted",
                  "your application has been submitted", "congratulations"]


def submit(page: Page, posting_id: int) -> bool:
    """Click Submit on the Review page, only if Ali approved this application.

    The approval check lives here, next to the click, so no caller can skip it.
    Returns True when Workday confirms the submission.
    """
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"Application {posting_id} is not approved; refusing to submit")
    if not is_review(page):
        raise Blocked("Not on the Review page anymore (session may have timed out)")
    click(next_button(page))
    page.wait_for_timeout(8000)
    wait_out_bot_check(page, "submitting")
    text = page.locator("body").inner_text().lower()
    return any(phrase in text for phrase in SUBMITTED_TEXT)


def files_on_review(page: Page, expected: list[str], timeout_s: int = 30) -> list[str]:
    """Wait for the Review page to list every expected file name; return the missing ones."""
    missing = expected
    for _ in range(timeout_s // 2):
        text = page.locator("body").inner_text()
        missing = [name for name in expected if name not in text]
        if not missing:
            return []
        page.wait_for_timeout(2000)
    return missing


def ensure_signed_in(page: Page) -> None:
    """Sign in if the application opened on Workday's Create Account/Sign In step."""
    if current_step(page) == STEPS[0]:
        sign_in(page)
