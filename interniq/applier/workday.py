"""Drives a Workday application in the visible browser.

Workday pages are built from containers marked `data-automation-id`, which stay the
same across employers: `formField-<name>` wraps one question, `progressBarActiveStep`
names the current step, `bottom-navigation-next-button` is Save and Continue (and
Submit on the Review step). This module only moves around and reads; deciding what to
type is the form filler's job, and submitting is gated on Ali's approval.
"""

import os
import re
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from interniq import browser, config, db, discord_bot

SITE = "workday"
STEPS = ["Create Account/Sign In", "My Information", "My Experience", "Application Questions",
         "Voluntary Disclosures", "Self Identify", "Review"]


class Blocked(Exception):
    """The run can't continue without Ali (sign-in failed, bot check not solved, ...)."""


def auto(page: Page, automation_id: str):
    return page.locator(f'[data-automation-id="{automation_id}"]')


def click(locator) -> None:
    """Workday covers many buttons with a transparent click_filter div; force the click."""
    locator.first.scroll_into_view_if_needed()
    locator.first.click(force=True)


# ---------- bot checks ----------

BOT_CHECK_FRAMES = ["recaptcha", "hcaptcha", "turnstile", "challenges.cloudflare", "arkoselabs", "funcaptcha"]
BOT_CHECK_TEXT = ["verify you are human", "i'm not a robot", "i am not a robot", "complete the security check"]


def bot_check_visible(page: Page) -> bool:
    for frame in page.frames:
        if any(marker in (frame.url or "").lower() for marker in BOT_CHECK_FRAMES):
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
    page.wait_for_timeout(5000)
    if auto(page, "legalNoticeDeclineButton").count():
        click(auto(page, "legalNoticeDeclineButton"))
    click(auto(page, "adventureButton"))
    page.wait_for_timeout(3000)
    manual = auto(page, "applyManually")
    if not manual.count():
        raise Blocked("Workday didn't offer 'Apply Manually' after clicking Apply")
    click(manual)
    page.wait_for_timeout(5000)
    wait_out_bot_check(page, "opening the application")


def current_step(page: Page) -> str:
    step = auto(page, "progressBarActiveStep")
    if not step.count():
        return ""
    text = step.first.inner_text()
    return next((s for s in STEPS if s.lower() in text.lower()), text.split("\n")[-1].strip())


def password_slots() -> list[tuple[int, str]]:
    return [(n, os.getenv(f"SITE_PASSWORD_{n}", "").strip()) for n in (1, 2, 3)
            if os.getenv(f"SITE_PASSWORD_{n}", "").strip()]


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
    if (file) { f.kind = 'file'; f.value = text(box.querySelector('[data-automation-id="file-upload-item"]')); }
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
      f.value = [...box.querySelectorAll('[data-automation-id^="dateSection"] input')].map(i => i.value).join('/'); }
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
    return {
        "step": current_step(page),
        "fields": page.evaluate(READ_FIELDS_JS),
        "add_buttons": page.evaluate(ADD_BUTTONS_JS),
        "errors": sorted(set(page.evaluate(ERRORS_JS))),
    }


def field(page: Page, field_id: str):
    return page.locator(f'[data-iiq="{field_id}"]')


def next_page(page: Page) -> str:
    """Save and Continue. Returns the new step name (unchanged if Workday showed errors)."""
    before = current_step(page)
    click(auto(page, "bottom-navigation-next-button"))
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
    button = auto(page, "bottom-navigation-next-button")
    return bool(button.count() and re.search(r"^\s*submit\s*$", button.first.inner_text(), re.I))
