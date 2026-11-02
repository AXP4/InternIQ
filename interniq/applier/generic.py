"""The catch-all driver: any application site without a driver of its own (BambooHR, ADP,
Oleeo, company career pages, ...).

It only relies on what every web form has: labelled inputs, selects, radios, checkboxes,
file boxes, and buttons. Labels come from <label for>, a wrapping <label>, aria
attributes, or the nearest question text above the field. From the job page it presses
the Apply button; Next / Continue move through the steps; the page with a Submit (or
Apply / Send) button and no Next is the final page, which only an approved submit() presses.
A sign-in or create-account page is Ali's: he's pinged and the agent waits for him in the
visible window (and never touches a CAPTCHA).
"""

import re

from playwright.sync_api import Page

from interniq import db, discord_bot
from interniq.applier import eightfold as ef
from interniq.applier.workday import Blocked, best_option, wait_out_bot_check

SITE = "generic"
SHOWS_FILES_ON_REVIEW = True
APPLY_TEXT = re.compile(r"^\s*(apply|apply now|apply online|apply for (this )?(job|position|role)|"
                        r"start (your )?application|i'?m interested)\s*$", re.I)
NEXT_TEXT = re.compile(r"^\s*(next|continue|save (and|&) continue|save (and|&) next|proceed|next step)\s*[>›»]?\s*$", re.I)
SUBMIT_TEXT = re.compile(r"^\s*(submit|submit (my |your )?application|apply|send( my)? application|"
                         r"complete application|finish)\s*$", re.I)
SUBMITTED_TEXT = ["thank you for applying", "thank you for your application", "thanks for applying",
                  "application has been submitted", "application was submitted", "application submitted",
                  "successfully submitted", "we have received your application", "we received your application",
                  "your application has been received", "thank you for your interest"]
SIGN_IN_WAIT_MS = 20 * 60 * 1000
_job_title = {"text": ""}

field, describe = ef.field, ef.describe
fill_text = ef.fill_text
set_checkbox, set_date = ef.set_checkbox, ef.set_date

# Fields whose label the Eightfold reader couldn't find (no <label for>, no aria) get the
# question text around them: a wrapping <label>, or the nearest short text block above.
LABEL_JS = r"""
() => {
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const out = {};
  for (const el of document.querySelectorAll('[data-iiq]')) {
    const wrap = el.closest('label');
    let t = text(wrap);
    if (!t) {
      let box = el.parentElement;
      for (let i = 0; i < 5 && box; i++, box = box.parentElement) {
        const own = text(box);
        if (own && own.length < 400 && own !== (el.value || '').trim()) { t = own; break; }
      }
    }
    out[el.dataset.iiq] = t.slice(0, 300);
  }
  return out;
}
"""

# Fixes for what the Eightfold reader gets wrong on other sites: radios whose <label> wraps
# the input (options and value from the label text), and "menu selects" (BambooHR): a hidden,
# empty <select> next to a visible toggle button that opens a menu of options.
FIX_JS = r"""
() => {
  const text = e => (e ? e.innerText.replace(/\s+/g, ' ').trim() : '');
  const radioLabel = r => text((r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`)) || r.closest('label')) || r.value;
  const out = {};
  for (const el of document.querySelectorAll('[data-iiq]')) {
    const id = el.dataset.iiq;
    if (el.type === 'radio') {
      const group = [...document.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)];
      const on = group.find(r => r.checked);
      out[id] = {options: group.map(radioLabel), value: on ? radioLabel(on) : ''};
    } else if (el.tagName === 'SELECT' && !el.offsetWidth) {
      let box = el.parentElement, toggle = null;
      for (let i = 0; i < 4 && box && !toggle; i++, box = box.parentElement)
        toggle = [...box.querySelectorAll('button[aria-haspopup]')].find(b => b.offsetWidth);
      if (!toggle) continue;
      toggle.dataset.iiqToggle = id;
      const shown = text(toggle);
      out[id] = {menu: true, value: /^[-–—\s]*select[-–—\s]*$/i.test(shown) ? '' : shown};
    }
  }
  return out;
}
"""


def menu_options(page: Page, field_id: str, close: bool = True) -> list:
    toggle = page.locator(f'[data-iiq-toggle="{field_id}"]')
    toggle.scroll_into_view_if_needed()
    toggle.click()
    page.wait_for_timeout(1000)
    items = [o for o in page.locator('[role="menuitem"], [role="option"]').all() if o.is_visible()]
    if close:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    return items


def select_option(page: Page, field_id: str, value: str) -> str:
    if not page.locator(f'[data-iiq-toggle="{field_id}"]').count():
        return ef.select_option(page, field_id, value)
    shown = page.evaluate(FIX_JS).get(field_id, {}).get("value", "")
    if shown and best_option([shown], value) == shown:
        # Re-picking a country resets the province list, so a set value is left alone.
        return f"ok (already {shown!r}; left alone)"
    items = menu_options(page, field_id, close=False)
    names = [re.sub(r"\s+", " ", o.inner_text()).strip() for o in items]
    choice = best_option(names, value)
    if not choice:
        page.keyboard.press("Escape")
        return f"no option matches {value!r}; options are: {names[:60]}"
    items[names.index(choice)].click()
    page.wait_for_timeout(800)
    got = page.evaluate(FIX_JS).get(field_id, {}).get("value", "")
    return f"ok (selected {choice!r})" if got == choice else f"selection didn't stick (shows {got!r})"


def select_search(page: Page, field_id: str, value: str) -> str:
    if page.locator(f'[data-iiq-toggle="{field_id}"]').count():
        return select_option(page, field_id, value)
    return ef.select_search(page, field_id, value)


# Buttons that add a repeating entry ("Add another", "+ Add education").
ADD_JS = r"""
() => {
  const out = [];
  let n = 0;
  document.querySelectorAll('[data-iiq-btn]').forEach(e => e.removeAttribute('data-iiq-btn'));
  for (const b of document.querySelectorAll('button, a[role="button"], [role="button"]')) {
    const t = (b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!b.offsetWidth || !/^\+?\s*add\b/i.test(t) || t.length > 60) continue;
    b.dataset.iiqBtn = 'b' + (++n);
    out.push({id: b.dataset.iiqBtn, label: t});
  }
  return out;
}
"""


def buttons(page: Page) -> list:
    return page.locator("button:visible, input[type=submit]:visible, input[type=button]:visible, "
                        "a[role='button']:visible, [role='button']:visible").all()


def button_text(b) -> str:
    return re.sub(r"\s+", " ", (b.inner_text() or b.get_attribute("value") or b.get_attribute("aria-label") or "")).strip()


def step_name(page: Page) -> str:
    heads = [h.strip() for h in page.locator("h1:visible, h2:visible, legend:visible").all_inner_texts() if h.strip()]
    return heads[0][:80] if heads else "Application"


def read_page(page: Page) -> dict:
    page.wait_for_timeout(1500)
    fields = page.evaluate(ef.READ_FIELDS_JS)
    guesses = page.evaluate(LABEL_JS)
    fixes = page.evaluate(FIX_JS)
    for f in fields:
        fix = fixes.get(f["id"], {})
        if fix.get("menu"):
            f["value"] = fix["value"]
            f["options"] = [re.sub(r"\s+", " ", o.inner_text()).strip() for o in menu_options(page, f["id"])][:60]
        elif "options" in fix:
            f["options"], f["value"] = fix["options"], fix["value"]
        if f["kind"] == "select" and "options" not in f:
            f["options"] = ef.combobox_options(page, f["id"])[:60]
        f.pop("list", None)
        weak = not f["label"] or f["label"] == "Resume/CV" or re.fullmatch(r"[\w\[\].-]+", f["label"] or "")
        if weak and guesses.get(f["id"]):
            f["label"] = guesses[f["id"]]
        if f["kind"] == "file":
            f["label"] = (guesses.get(f["id"]) or "File upload")[:200]
            f["value"] = ", ".join(field(page, f["id"]).evaluate("e => [...e.files].map(x => x.name)"))
    if password_page(page):
        fields = [f for f in fields if "password" not in f["label"].lower()]
    return {"step": step_name(page), "fields": fields, "add_buttons": page.evaluate(ADD_JS),
            "errors": sorted(set(page.evaluate(ef.ERRORS_JS)))[:15]}


def choose_radio(page: Page, field_id: str, value: str) -> str:
    """Radio labels may be <label for>, a wrapping <label>, or the value attribute."""
    name = field(page, field_id).get_attribute("name")
    group = page.locator(f'input[type="radio"][name="{name}"]').all()
    labels = [r.evaluate("""r => {
        const l = (r.id && document.querySelector(`label[for="${CSS.escape(r.id)}"]`)) || r.closest('label');
        return (l ? l.innerText : (r.getAttribute('aria-label') || r.value || '')).replace(/\\s+/g, ' ').trim(); }""")
              for r in group]
    choice = best_option(labels, value)
    if not choice:
        return f"no choice matches {value!r}; choices are: {labels}"
    radio = group[labels.index(choice)]
    radio.check(force=True)
    return "ok" if radio.is_checked() else f"clicked {choice!r} but it isn't selected"


def upload(page: Page, field_id: str, path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    box = field(page, field_id)
    held = box.evaluate("e => [...e.files].map(x => x.name)")
    if held and not box.get_attribute("multiple"):
        return f"that box already holds {held}; not replacing it with {name}"
    box.set_input_files(path)
    for _ in range(10):
        page.wait_for_timeout(1500)
        if name.rsplit(".", 1)[0] in page.locator("body").inner_text() or \
                name in (field(page, field_id).evaluate("e => [...e.files].map(x => x.name)") if field(page, field_id).count() else []):
            return (f"ok ({name} uploaded). Some sites parse a resume and re-fill fields: "
                    "call read_page and check them.")
    return "upload didn't show up on the page; do NOT upload again, call record_unsure instead"


def press_add(page: Page, button_id: str) -> str:
    button = page.locator(f'[data-iiq-btn="{button_id}"]')
    if not button.count():
        return "that button is gone; read the page again"
    button.click()
    page.wait_for_timeout(2000)
    return "ok"


# ---------- moving through the application ----------

def visible_inputs(page: Page) -> int:
    return page.locator("input:visible:not([type=hidden]):not([type=search]), textarea:visible, select:visible").count()


def password_page(page: Page) -> bool:
    return page.locator("input[type=password]:visible").count() > 0


def open_application(page: Page, job_url: str) -> None:
    page.goto(job_url, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    wait_out_bot_check(page, "opening the job page")
    heads = page.locator("h1:visible").all_inner_texts()
    _job_title["text"] = heads[0].strip() if heads else page.title().split(" | ")[0].split(" - ")[0].strip()
    if visible_inputs(page) >= 3 or is_review(page) or password_page(page):
        return  # the form (or a sign-in) is already on the page
    apply = [b for b in page.locator("a:visible, button:visible").all() if APPLY_TEXT.match(button_text(b))]
    if not apply:
        raise Blocked("No Apply button on the job page (closed posting, or the site needs its own driver)")
    href = apply[0].get_attribute("href")
    if href and not href.startswith(("#", "javascript")) and apply[0].get_attribute("target") == "_blank":
        page.goto(page.url.split("#")[0] if href.startswith("?") else href, wait_until="domcontentloaded")
    else:
        apply[0].click()
    page.wait_for_timeout(6000)
    if not (captcha_box(page) and visible_inputs(page) >= 3):
        wait_out_bot_check(page, "opening the application")  # a box inside the form waits for submit()


# ---------- "I'm not a robot" box inside the form ----------
# Some forms (BambooHR) put a reCAPTCHA checkbox next to Submit. Ali ticks it himself, and
# a tick expires after about two minutes, so it's only asked for when he taps Submit.

def captcha_box(page: Page):
    for frame in page.frames:
        if "recaptcha/api2/anchor" in frame.url or ("hcaptcha" in frame.url and "checkbox" in frame.url):
            element = frame.frame_element()
            if element.is_visible():
                return frame
    return None


def captcha_ticked(page: Page) -> bool:
    frame = captcha_box(page)
    if frame is None:
        return True
    box = frame.locator("#recaptcha-anchor, #checkbox").first
    return box.count() > 0 and box.get_attribute("aria-checked") == "true"


def card_notes(page: Page) -> list[str]:
    if captcha_box(page) is None:
        return []
    return ["**🤖 This form has an \"I'm not a robot\" box next to Submit.** Tick it in the InternIQ window, "
            "then tap ✅ Submit within 2 minutes (a tick expires). If it isn't ticked when you tap, I'll ping you "
            "and press Submit as soon as you tick it."]


def wait_for_tick(page: Page) -> None:
    if captcha_ticked(page):
        return
    discord_bot.call(discord_bot.send(
        "DISCORD_QUESTIONS_CHANNEL_ID",
        "🤖 Approved. Now tick \"I'm not a robot\" at the bottom of the form in the InternIQ window; "
        "I press Submit right after."))
    for _ in range(180):  # up to 30 minutes
        page.wait_for_timeout(2000)
        if captcha_ticked(page):
            page.wait_for_timeout(1000)
            return
    raise Blocked("The \"I'm not a robot\" box was not ticked within 30 minutes; nothing was submitted")


def page_job_title(page: Page) -> str:
    return _job_title["text"]


def ensure_signed_in(page: Page) -> None:
    """A login or create-account page: Ali signs in (or makes the account) himself in the
    window; the agent waits until the password box is gone."""
    if not password_page(page):
        return
    discord_bot.call(discord_bot.send(
        "DISCORD_QUESTIONS_CHANNEL_ID",
        f"🔑 {page.url.split('/')[2]} wants a sign-in or a new account. Please do it in the InternIQ browser "
        "window (I don't create accounts or accept site terms for you); I'll continue once the form shows."))
    page.wait_for_function("() => ![...document.querySelectorAll('input[type=password]')].some(e => e.offsetWidth)",
                           timeout=SIGN_IN_WAIT_MS)
    page.wait_for_timeout(5000)


def next_button(page: Page):
    found = [b for b in buttons(page) if NEXT_TEXT.match(button_text(b))]
    return found[-1] if found else None


def submit_button(page: Page):
    found = [b for b in buttons(page) if SUBMIT_TEXT.match(button_text(b))]
    return found[-1] if found else None


def is_review(page: Page) -> bool:
    return submit_button(page) is not None and next_button(page) is None


def next_page(page: Page) -> str:
    if is_review(page):
        raise Blocked("Refusing to press anything on the final page; only an approved submit may")
    heading = step_name(page)
    button = next_button(page)
    if button is None:
        raise Blocked(f"No Next / Continue / Submit button on {heading!r}; this site needs a closer look")
    before = [f["label"] for f in page.evaluate(ef.READ_FIELDS_JS)]
    button.click()
    page.wait_for_timeout(5000)
    wait_out_bot_check(page, "moving to the next page")
    ensure_signed_in(page)
    after = step_name(page)
    changed = [f["label"] for f in page.evaluate(ef.READ_FIELDS_JS)] != before
    return after + " (next)" if changed and after == heading else after


def files_on_review(page: Page, expected: list[str], timeout_s: int = 10) -> list[str]:
    text = page.locator("body").inner_text()
    held = page.evaluate("() => [...document.querySelectorAll('input[type=file]')].flatMap(e => [...e.files].map(x => x.name))")
    return [n for n in expected if n.rsplit(".", 1)[0] not in text and n not in held]


def submitted(page: Page) -> bool:
    text = page.locator("body").inner_text().lower()
    return any(phrase in text for phrase in SUBMITTED_TEXT)


def submit(page: Page, posting_id: int) -> bool:
    """Press the final button once, only if Ali approved (checked here)."""
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"Application {posting_id} is not approved; refusing to submit")
    if submit_button(page) is None:
        raise Blocked("The Submit button is gone (session may have timed out)")
    wait_for_tick(page)
    button = submit_button(page)
    button.scroll_into_view_if_needed()
    button.click()
    page.wait_for_timeout(8000)
    wait_out_bot_check(page, "submitting")
    return submitted(page)


def after_submit_page(page: Page) -> dict | None:
    """After a press with no confirmation: the form's own errors stop the run; a new page
    with questions gets an Approval 2 card; anything else is left to the confirmation check."""
    snapshot = read_page(page)
    if snapshot["errors"] and submit_button(page) is not None:
        raise Blocked(f"The site refused the submit: {'; '.join(snapshot['errors'])[:300]}")
    return snapshot if snapshot["fields"] and not submitted(page) and is_review(page) else None


def confirmed_elsewhere(page: Page) -> bool:
    return submitted(page)
