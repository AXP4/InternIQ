"""The agent's browser: visible Chromium with a saved profile.

Logins (Elevate SSO, employer accounts) are kept in data/browser_profile/,
like a normal browser, so Ali only logs in once.
"""

import random
import time
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from interniq import config

PROFILE_DIR = config.ROOT / "data" / "browser_profile"


@contextmanager
def open_browser():
    """Yield a headed, persistent browser context and close it afterwards."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context: BrowserContext = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        try:
            yield context
        finally:
            context.close()


def page_of(context: BrowserContext):
    """The context's first tab (a persistent context always opens one)."""
    return context.pages[0] if context.pages else context.new_page()


def pause(low: float = 2.0, high: float = 5.0) -> None:
    """Human pace: wait a few random seconds between page loads."""
    time.sleep(random.uniform(low, high))
