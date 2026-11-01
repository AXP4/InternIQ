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
# Elevate runs in Ali's installed Google Chrome with its own profile: a real browser
# session at a polite pace, as the Elevate team suggested (Cloudflare turned away the
# bundled test browser). Employer sites keep the original profile and its logins.
ELEVATE_PROFILE_DIR = config.ROOT / "data" / "elevate_profile"


@contextmanager
def open_browser(elevate: bool = False):
    """Yield a headed, persistent browser context and close it afterwards."""
    profile = ELEVATE_PROFILE_DIR if elevate else PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context: BrowserContext = p.chromium.launch_persistent_context(
            profile,
            channel="chrome" if elevate else None,
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        context.interniq_elevate = elevate  # lets callers know which browser this is
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
