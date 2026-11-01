"""The agent's browser: visible Chromium with a saved profile.

Logins (Elevate SSO, employer accounts) are kept in data/browser_profile/,
like a normal browser, so Ali only logs in once.
"""

import random
import subprocess
import time
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from interniq import config

PROFILE_DIR = config.ROOT / "data" / "browser_profile"
# Elevate runs in Ali's installed Google Chrome with its own profile: a real browser
# session at a polite pace, as the Elevate team suggested (Cloudflare turned away the
# bundled test browser). Employer sites keep the original profile and its logins.
ELEVATE_PROFILE_DIR = config.ROOT / "data" / "elevate_chrome"


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEBUG_PORT = 9333


@contextmanager
def open_browser(elevate: bool = False):
    """Yield a headed, persistent browser context and close it afterwards."""
    if elevate:
        with open_elevate_chrome() as context:
            yield context
        return
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context: BrowserContext = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        context.interniq_elevate = False  # lets callers know which browser this is
        try:
            yield context
        finally:
            context.close()


@contextmanager
def open_elevate_chrome():
    """Ali's installed Chrome, started normally (no automation flags) with a dedicated
    profile, then attached to over Chrome's debugging port. Launching Chrome through
    Playwright adds automation switches that Cloudflare turns away."""
    ELEVATE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome = subprocess.Popen([CHROME, f"--remote-debugging-port={DEBUG_PORT}",
                               f"--user-data-dir={ELEVATE_PROFILE_DIR}", "--start-maximized",
                               "--no-first-run", "--no-default-browser-check"])
    try:
        with sync_playwright() as p:
            for _ in range(30):  # wait for Chrome to open its debugging port
                try:
                    browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
                    break
                except Exception:
                    time.sleep(1)
            else:
                raise RuntimeError("Chrome didn't open its debugging port")
            context = browser.contexts[0]
            context.interniq_elevate = True
            try:
                yield context
            finally:
                browser.close()
    finally:
        chrome.terminate()


def page_of(context: BrowserContext):
    """The context's first tab (a persistent context always opens one)."""
    return context.pages[0] if context.pages else context.new_page()


def pause(low: float = 2.0, high: float = 5.0) -> None:
    """Human pace: wait a few random seconds between page loads."""
    time.sleep(random.uniform(low, high))
