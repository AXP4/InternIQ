"""Drives an iCIMS application portal (AMD uses campuscanada-amd.icims.com).

iCIMS shows the portal inside an iframe (its URL has in_iframe=1), and existing users
sign in on iCIMS's central login (login.icims.com: username, then password). The same
bot check rule as everywhere: never solve one, ping Ali and wait.
"""

import re
from urllib.parse import urlparse

from playwright.sync_api import Frame, Page

from interniq import config, db, discord_bot
from interniq.applier.workday import Blocked, bot_check_visible, password_slots, wait_out_bot_check

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


def sign_in(page: Page, portal_url: str, job: str) -> None:
    """Portal login page -> accept the privacy statement (Ali approved this for AMD) ->
    email -> central iCIMS login -> password slots in order (the remembered one first)."""
    page.goto(f"{portal_url}/jobs/{job}/login", wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    frame = content(page)
    if frame.locator("#email").count():
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
        if frame.locator("#email").count() or "/login" in page.url:
            raise Blocked("The AMD portal stayed on its login page after the email step")
        return  # the portal remembered the session

    host = urlparse(portal_url).netloc
    key = f"password_slot:{host}"
    conn = db.connect()
    remembered = db.get_setting(conn, key, "")
    slots = sorted(password_slots(), key=lambda s: str(s[0]) != remembered)
    if page.locator("#username").count():
        page.locator("#username").fill(config.require("APPLY_EMAIL"))
        page.locator('button[name="action"][type="submit"]').first.click()
        page.wait_for_timeout(4000)
        wait_out_bot_check(page, "the iCIMS login (username step)")
    for slot, password in slots:
        page.locator("#password").first.wait_for(state="visible", timeout=30_000)
        page.locator("#password").first.fill(password)
        page.locator('button[name="action"][type="submit"]').first.click()
        page.wait_for_timeout(7000)
        wait_out_bot_check(page, "the iCIMS login (password step)")
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
    """Every job on the portal's dashboard (the jobs Ali has applied to), as id + title."""
    page.goto(f"{portal_url}/jobs/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(8000)
    frame = content(page)
    links = frame.evaluate("""() => [...document.querySelectorAll('a[href*="/jobs/"]')]
        .map(a => ({title: a.innerText.trim(), href: a.href})).filter(a => a.title)""")
    jobs = {}
    for link in links:
        number = job_number(link["href"])
        if number and number not in jobs and not re.search(r"login|search|intro|dashboard", link["href"]):
            jobs[number] = {"job": number, "title": link["title"]}
    return list(jobs.values())
