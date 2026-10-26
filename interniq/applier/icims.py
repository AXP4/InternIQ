"""Drives an iCIMS application portal (AMD uses campuscanada-amd.icims.com).

iCIMS shows the portal inside an iframe (its URL has in_iframe=1), and existing users
sign in on iCIMS's central login (login.icims.com: username, then password). The same
bot check rule as everywhere: never solve one, ping Ali and wait.
"""

import re
from urllib.parse import urlparse

from playwright.sync_api import Frame, Page

from interniq import config, db, discord_bot
from interniq.applier.workday import (Blocked, best_option, bot_check_visible, password_slots,
                                      wait_out_bot_check)

SITE = "icims"


def content(page: Page) -> Frame:
    """The frame holding the portal's real content (the page itself on login.icims.com)."""
    return next((f for f in page.frames if "in_iframe=1" in f.url), page.main_frame)


def portal(job_url: str) -> str:
    """https://campuscanada-amd.icims.com from any job or login URL on that portal."""
    parsed = urlparse(job_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def job_number(job_url: str) -> str | None:
    match = re.search(r"/jobs/(\d+)", job_url)
    return match.group(1) if match else None


def decline_passkey(page: Page) -> bool:
    """After a successful login iCIMS may offer a passkey ("Log In Faster on This Device").
    Decline it: the agent's browser should never hold a fingerprint or face login."""
    if not page.get_by_text("Log In Faster on This Device").count():
        return False
    page.get_by_text("Not on this device").first.click()
    print("  declined the iCIMS passkey prompt")
    return True


def sign_in(page: Page, portal_url: str, job: str) -> None:
    """Portal login page -> accept the privacy statement (Ali approved this for AMD) ->
    email -> central iCIMS login -> password slots in order (the remembered one first)."""
    page.goto(f"{portal_url}/jobs/{job}/login", wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    frame = content(page)
    if frame.locator("#accept_gdpr:visible").count() and not frame.locator("#email:visible").count():
        # The privacy statement alone (the portal remembered the email): accept and go on.
        frame.locator("#accept_gdpr").check()
        frame.locator('#enterEmailSubmitButton, input[type="submit"]:visible, button[type="submit"]:visible').first.click()
        page.wait_for_timeout(6000)
        wait_out_bot_check(page, "the AMD privacy statement")
        frame = content(page)
    if frame.locator("#email:visible").count():
        frame.locator("#email").fill(config.require("APPLY_EMAIL"))
        if frame.locator("#accept_gdpr").count():
            frame.locator("#accept_gdpr").check()
        frame.locator("#enterEmailSubmitButton").click()
        # An hCaptcha challenge usually pops up a few seconds after Next. Watch for it (Ali
        # solves it) or for the move to iCIMS's central login, for about a minute.
        for _ in range(30):
            page.wait_for_timeout(2000)
            if "login.icims.com" in page.url:
                break
            if bot_check_visible(page):
                wait_out_bot_check(page, "the AMD login (email step)")

    if "login.icims.com" not in page.url:
        # The iframe reloads as the portal moves on, so look at the current one.
        try:
            still_on_email = content(page).locator("#email:visible").count() > 0
        except Exception:
            still_on_email = False
        if still_on_email:
            raise Blocked("The AMD portal stayed on its login page after the email step")
        print("  already signed in to the portal (session remembered)")
        return

    host = urlparse(portal_url).netloc
    key = f"password_slot:{host}"
    conn = db.connect()
    remembered = db.get_setting(conn, key, "")
    slots = password_slots()
    if remembered:
        # A known slot is the only one tried: no extra wrong attempts on a known site.
        slots = [s for s in slots if str(s[0]) == remembered]
    if page.locator("#username:visible").count():
        page.locator("#username").fill(config.require("APPLY_EMAIL"))
        page.locator('button[name="action"][type="submit"]').first.click()
    # Watch for what comes next instead of assuming: the password box, a bot check, or
    # already being back on the portal.
    for _ in range(30):
        page.wait_for_timeout(2000)
        if "login.icims.com" not in page.url:
            print(f"  signed in to {host} (session remembered)")
            return
        if decline_passkey(page):
            continue
        if page.locator("#password:visible").count():
            break
        if bot_check_visible(page):
            wait_out_bot_check(page, "the iCIMS login (username step)")
    else:
        heading = " ".join(t.strip() for t in page.locator("h1:visible, h2:visible").all_inner_texts())
        page.screenshot(path=str(config.ROOT / "data" / "icims_login_stuck.png"), full_page=True)
        raise Blocked(f"iCIMS login showed {heading!r} instead of the password box "
                      "(screenshot in data/icims_login_stuck.png)")
    for slot, password in slots:
        page.locator("#password").first.wait_for(state="visible", timeout=30_000)
        page.locator("#password").first.fill(password)
        page.locator('button[name="action"][type="submit"]').first.click()
        page.wait_for_timeout(8000)
        wait_out_bot_check(page, "the iCIMS login (password step)")
        if decline_passkey(page):
            page.wait_for_timeout(6000)
        heading =" ".join(t.strip() for t in page.locator("h1:visible, h2:visible").all_inner_texts())
        print(f"  after password slot {slot}: {page.url.split('?')[0]} | {heading!r}")
        on_password_page = "/u/login/password" in page.url and page.locator("#password:visible").count()
        if "login.icims.com" in page.url and not on_password_page:
            # Not a wrong password: iCIMS wants another step (verification code, "verify it's you").
            discord_bot.call(discord_bot.send(
                "DISCORD_QUESTIONS_CHANNEL_ID",
                f"🔐 iCIMS is asking for an extra login step ({heading or 'unknown page'}). "
                "Please finish it in the InternIQ browser window."))
            for _ in range(180):  # up to 30 minutes
                page.wait_for_timeout(10_000)
                if "login.icims.com" not in page.url:
                    break
        if "login.icims.com" not in page.url:
            db.set_setting(conn, key, str(slot))
            print(f"  signed in to {host} with password slot {slot}")
            return
        print(f"  password slot {slot} didn't work on {host}")
    discord_bot.call(discord_bot.send(
        "DISCORD_QUESTIONS_CHANNEL_ID",
        f"🔑 None of the 3 site passwords worked for {host}. I stopped and won't try again."))
    raise Blocked(f"All password slots failed on {host}")


def dashboard_jobs(page: Page, portal_url: str) -> list[dict]:
    """Every row of the portal's "Past Job Submittals" table: job number, title, status.

    Rows look like: 2026-90383 | Summer 2027 Long Term ML/AI ... | Received Submission | ...
    """
    page.goto(f"{portal_url}/jobs/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    rows = content(page).evaluate("""() => [...document.querySelectorAll('tr')]
        .map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))""")
    jobs = []
    for cells in rows:
        match = re.match(r"\d{4}-(\d+)$", cells[0]) if cells else None
        if match and len(cells) >= 3:
            jobs.append({"job": match.group(1), "title": cells[1], "status": cells[2]})
    return jobs


# ---------- reading a page (inside the content iframe) ----------

READ_FIELDS_JS = r"""
() => {
  window.__iiq = window.__iiq || 0;
  const vis = e => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const labelFor = el => {
    const l = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    return text(l) || el.getAttribute('aria-label') || el.name || el.id || '';
  };
  const legend = el => { const fs = el.closest('fieldset'); return text(fs && fs.querySelector('legend')); };
  const out = [], radios = new Set();
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (type !== 'file' && !vis(el)) continue;
    if (type === 'radio') { if (radios.has(el.name)) continue; radios.add(el.name); }
    if (!el.dataset.iiq) el.dataset.iiq = 'f' + (++window.__iiq);
    const label = labelFor(el);
    const f = {id: el.dataset.iiq, name: el.name || el.id, label, section: legend(el),
               required: el.required || el.getAttribute('aria-required') === 'true' || /\*/.test(label)};
    if (type === 'file') {
      f.kind = 'file';
      let box = el.parentElement;
      for (let i = 0; i < 3 && box && text(box).length < 20; i++) box = box.parentElement;
      f.value = text(box).slice(0, 200);
    } else if (el.tagName === 'SELECT') {
      f.kind = 'select';
      f.value = el.selectedIndex >= 0 ? el.options[el.selectedIndex].text.trim() : '';
      f.options = [...el.options].map(o => o.text.trim()).slice(0, 60);
    } else if (type === 'radio') {
      const group = [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)];
      f.kind = 'radio'; f.label = legend(el) || label;
      f.options = group.map(labelFor);
      const on = group.find(r => r.checked); f.value = on ? labelFor(on) : '';
    } else if (type === 'checkbox') { f.kind = 'checkbox'; f.value = el.checked ? 'checked' : 'unchecked'; }
    else if (el.tagName === 'TEXTAREA') { f.kind = 'textarea'; f.value = el.value; }
    else { f.kind = 'text'; f.value = el.value; }
    out.push(f);
  }
  return out;
}
"""

ADD_BUTTONS_JS = r"""
() => [...document.querySelectorAll('a, button')].filter(b => /^add more/i.test(b.innerText.trim()) && b.offsetWidth)
  .map(b => { if (!b.dataset.iiqAdd) b.dataset.iiqAdd = 'add' + Math.random().toString(36).slice(2, 7);
              return {id: b.dataset.iiqAdd, section: b.innerText.trim(), label: b.innerText.trim()}; })
"""

ERRORS_JS = r"""
() => [...document.querySelectorAll('.iCIMS_Error, .iCIMS_ErrorMessage, [role="alert"], .error, .field-error')]
  .filter(e => e.offsetWidth).map(e => e.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean)
"""

# "Update Profile" ends the shared Candidate Profile page and moves on to the job's questions.
NEXT_TEXT = re.compile(r"^(next|continue|save( and continue)?|save & continue|submit profile|update profile)$", re.I)
SUBMIT_TEXT = re.compile(r"^submit( (my )?application)?$", re.I)


def buttons(frame: Frame) -> list:
    return frame.locator('button:visible, input[type="submit"]:visible, a[role="button"]:visible').all()


def button_text(b) -> str:
    return (b.inner_text() or b.get_attribute("value") or "").strip()


def current_step(page: Page) -> str:
    heads = content(page).locator("h2:visible").all_inner_texts()
    return heads[0].strip() if heads else ""


def page_job_title(page: Page) -> str:
    heads = content(page).locator("h1:visible").all_inner_texts()
    return heads[0].strip() if heads else ""


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    frame = content(page)
    return {
        "step": current_step(page),
        "fields": frame.evaluate(READ_FIELDS_JS),
        "add_buttons": frame.evaluate(ADD_BUTTONS_JS),
        "errors": sorted({e for e in frame.evaluate(ERRORS_JS) if "success" not in e.lower()}),
    }


def field(page: Page, field_id: str):
    return content(page).locator(f'[data-iiq="{field_id}"]')


def describe(page: Page, field_id: str) -> dict:
    return next((f for f in content(page).evaluate(READ_FIELDS_JS) if f["id"] == field_id), {})


# ---------- actions (each one verifies the value stuck) ----------

def fill_text(page: Page, field_id: str, value: str) -> str:
    box = field(page, field_id)
    box.fill(value)
    box.press("Tab")
    got = box.input_value()
    return "ok" if got.strip() == value.strip() else f"value didn't stick (field shows {got!r})"


def select_option(page: Page, field_id: str, value: str) -> str:
    box = field(page, field_id)
    names = box.evaluate("e => [...e.options].map(o => o.text.trim())")
    choice = best_option(names, value)
    if not choice:
        return f"no option matches {value!r}; options are: {names[:80]}"
    box.select_option(label=choice)
    page.wait_for_timeout(1200)  # dependent dropdowns (Region after Country) reload
    got = describe(page, field_id).get("value", "")
    return f"ok (selected {choice!r})" if got == choice else f"selection didn't stick (shows {got!r})"


def select_search(page: Page, field_id: str, value: str) -> str:
    return select_option(page, field_id, value)


def choose_radio(page: Page, field_id: str, value: str) -> str:
    frame = content(page)
    name = field(page, field_id).get_attribute("name")
    group = frame.locator(f'input[type="radio"][name="{name}"]').all()
    labels = []
    for r in group:
        rid = r.get_attribute("id")
        lab = frame.locator(f'label[for="{rid}"]') if rid else None
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
    return ("iCIMS dates are separate Month and Day dropdowns plus a Year box; "
            "fill each one with select_option and fill_text")


def upload(page: Page, field_id: str, path: str) -> str:
    """Upload (or replace) a file.

    iCIMS shows the stored file with a "Replace ..." button; the file input only works
    after that button is clicked, and the new name only shows next to the field once the
    page is saved. So success means: the input now holds our file and any resume parsing
    has finished. A resume upload is parsed, and the parser re-fills profile fields.
    """
    frame = content(page)
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    file_input = field(page, field_id)
    base = (file_input.get_attribute("id") or "").removesuffix("_File")
    if name in describe(page, field_id).get("value", ""):
        return f"ok ({name} is already uploaded)"
    replace = frame.locator(f'[id="{base}_Button"]:visible')
    if replace.count():
        replace.first.click()
        page.wait_for_timeout(1500)
    file_input.set_input_files(path)
    loading = frame.locator(f'[id="{base}_Loading"]:visible')
    for _ in range(30):  # parsing can take a while
        page.wait_for_timeout(2000)
        if not loading.count():
            break
    chosen = file_input.evaluate("e => e.files && e.files.length ? e.files[0].name : ''")
    if chosen != name:
        return f"upload didn't take (the file box holds {chosen!r})"
    parsed = " The resume was parsed, which can re-fill profile fields: call read_page and check them." \
        if "resume" in base.lower() else ""
    return f"ok ({name} chosen; it shows on the page after the profile is saved).{parsed}"


def press_add(page: Page, button_id: str) -> str:
    button = content(page).locator(f'[data-iiq-add="{button_id}"]')
    if not button.count():
        return "that Add More link is gone; read the page again"
    button.first.click()
    page.wait_for_timeout(2000)
    return "ok, new fields added"


# ---------- moving through the application ----------

def apply_url(page: Page, job_url: str) -> str:
    """The portal's apply URL, read from the employer's job page Apply link."""
    if "icims.com" in job_url:
        return job_url if "/login" in job_url else f"{portal(job_url)}/jobs/{job_number(job_url)}/login"
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    # The job's own Apply button: other iCIMS links on the page lead to other portals
    # (AMD's general careers-amd portal instead of the student campuscanada-amd one).
    link = page.locator('a#link-apply[href*="icims.com"]')
    if not link.count():
        link = page.locator('a[href*="icims.com"]', has_text=re.compile(r"^\s*apply\s*$", re.I))
    if not link.count():
        raise Blocked("The job page has no iCIMS Apply button (closed posting?)")
    href = link.first.get_attribute("href")
    print(f"  apply link: {href}")
    return href


def open_application(page: Page, job_url: str) -> None:
    """Open the job's application form, signing in first if the portal asks."""
    target = apply_url(page, job_url)
    page.goto(target, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    try:
        frame = content(page)
        needs_login = (frame.locator("#email:visible").count() + frame.locator("#accept_gdpr:visible").count()) > 0
    except Exception:
        needs_login = False
    if needs_login or "login.icims.com" in page.url:
        sign_in(page, portal(target), job_number(target))
        page.goto(target, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
    wait_out_bot_check(page, "opening the application")
    # Make sure this is the application form, not the dashboard or a login page.
    snapshot = read_page(page)
    if not snapshot["fields"] or snapshot["step"] in ("General Options", ""):
        raise Blocked(f"Expected the application form but the portal shows {snapshot['step']!r} "
                      f"({page.url[:90]})")


def next_page(page: Page) -> str:
    """Click Next / Save and Continue (never a final Submit). Returns the new step name."""
    before = current_step(page)
    candidates = [b for b in buttons(content(page)) if NEXT_TEXT.match(button_text(b))]
    if not candidates:
        raise Blocked(f"No Next button on {before!r}")
    candidates[-1].click()
    page.wait_for_timeout(6000)
    wait_out_bot_check(page, f"leaving {before}")
    return current_step(page)


def is_review(page: Page) -> bool:
    """The last page: a final Submit button and no Next button."""
    names = [button_text(b) for b in buttons(content(page))]
    return any(SUBMIT_TEXT.match(n) for n in names) and not any(NEXT_TEXT.match(n) for n in names)


def files_on_review(page: Page, expected: list[str], timeout_s: int = 30) -> list[str]:
    missing = expected
    for _ in range(timeout_s // 2):
        text = content(page).locator("body").inner_text()
        missing = [name for name in expected if name not in text]
        if not missing:
            return []
        page.wait_for_timeout(2000)
    return missing


SUBMITTED_TEXT = ["thank you", "application has been submitted", "successfully submitted",
                  "received your application"]


def submit(page: Page, posting_id: int) -> bool:
    """Click the final Submit, only if Ali approved this application (checked here)."""
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"Application {posting_id} is not approved; refusing to submit")
    if not is_review(page):
        raise Blocked("Not on the final page anymore (session may have timed out)")
    [b for b in buttons(content(page)) if SUBMIT_TEXT.match(button_text(b))][-1].click()
    page.wait_for_timeout(8000)
    wait_out_bot_check(page, "submitting")
    text = content(page).locator("body").inner_text().lower()
    return any(phrase in text for phrase in SUBMITTED_TEXT)


def ensure_signed_in(page: Page) -> None:
    """iCIMS signs in inside open_application, so there's nothing left to do here."""
