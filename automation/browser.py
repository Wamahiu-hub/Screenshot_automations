"""
Persistent Microsoft Edge context for the AIQ automation.

We use ``launch_persistent_context`` with ``channel="msedge"`` so cookies,
tokens, and any authenticated session state survive between runs in a real
Edge profile. That way you log in manually ONCE (including MFA/SSO), and
every subsequent run reuses the authenticated profile without touching
credentials.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

import config


log = logging.getLogger(__name__)


@contextmanager
def launch_context(
    user_data_dir: Path | None = None,
    headless: bool | None = None,
) -> Iterator[tuple[BrowserContext, Page]]:
    """
    Launch (or reuse) a persistent Edge profile and yield a (context, page).

    Usage::

        with launch_context() as (ctx, page):
            page.goto(...)
    """
    profile = Path(user_data_dir or config.BROWSER_PROFILE_DIR).resolve()
    profile.mkdir(parents=True, exist_ok=True)

    log.info("Using Edge browser profile at %s", profile)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="msedge",
            headless=bool(config.HEADLESS if headless is None else headless),
            viewport=config.VIEWPORT,
            accept_downloads=True,
            args=[
                # Avoid the automation-controlled banner triggering weird
                # anti-automation behavior in some corporate SSO flows.
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context.set_default_timeout(config.DEFAULT_TIMEOUT_MS)
        context.set_default_navigation_timeout(config.NAVIGATION_TIMEOUT_MS)

        page = context.pages[0] if context.pages else context.new_page()

        try:
            yield context, page
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                log.exception("Error while closing browser context")
