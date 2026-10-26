"""Applies to "Accumulate Online" postings inside Elevate itself.

These postings have no employer site: Elevate's APPLY button opens a small form where
the application package is built from documents in Ali's Elevate document library.
The form is always the same (package name, cover letter, resume, transcript), so no
Claude loop is needed:

1. Upload this posting's tailored resume and cover letter to the library (once).
2. Open the posting, press APPLY (it only opens the form), build a custom package.
3. Screenshot it and post the approval card; nothing is sent.
4. After Ali's Submit tap, open the form again (the session may have expired while he
   decided), build the same package, press "Submit Application", and confirm the job
   shows up under Application Submitted.
"""

import re

from playwright.sync_api import BrowserContext, Page
from pypdf import PdfWriter

from interniq import browser, db
from interniq.elevate import crawler

DOCS_URL = "/myAccount/engineering/docs-engineering.htm"
APPLICATIONS_URL = "/myAccount/engineering/applications-engineering.htm"
SUBMITTED = "Application Submitted"

# Elevate's document type ids; the apply form's dropdowns are named after them too.
# "combined" is one merged PDF (letter, resume, transcript) for postings that ask for
# everything in one document; it goes in the resume slot, the only required one.
DOC_TYPES = {"cover_letter": "30", "resume": "31", "transcript": "32", "combined": "31"}
TRANSCRIPT_NAME = "Ali_Alanizi_Transcript"
ONE_DOCUMENT = re.compile(r"\b(in|as|into) (one|a single) (document|pdf|file)\b", re.IGNORECASE)


class Blocked(Exception):
    """Elevate showed something unexpected; Ali gets a message and nothing is sent."""


def base_url(page: Page) -> str:
    return page.url.split("/myAccount")[0]


def doc_name(kind: str, posting_id: int) -> str:
    """The library name for one posting's document, e.g. Ali_Alanizi_Resume_151913."""
    if kind == "transcript":
        return TRANSCRIPT_NAME
    label = {"resume": "Resume", "cover_letter": "Cover_Letter", "combined": "Application"}[kind]
    return f"Ali_Alanizi_{label}_{posting_id}"


def merge(paths: list, out) -> None:
    """Join PDFs into one file, in order."""
    writer = PdfWriter()
    for path in paths:
        writer.append(str(path))
    with open(out, "wb") as f:
        writer.write(f)


def upload_document(page: Page, path, name: str, kind: str) -> None:
    """Add one PDF to Ali's Elevate document library."""
    page.goto(base_url(page) + DOCS_URL)
    page.wait_for_load_state()
    browser.pause()
    page.locator("button:has-text('Upload Document'):visible, a:has-text('Upload Document'):visible").first.click()
    page.wait_for_selector("input[name=docName]")
    page.fill("input[name=docName]", name)
    page.select_option("select[name=docType]", DOC_TYPES[kind])
    page.set_input_files("#fileUpload_docUpload", str(path))
    # The file goes up by itself; the hidden field gets its id when it's done.
    page.wait_for_function("() => document.querySelector('#inputFileUpload_docUpload').value !== ''",
                           timeout=60_000)
    page.wait_for_timeout(3500)  # the save button unlocks 3 seconds after a file is picked
    page.click("#submitFileUploadFormBtn")
    page.wait_for_load_state()
    page.wait_for_timeout(3000)


def open_apply_form(context: BrowserContext, page: Page, posting_id: int) -> Page:
    """Open the posting in a new tab and press APPLY, which only opens the package form."""
    crawler.ensure_logged_in(page)
    browser.pause()
    rows = crawler.open_quick_search(page)
    if not any(r["id"] == posting_id for r in rows):
        raise Blocked("the posting isn't in the ENSF list anymore (closed?)")
    with context.expect_page() as new_tab:
        page.locator(f"tr#posting{posting_id} td[data-totitle] a").first.click()
    tab = new_tab.value
    tab.wait_for_selector("text=JOB POSTING INFORMATION")
    browser.pause()
    buttons = tab.locator("button.applyButton:visible")
    if not buttons.count():
        tab.close()
        raise Blocked("the posting has no APPLY button (already applied, or applications are closed)")
    buttons.first.click()
    tab.wait_for_selector('input[name="applyOption"]')
    tab.wait_for_timeout(1500)
    return tab


def library_options(tab: Page) -> dict[str, list[str]]:
    """The documents each dropdown of the custom package offers, by kind."""
    tab.check('input[name="applyOption"][value="customPkg"]')
    tab.wait_for_timeout(1500)
    return {kind: tab.evaluate(
        "n => { const s = document.querySelector(`select[name='${n}']`);"
        " return s ? [...s.options].map(o => o.text.trim()) : null; }", type_id)
        for kind, type_id in DOC_TYPES.items()}


def build_package(tab: Page, posting_id: int, org: str, kinds: list[str]) -> dict[str, str]:
    """Fill the custom package form (never submits). Returns what each dropdown shows."""
    options = library_options(tab)
    tab.fill('input[name="name"]', f"{posting_id} {org}"[:60])
    chosen = {}
    for kind in kinds:
        name = doc_name(kind, posting_id)
        # Newest first; a re-upload with the same name shows up twice.
        label = next((o for o in options[kind] or [] if o.startswith(name + " (")), None)
        if label is None:
            raise Blocked(f"{name} isn't in the Elevate document library")
        tab.select_option(f"select[name='{DOC_TYPES[kind]}']", label=label)
        chosen[kind] = label
    # Read back what the form really holds.
    return {kind: tab.evaluate(
        "n => { const s = document.querySelector(`select[name='${n}']`); return s.options[s.selectedIndex].text.trim(); }",
        DOC_TYPES[kind]) for kind in chosen}


def submitted_jobs(page: Page) -> set[int]:
    """Job IDs listed under Application Submitted on the INTE Job Applications page."""
    crawler.ensure_logged_in(page)
    page.goto(base_url(page) + APPLICATIONS_URL)
    page.wait_for_load_state()
    browser.pause()
    page.locator(f"tr:has-text('{SUBMITTED}') a:has-text('View')").first.click()
    page.wait_for_timeout(5000)
    rows = page.evaluate("() => [...document.querySelectorAll('table tr')].map(r => r.innerText)")
    return {int(m.group(1)) for r in rows if (m := re.search(r"\b(1\d{5})\b", r))}


def submit(tab: Page, posting_id: int) -> None:
    """Press Submit Application. Refuses unless Ali approved this application."""
    app = db.get_application(db.connect(), posting_id)
    if not app or app["state"] != "approved":
        raise PermissionError(f"{posting_id} isn't approved; not submitting")
    tab.locator('input[type=submit][value="Submit Application"]').click()
    tab.wait_for_load_state()
    tab.wait_for_timeout(5000)
