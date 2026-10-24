"""Recognizes employer application sites and checks that links still work."""

from playwright.sync_api import BrowserContext

from interniq import browser, db

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

# Phrases that mean the posting is gone, even when the server says 200 OK.
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


def check_link(context: BrowserContext, url: str) -> dict:
    """Open the link once in a new tab.

    Returns site, link_status (ok, dead, down, error), final_url, and a note
    saying why a link was flagged.
    """
    tab = context.new_page()
    try:
        response = tab.goto(url, wait_until="domcontentloaded", timeout=45_000)
        tab.wait_for_timeout(4000)  # let JavaScript job boards render
        final = tab.url
        text = tab.locator("body").inner_text().lower()
        phrase = next((p for p in DEAD_PHRASES if p in text), None)

        status, note = "ok", None
        if any(m in final.lower() for m in MAINTENANCE_MARKERS):
            status, note = "down", "site is down for maintenance"
        elif response is not None and response.status >= 400:
            status, note = "dead", f"HTTP {response.status}"
        elif any(m in final.lower() for m in SEARCH_PAGE_MARKERS) and final != url:
            status, note = "dead", "redirected to the job search page"
        elif phrase:
            status, note = "dead", f'page says "{phrase}"'
        return {
            "site": detect_site(url, final, tab.content()),
            "link_status": status,
            "final_url": final,
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
        if not row["link"]:
            db.save_link_check(conn, row["id"], {"site": None, "link_status": "none"})
            continue
        browser.pause()
        result = check_link(context, row["link"])
        db.save_link_check(conn, row["id"], result)
        print(f"  {row['id']}  {result['site']:<15} {result['link_status']:<5} {row['org']}  {result['note'] or ''}")
        if result["link_status"] != "ok":
            flagged.append({**result, "id": row["id"], "org": row["org"], "title": row["title"]})
    return flagged
