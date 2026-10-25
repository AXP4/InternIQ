"""Recognizes employer application sites and checks that links still work."""

import re

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from interniq import browser, db
from interniq.elevate import clean_title

# (text found in the URL or page, site name). Order matters: first match wins.
SITE_MARKERS = [
    ("myworkdayjobs", "workday"),
    ("myworkdaysite", "workday"),
    ("icims.com", "icims"),
    ("successfactors", "successfactors"),
    ("sapsf.", "successfactors"),
    ("jobs.sap.com", "successfactors"),
    ("greenhouse.io", "greenhouse"),
    ("oraclecloud.com", "oracle"),
    ("taleo.net", "taleo"),
    ("ultipro.", "ultipro"),
    ("ukg.net", "ultipro"),
    ("eightfold.ai", "eightfold"),
    ("raise.jobs", "raise"),
    ("lever.co", "lever"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("bamboohr.com", "bamboohr"),
    ("jobvite.com", "jobvite"),
    ("dayforcehcm.com", "dayforce"),
    ("adp.com", "adp"),
    ("tal.net", "oleeo"),
    ("avature", "avature"),
]

# Phrases that mean the posting is gone (or a not found page), even when the server says 200 OK.
# A redirect to a search page is NOT on this list: the job may only have moved.
DEAD_PHRASES = [
    "no longer available",
    "no longer accepting",
    "no longer open",
    "position has been filled",
    "job not found",
    "page not found",
    "page you are looking for doesn't exist",
    "page you are looking for does not exist",
    "job has expired",
    "posting has expired",
    "this job is closed",
    "posting is closed",
    "posting has closed",
]


# Postings that are not applied to through the employer link, so the link isn't checked.
NO_LINK_PROCEDURES = {
    "Employer Email": "applied by email; link not checked",
    "Accumulate Online": "applied inside Elevate; link not checked",
}

# Final URLs that mean the site is down for maintenance (try again later).
MAINTENANCE_MARKERS = ["maintenance"]

# Final URLs that mean the job page redirected to a general search page.
SEARCH_PAGE_MARKERS = ["searchjobs", "search-jobs", "/search?", "/jobs/search"]


def detect_site(*texts: str) -> str:
    """Name the application system from the first text (URL or HTML) that matches."""
    for text in texts:
        for marker, site in SITE_MARKERS:
            if marker in (text or "").lower():
                return site
    return "custom"


# Keyword search boxes on careers sites, most specific first. Filter dropdowns are also
# type="search" on some sites ("Select an option"), so those come last and are excluded.
SEARCH_BOXES = [
    'input[name*="keyword" i]', 'input[id*="keyword" i]', 'input[name="search" i]', 'input[name="q"]',
    'input[placeholder*="search" i]', 'input[aria-label*="search" i]',
    'input[type="search"]:not([placeholder*="select" i])',
]
LINKS_JS = "() => [...document.querySelectorAll('a[href]')].map(a => ({text: a.innerText, href: a.href}))"


def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def find_job_link(tab: Page, title: str, city: str) -> tuple[str | None, list[str]]:
    """Look for the job by title on the careers page we landed on.

    First check the links already on the page, then try the site's search box.
    Returns (link whose text or URL also mentions the city, every same title link).
    Many employers post one title in several cities, so a title match alone is not enough.
    """
    wanted, city = words(title), words(city)
    if not wanted:
        return None, []

    def matches() -> list[dict]:
        return [link for link in tab.evaluate(LINKS_JS) if wanted in words(link["text"])]

    found = matches()
    box = next((tab.locator(s).first for s in SEARCH_BOXES if tab.locator(s).first.is_visible()), None)
    if not found and box:
        try:
            box.fill(title)
            box.press("Enter")
            tab.wait_for_timeout(5000)
            found = matches()
        except PlaywrightError:
            pass
    in_city = next((f["href"] for f in found if city and city in words(f["text"] + " " + f["href"])), None)
    return in_city, [f["href"] for f in found]


def check_link(context: BrowserContext, url: str, title: str = "", location: str = "") -> dict:
    """Open the link once in a new tab.

    Returns site, link_status (ok, dead, verify, down, error), final_url, a note
    saying why a link was flagged, and new_link when the job moved to a new URL.
    Only a 404 or a page that clearly says the job is gone counts as dead. A
    redirect to a search page means: search for the job by title, else verify.
    """
    tab = context.new_page()
    try:
        response = tab.goto(url, wait_until="domcontentloaded", timeout=45_000)
        tab.wait_for_timeout(4000)  # let JavaScript job boards render
        final = tab.url
        text = tab.locator("body").inner_text().lower()
        phrase = next((p for p in DEAD_PHRASES if p in text), None)

        status, note, new_link = "ok", None, None
        if any(m in final.lower() for m in MAINTENANCE_MARKERS):
            status, note = "down", "site is down for maintenance"
        elif response is not None and response.status >= 400:
            status, note = "dead", f"HTTP {response.status}"
        elif phrase:
            status, note = "dead", f'page says "{phrase}"'
        elif any(m in final.lower() for m in SEARCH_PAGE_MARKERS) and final != url:
            city = (location or "").split(",")[0]
            new_link, same_title = find_job_link(tab, clean_title(title), city)
            if new_link:
                note = "job moved; found it on the careers site by title and city"
            elif same_title:
                status = "verify"
                note = f"redirected to the job search page; same title found but not in {city}: {same_title[0]}"
            else:
                status, note = "verify", "redirected to the job search page and the title wasn't found; check manually"
        return {
            "site": detect_site(url, final, tab.content()),
            "link_status": status,
            "final_url": new_link or final,
            "new_link": new_link,
            "note": note,
        }
    except Exception as e:  # bad URL, timeout, DNS failure
        return {"site": detect_site(url), "link_status": "error", "final_url": None, "note": str(e).splitlines()[0][:200]}
    finally:
        tab.close()


def check_links(context: BrowserContext, conn) -> list[dict]:
    """Check every unchecked link once (and retry ones that were down). Returns flagged results."""
    flagged = []
    rows = db.unchecked_links(conn)
    print(f"Checking {len(rows)} employer links...")
    for row in rows:
        if row["procedure"] in NO_LINK_PROCEDURES:
            db.save_link_check(conn, row["id"], {"site": None, "link_status": "none",
                                                 "note": NO_LINK_PROCEDURES[row["procedure"]]})
            continue
        if not row["link"]:
            db.save_link_check(conn, row["id"], {"site": None, "link_status": "none"})
            continue
        browser.pause()
        result = check_link(context, row["link"], row["title"], row["location"])
        db.save_link_check(conn, row["id"], result)
        print(f"  {row['id']}  {result['site']:<15} {result['link_status']:<5} {row['org']}  {result['note'] or ''}")
        if result["link_status"] != "ok":
            flagged.append({**result, "id": row["id"], "org": row["org"], "title": row["title"]})
    return flagged
