"""
Qualtrics section navigation for AIQ.

Responsibilities:

* Navigate to AIQ, detect authentication, allow manual login if needed.
* Get into the Qualtrics area.
* Verify the "Company" tab is selected (click it only if it isn't).
* Open a dashboard by its (possibly ambiguous) UI label.

Every selector we use lives in :mod:`automation.selectors` — do not
hard-code strings here.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeoutError

import config
from automation import selectors as sel


log = logging.getLogger(__name__)


class AutomationError(RuntimeError):
    """Raised for any hard failure that should stop the run."""


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _looks_like_auth_page(page: Page) -> bool:
    url = (page.url or "").lower()
    if any(marker in url for marker in sel.AUTH_URL_MARKERS):
        return True
    for css in sel.AUTH_REQUIRED_SELECTORS:
        try:
            if page.locator(css).first.is_visible(timeout=500):
                return True
        except PWTimeoutError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return False


def _looks_logged_in(page: Page) -> bool:
    """Return True if we can positively confirm the user is logged in."""
    if not sel.LOGGED_IN_SELECTORS:
        # We don't have a positive signal configured; caller should
        # fall back to checking that we're not on an auth page.
        return not _looks_like_auth_page(page)

    for css in sel.LOGGED_IN_SELECTORS:
        try:
            if page.locator(css).first.is_visible(timeout=500):
                return True
        except PWTimeoutError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return False


# Selectors that are only visible on the AIQ authenticated shell.
# These are the "positive" signals for `_detect_auth_state()`.
_LOGGED_IN_CHROME_SELECTORS: list[str] = [
    "input[placeholder*='Find a Dashboard' i]",
    "text=AIQ Travel Hub",
    "p-select[placeholder='Select Organization']",
    "img[alt*='AIQ' i]",
]

# Selectors that only appear on the Cerebri login/SSO screen.
# These are the "negative" signals for `_detect_auth_state()`.
_LOGIN_PAGE_SELECTORS: list[str] = [
    "input[type='password']",
    "text=Log in to CerebriAI",
    "button:has-text('Continue with Microsoft')",
    "button:has-text('Continue with Cerebri')",
    "button:has-text('Continue with SAML')",
]


def _detect_auth_state(page: Page, timeout_ms: int = 15_000) -> str:
    """Return ``"logged_in"``, ``"login_required"``, or ``"unknown"``.

    Races the AIQ authenticated-chrome selectors against the login-page
    selectors and returns whichever one lights up first. This is what
    makes the auth check robust against the transient state where the
    login page has just navigated but hasn't rendered its password input
    yet (previously we would false-positive "logged in" during that gap).
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        for css in _LOGGED_IN_CHROME_SELECTORS:
            try:
                if page.locator(css).first.is_visible(timeout=200):
                    return "logged_in"
            except PWTimeoutError:
                continue
            except Exception:  # noqa: BLE001
                continue
        for css in _LOGIN_PAGE_SELECTORS:
            try:
                if page.locator(css).first.is_visible(timeout=200):
                    return "login_required"
            except PWTimeoutError:
                continue
            except Exception:  # noqa: BLE001
                continue
        time.sleep(0.25)
    return "unknown"


def navigate_and_authenticate(page: Page) -> None:
    """Open AIQ and make sure we're authenticated before returning."""
    if "REPLACE-ME" in config.AIQ_URL:
        raise AutomationError(
            "AIQ_URL is still the placeholder. Set it in config.py before running."
        )

    log.info("Navigating to AIQ: %s", config.AIQ_URL)
    page.goto(config.AIQ_URL, wait_until="domcontentloaded")

    # Give SSO / redirects a moment to settle.
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PWTimeoutError:
        pass

    state = _detect_auth_state(page, timeout_ms=15_000)
    if state == "logged_in":
        log.info("Existing authenticated session detected.")
        return

    if state == "unknown":
        log.info(
            "Auth state ambiguous after initial load; waiting up to 20s more."
        )
        state = _detect_auth_state(page, timeout_ms=20_000)

    if state == "logged_in":
        log.info("Existing authenticated session detected (after retry).")
        return

    log.warning("Not authenticated. Manual login required.")
    print()
    print("=" * 60)
    print("  AIQ requires login.")
    print("  Please complete the login (SSO/MFA) in the Edge window.")
    print("  Automation will resume automatically once AIQ loads.")
    print("  (Up to 5 minutes to complete login.)")
    print("=" * 60)
    print()

    _wait_for_login(page, timeout_seconds=300)

    # After manual login, give the app a moment to settle again.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        pass
    time.sleep(2)  # let Angular finish hydrating the top-nav

    log.info("Manual authentication accepted. Continuing.")


def _wait_for_login(page: Page, timeout_seconds: float) -> None:
    """Poll until we see the AIQ authenticated chrome, then return.

    Uses the same positive/negative selector race as `_detect_auth_state`
    so we won't get tricked by intermediate login-flow pages.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = _detect_auth_state(page, timeout_ms=2_000)
        if state == "logged_in":
            log.info("Login detected. Continuing.")
            return
        time.sleep(1.0)
    raise AutomationError(
        "Timed out waiting for manual login to complete "
        f"({int(timeout_seconds)}s)."
    )


# ---------------------------------------------------------------------------
# Qualtrics navigation
# ---------------------------------------------------------------------------

def open_qualtrics(page: Page) -> None:
    """Navigate to the Qualtrics section."""
    if config.QUALTRICS_URL:
        log.info("Navigating directly to Qualtrics URL: %s", config.QUALTRICS_URL)
        page.goto(config.QUALTRICS_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeoutError:
            pass
        return

    log.info("Looking for a Qualtrics nav entry by name...")
    for name in sel.QUALTRICS_NAV_NAMES:
        loc = _first_visible(
            page.get_by_role("link", name=name, exact=False),
            page.get_by_role("button", name=name, exact=False),
            page.get_by_role("menuitem", name=name, exact=False),
            page.get_by_role("tab", name=name, exact=False),
        )
        if loc is not None:
            log.info("Clicking Qualtrics nav entry: %s", name)
            loc.click()
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeoutError:
                pass
            return

    raise AutomationError(
        "Could not find a Qualtrics navigation entry.\n"
        "Fix by either:\n"
        "  1. Setting config.QUALTRICS_URL to the direct URL, OR\n"
        "  2. Adding the visible label to selectors.QUALTRICS_NAV_NAMES.\n"
        "In Chrome DevTools, right-click the Qualtrics menu item, choose\n"
        "Inspect, and copy either its visible text or a unique CSS selector."
    )


# ---------------------------------------------------------------------------
# Organization selector (PrimeNG p-select "Select Organization")
# ---------------------------------------------------------------------------

def _locate_org_host(page: Page, timeout_ms: int = 30_000) -> Locator:
    """Locate the <p-select> "Select Organization" element (with fallbacks).

    We explicitly avoid matching the impersonation widget (which uses the
    class ``login-as-select``) so that when both are on the page we always
    return the org selector.
    """
    candidates: list[str] = [
        sel.ORG_SELECT_HOST_CSS,
        "p-select[placeholder*='Organization']",
        "p-select[placeholder*='organization']",
        "p-select:has(.p-select-label):not(.login-as-select)",
    ]
    deadline = time.monotonic() + timeout_ms / 1000.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        for css in candidates:
            loc = page.locator(css).first
            try:
                loc.wait_for(state="visible", timeout=1_500)
                return loc
            except PWTimeoutError as exc:
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        time.sleep(0.5)
    if last_error is not None:
        raise last_error
    raise PWTimeoutError("Organization dropdown not found")


def _read_current_org(page: Page) -> str:
    """Return the currently displayed value of the organization dropdown."""
    host = _locate_org_host(page)
    label = host.locator(sel.ORG_SELECT_LABEL_CSS).first
    label.wait_for(state="attached", timeout=5_000)
    aria = (label.get_attribute("aria-label") or "").strip()
    if aria:
        return aria
    return (label.inner_text() or "").strip()


def _dump_debug_screenshot(page: Page, tag: str) -> None:
    try:
        out = config.OUTPUT_DIRECTORY / "test" / "errors"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{tag}.png"
        page.screenshot(path=str(path), full_page=True)
        log.info("Wrote diagnostic screenshot: %s", path)
    except Exception:  # noqa: BLE001
        log.exception("Failed to write diagnostic screenshot for %s", tag)


def ensure_organization_selected(page: Page, organization: str) -> None:
    """
    Verify the "Select Organization" p-select shows ``organization``.

    If it doesn't, open the dropdown and pick that organization.
    """
    log.info("Verifying organization dropdown is set to '%s'...", organization)

    try:
        current = _read_current_org(page)
    except PWTimeoutError as exc:
        _dump_debug_screenshot(page, "org_select_not_found")
        raise AutomationError(
            "Could not find the 'Select Organization' dropdown on the "
            "current page. A diagnostic screenshot was written to "
            "output/test/errors/org_select_not_found.png. Inspect the page "
            "in DevTools and update selectors.ORG_SELECT_HOST_CSS to match "
            "the real <p-select> element."
        ) from exc

    if current.strip().lower() == organization.strip().lower():
        log.info("Organization is already '%s'.", current)
        return

    log.info("Organization is '%s'; switching to '%s'.", current, organization)

    host = page.locator(sel.ORG_SELECT_HOST_CSS).first
    trigger = host.locator(sel.ORG_SELECT_TRIGGER_CSS).first
    try:
        trigger.wait_for(state="visible", timeout=3_000)
        trigger.click()
    except PWTimeoutError:
        host.locator(sel.ORG_SELECT_LABEL_CSS).first.click()

    # Wait for the listbox overlay to render.
    time.sleep(0.4)

    option = page.get_by_role("option", name=organization, exact=False).first
    try:
        option.wait_for(state="visible", timeout=5_000)
    except PWTimeoutError:
        option = page.locator(sel.ORG_SELECT_OPTION_CSS, has_text=organization).first
        option.wait_for(state="visible", timeout=5_000)

    option.click()

    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PWTimeoutError:
        pass
    time.sleep(0.5)

    current = _read_current_org(page)
    if current.strip().lower() != organization.strip().lower():
        raise AutomationError(
            f"Tried to select organization '{organization}' but the "
            f"dropdown still shows '{current}'."
        )
    log.info("Organization is now '%s'.", current)


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------

# AIQ views that return a broken lazy-loaded JS chunk (MIME "text/html") when
# navigated to directly with page.goto(). These MUST be reached via in-SPA
# navigation: load "/" first (which redirects to /categories/356 and renders
# the dashboard tiles), then click the tile with matching text.
#
# Confirmed with automation/_probe_trip_stats.py: /view/1896 (Trip Statistics)
# and /categories/356 both fail with:
#   "Failed to load module script: Expected a JavaScript-or-Wasm module script
#    but the server responded with a MIME type of 'text/html'."
_DIRECT_NAV_BROKEN_URL_MARKERS: tuple[str, ...] = (
    "/view/1896",  # Trip Statistics
)

# The AIQ Qualtrics category listing (which "/" redirects to) shows the tile
# for view/1896 with the label "Trips Statistics" (plural). Include both
# spellings for defensiveness.
_TRIP_STATS_TILE_LABELS: tuple[str, ...] = ("Trips Statistics", "Trip Statistics")


def _url_needs_spa_bootstrap(direct_url: str | None) -> bool:
    if not direct_url:
        return False
    return any(m in direct_url for m in _DIRECT_NAV_BROKEN_URL_MARKERS)


def _click_card(
    page: Page, label: str, description: str, timeout_ms: int = 15_000
) -> bool:
    """Try to click a ``.category-card`` whose text contains ``label``.

    Returns True on success. Uses ``scroll_into_view_if_needed`` because AIQ's
    category cards live inside a grid that can extend below the fold, and
    Playwright refuses to click elements outside the viewport.
    """
    selectors = (
        f".category-card:has-text({label!r})",
        f"[data-cy='category-card']:has-text({label!r})",
        f".category-card-wrapper:has-text({label!r})",
    )
    for sel_str in selectors:
        try:
            loc = page.locator(sel_str).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            try:
                loc.scroll_into_view_if_needed(timeout=3_000)
            except PWTimeoutError:
                pass
            loc.click(timeout=5_000)
            log.info("Clicked %s via '%s'.", description, sel_str)
            return True
        except PWTimeoutError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return False


def _open_dashboard_via_tile(page: Page, canonical: str, tile_labels) -> None:
    """Land on /dashboard, click Qualtrics -> click the dashboard tile.

    Used for AIQ 4.1.4 routes whose lazy JS chunk is served with the wrong
    MIME type on direct navigation (e.g. /view/1896, /categories/356). By
    entering through /dashboard (always renders correctly) and using in-SPA
    tile clicks, Angular's router reuses already-loaded modules and never
    re-fetches the broken chunk.
    """
    log.info(
        "Opening dashboard '%s' via SPA bootstrap: /dashboard -> Qualtrics tile -> tile.",
        canonical,
    )
    page.goto("https://aiq.cerebriai.com/dashboard",
              wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        pass
    time.sleep(2.0)

    # If /dashboard already redirected us into /categories/356 (some user
    # roles land there by default), skip the Qualtrics-tile click.
    if "/categories/356" not in page.url:
        if not _click_card(page, "Qualtrics", "Qualtrics category card",
                           timeout_ms=15_000):
            raise AutomationError(
                f"Could not click the Qualtrics category card on /dashboard "
                f"to reach /categories/356 (needed to open '{canonical}')."
            )
        try:
            page.wait_for_url("**/categories/356", timeout=15_000)
        except PWTimeoutError:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeoutError:
            pass
        time.sleep(2.0)

    # Now click the target dashboard tile.
    for label in tile_labels:
        if _click_card(page, label, f"'{canonical}' tile", timeout_ms=10_000):
            _wait_for_dashboard_ready(page)
            return

    # Last-ditch: raw text click (in case AIQ changes tile CSS).
    for label in tile_labels:
        try:
            loc = page.get_by_text(label, exact=False).first
            loc.wait_for(state="visible", timeout=5_000)
            loc.scroll_into_view_if_needed(timeout=3_000)
            loc.click(timeout=5_000)
            _wait_for_dashboard_ready(page)
            return
        except (PWTimeoutError, Exception):  # noqa: BLE001
            continue

    # Save a diagnostic screenshot to help the operator inspect the DOM.
    err_dir = Path("output/test/errors")
    err_dir.mkdir(parents=True, exist_ok=True)
    diag = err_dir / f"tile_click_failed_{canonical.replace(' ', '_')}.png"
    try:
        page.screenshot(path=str(diag), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    raise AutomationError(
        f"Could not find/click the '{canonical}' tile after landing on "
        f"/categories/356. URL at time of failure: {page.url}. "
        f"Tried labels: {list(tile_labels)}. Diagnostic screenshot: {diag}."
    )


def open_dashboard(page: Page, dashboard: dict) -> None:
    """Open a dashboard, preferring its direct URL if configured."""
    canonical = dashboard["name"]
    labels: list[str] = dashboard["ui_labels"]
    direct_url = dashboard.get("url")

    # Route around the AIQ 4.1.4 lazy-chunk MIME bug for known-broken URLs
    # by using an in-SPA tile click instead of a direct page.goto().
    if _url_needs_spa_bootstrap(direct_url):
        _open_dashboard_via_tile(
            page, canonical, _TRIP_STATS_TILE_LABELS if "1896" in direct_url
            else tuple(labels)
        )
        return

    if direct_url:
        # Occasionally AIQ returns an empty shell for a dashboard (blank
        # background, no chrome, no widgets) — a transient backend issue.
        # Retry the navigation once if we detect that state.
        for attempt in (1, 2):
            log.info(
                "Opening dashboard '%s' via direct URL (attempt %d): %s",
                canonical, attempt, direct_url,
            )
            page.goto(direct_url, wait_until="domcontentloaded")
            _wait_for_dashboard_ready(page)
            if not _page_seems_blank(page):
                return
            log.warning(
                "Dashboard '%s' rendered as a blank page (no AIQ chrome). %s",
                canonical,
                "Retrying once..." if attempt == 1 else "Giving up; continuing.",
            )
            if attempt == 1:
                time.sleep(2)
        return

    log.info("Opening dashboard: %s (labels tried: %s)", canonical, labels)

    css_fallback = sel.DASHBOARD_CSS_FALLBACKS.get(canonical)
    if css_fallback:
        loc = page.locator(css_fallback).first
        try:
            loc.wait_for(state="visible", timeout=2_000)
            loc.click()
            _wait_for_dashboard_ready(page)
            return
        except PWTimeoutError:
            pass

    for label in labels:
        for role in sel.DASHBOARD_ROLE_CANDIDATES:
            loc = page.get_by_role(role, name=label, exact=False).first
            try:
                loc.wait_for(state="visible", timeout=1_500)
                loc.click()
                _wait_for_dashboard_ready(page)
                log.info("Dashboard '%s' opened via role=%s, label='%s'.",
                         canonical, role, label)
                return
            except PWTimeoutError:
                continue
            except Exception:  # noqa: BLE001
                continue

        # Last-ditch: raw text match
        loc = page.get_by_text(label, exact=False).first
        try:
            loc.wait_for(state="visible", timeout=1_500)
            loc.click()
            _wait_for_dashboard_ready(page)
            log.info("Dashboard '%s' opened via text match '%s'.",
                     canonical, label)
            return
        except PWTimeoutError:
            continue

    raise AutomationError(
        f"Could not find dashboard '{canonical}' with any of the labels "
        f"{labels}.\n"
        "Fix in Chrome DevTools:\n"
        "  1. Right-click the dashboard tile/link and choose Inspect.\n"
        "  2. Note its exact visible text and, if it has one, its role.\n"
        "  3. Update config.DASHBOARDS ui_labels if the visible text differs.\n"
        "  4. Or paste a unique CSS selector into "
        "selectors.DASHBOARD_CSS_FALLBACKS keyed by the canonical name."
    )


_BLANK_PAGE_CHECK_JS = """
() => {
    const elCount = document.querySelectorAll('*').length;
    const bodyText = document.body ? (document.body.innerText || '') : '';
    return {
        elCount: elCount,
        textLen: bodyText.trim().length,
        hasHeader: !!document.querySelector(
            "img[alt*='AIQ' i], img[alt*='Qualtrics' i], header, nav, [class*='header' i], [class*='top-nav' i]"
        ),
    };
}
"""


def _page_seems_blank(page: Page) -> bool:
    """Return True if the current page has no AIQ chrome / almost no DOM.

    A working AIQ dashboard easily has thousands of DOM elements and shows
    a header/nav bar. A broken navigation typically leaves the browser on
    a page with <100 elements, no visible text, and no header.
    """
    try:
        info = page.evaluate(_BLANK_PAGE_CHECK_JS)
    except Exception:  # noqa: BLE001
        return False
    if info.get("hasHeader"):
        return False
    return info.get("elCount", 0) < 200 and info.get("textLen", 0) < 40


def _wait_for_dashboard_ready(page: Page) -> None:
    """Wait for a dashboard to become interactive after being clicked."""
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        pass

    if sel.DASHBOARD_LOADING_SPINNER:
        try:
            page.locator(sel.DASHBOARD_LOADING_SPINNER).first.wait_for(
                state="detached", timeout=20_000
            )
        except PWTimeoutError:
            log.warning("Loading spinner did not disappear within timeout.")

    if sel.DASHBOARD_READY_SELECTOR:
        try:
            page.locator(sel.DASHBOARD_READY_SELECTOR).first.wait_for(
                state="visible", timeout=15_000
            )
        except PWTimeoutError:
            log.warning("Dashboard 'ready' selector did not appear.")

    # Also block on the per-widget spinner check. This is the same check
    # used before every screenshot in automation.screenshots — running it
    # here means we don't even start capturing until every widget's data
    # fetch has resolved.
    try:
        from automation.screenshots import _wait_for_widgets_loaded  # noqa: WPS433
        _wait_for_widgets_loaded(page, timeout_ms=config.WIDGET_LOAD_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001
        log.debug("widget-load check in dashboard-ready failed: %s", exc)

    time.sleep(config.DASHBOARD_LOAD_DELAY)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_visible(*locators: Locator) -> Locator | None:
    """Return the first locator that resolves to a visible element."""
    for loc in locators:
        try:
            first = loc.first
            first.wait_for(state="visible", timeout=1_500)
            return first
        except PWTimeoutError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return None
