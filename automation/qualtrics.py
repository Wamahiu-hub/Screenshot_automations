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
import re
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
    """Navigate to the Qualtrics category card grid."""
    if _on_card_grid(page):
        log.info("Already on the Qualtrics dashboard card grid.")
        _ensure_company_tab(page)
        return

    if config.QUALTRICS_URL:
        log.info("Navigating directly to Qualtrics URL: %s", config.QUALTRICS_URL)
        page.goto(config.QUALTRICS_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeoutError:
            pass
        time.sleep(1.0)
        if _on_card_grid(page) and not _page_seems_blank(page):
            _ensure_company_tab(page)
            return
        log.warning(
            "Direct Qualtrics URL did not show the dashboard cards (url=%s). "
            "Opening the card grid from /dashboard instead.",
            page.url,
        )

    _open_card_grid_from_dashboard(page)
    _ensure_company_tab(page)


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

def _normalize_label(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _view_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/view/(\d+)", url)
    return match.group(1) if match else None


def _card_titles_visible(page: Page) -> bool:
    try:
        return page.locator(sel.DASHBOARD_CARD_TITLE_CSS).first.is_visible(timeout=800)
    except Exception:  # noqa: BLE001
        return False


def _on_card_grid(page: Page) -> bool:
    """True when the Qualtrics category card grid is what's on screen."""
    if "/view/" in (page.url or ""):
        return False
    return _card_titles_visible(page)


def _inside_dashboard(page: Page) -> bool:
    if "/view/" in (page.url or ""):
        return True
    try:
        return page.locator(
            f"{sel.DASHBOARD_VIEW_CSS} {sel.DASHBOARD_TITLE_CSS}"
        ).first.is_visible(timeout=500)
    except Exception:  # noqa: BLE001
        return False


def _visible_card_titles(page: Page) -> list[str]:
    titles: list[str] = []
    loc = page.locator(sel.DASHBOARD_CARD_TITLE_CSS)
    try:
        count = loc.count()
    except Exception:  # noqa: BLE001
        return titles
    for i in range(count):
        try:
            text = " ".join((loc.nth(i).inner_text(timeout=1_000) or "").split())
        except Exception:  # noqa: BLE001
            continue
        if text:
            titles.append(text)
    return titles


def _ensure_company_tab(page: Page) -> None:
    """Select the Company tab on the category page when it isn't already."""
    tab = page.get_by_role("tab", name=sel.COMPANY_TAB_NAME, exact=True).first
    try:
        tab.wait_for(state="visible", timeout=1_000)
    except PWTimeoutError:
        return
    except Exception:  # noqa: BLE001
        return
    selected = (tab.get_attribute("aria-selected") or "").lower()
    if selected == "true":
        return
    log.info("Selecting the '%s' tab on the dashboard card grid.", sel.COMPANY_TAB_NAME)
    tab.click()
    try:
        page.locator(sel.DASHBOARD_CARD_TITLE_CSS).first.wait_for(
            state="visible", timeout=10_000
        )
    except PWTimeoutError:
        pass
    time.sleep(0.4)


def _find_back_button(page: Page):
    for css in sel.DASHBOARD_BACK_BUTTON_SELECTORS:
        loc = page.locator(css).first
        try:
            loc.wait_for(state="visible", timeout=2_000)
            return loc
        except PWTimeoutError:
            continue
        except Exception:  # noqa: BLE001
            continue
    return None


def _go_back_to_card_grid(page: Page) -> bool:
    """Click the dashboard Go Back button (angle-left) to return to the cards."""
    log.info("Clicking Go Back to return to the dashboard cards.")
    button = _find_back_button(page)
    if button is None and _inside_dashboard(page):
        # After impersonation the dashboard chrome can finish painting a
        # moment after the URL is already on /view/<id>.
        log.info("Go Back is not visible yet; waiting for the dashboard chrome.")
        page.wait_for_timeout(2_000)
        button = _find_back_button(page)
    if button is None:
        log.info("Go Back button is not visible.")
        return False

    button.click(timeout=5_000)
    try:
        page.wait_for_function(
            "() => !location.pathname.includes('/view/')",
            timeout=15_000,
        )
    except PWTimeoutError:
        log.warning("URL still contains /view/ after Go Back: %s", page.url)
    try:
        page.locator(sel.DASHBOARD_CARD_TITLE_CSS).first.wait_for(
            state="visible", timeout=15_000
        )
    except PWTimeoutError:
        return False
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except PWTimeoutError:
        pass
    return _on_card_grid(page)


def _open_card_grid_from_dashboard(page: Page) -> None:
    """Land on /dashboard, then open the Qualtrics category with an in-app click.

    Direct page.goto() of /categories/356/view/<id> (and sometimes of the
    category URL itself) serves a lazy JS chunk as text/html after the
    4.1.6 release, which leaves a blank page. Entering through /dashboard
    and clicking keeps Angular on already-loaded modules.
    """
    log.info("Opening the Qualtrics card grid via /dashboard.")
    page.goto("https://aiq.cerebriai.com/dashboard", wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        pass
    time.sleep(1.5)

    if _on_card_grid(page):
        return

    if not _click_card(page, "Qualtrics", "Qualtrics category card", timeout_ms=15_000):
        link = page.locator("a[href='/categories/356']").first
        try:
            link.wait_for(state="visible", timeout=3_000)
            link.click(timeout=5_000)
            log.info("Opened Qualtrics via the sidebar link.")
        except (PWTimeoutError, Exception):  # noqa: BLE001
            raise AutomationError(
                "Could not open the Qualtrics category from /dashboard "
                f"(url={page.url})."
            )

    try:
        page.wait_for_url("**/categories/356**", timeout=15_000)
    except PWTimeoutError:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        pass
    try:
        page.locator(sel.DASHBOARD_CARD_TITLE_CSS).first.wait_for(
            state="visible", timeout=15_000
        )
    except PWTimeoutError:
        pass
    time.sleep(0.8)
    if not _on_card_grid(page):
        raise AutomationError(
            "Qualtrics card grid did not render after leaving /dashboard. "
            f"URL: {page.url}. Cards seen: {_visible_card_titles(page)}."
        )


def _ensure_card_grid(page: Page) -> None:
    """Make sure the five Qualtrics dashboard cards are on screen."""
    if _inside_dashboard(page):
        if _go_back_to_card_grid(page):
            return
        log.info(
            "Go Back did not return to the card grid (url=%s). "
            "Re-opening it from /dashboard.",
            page.url,
        )
    if _on_card_grid(page):
        return
    _open_card_grid_from_dashboard(page)


def _click_dashboard_card(
    page: Page, labels: Iterable[str], timeout_ms: int = 12_000
) -> bool:
    """Click the category card whose title matches one of ``labels``."""
    wanted = {_normalize_label(label) for label in labels}
    titles = page.locator(sel.DASHBOARD_CARD_TITLE_CSS)
    try:
        titles.first.wait_for(state="visible", timeout=timeout_ms)
    except PWTimeoutError:
        titles = None

    if titles is not None:
        page.wait_for_timeout(300)
        count = titles.count()
        for i in range(count):
            title = titles.nth(i)
            try:
                text = _normalize_label(title.inner_text(timeout=2_000))
            except Exception:  # noqa: BLE001
                continue
            if text not in wanted:
                continue
            card = title.locator("xpath=ancestor::app-dashboard-card[1]")
            target = card if card.count() else title
            try:
                target.scroll_into_view_if_needed(timeout=3_000)
            except PWTimeoutError:
                pass
            target.click(timeout=5_000)
            log.info("Clicked dashboard card '%s'.", text)
            return True

    for label in labels:
        if _click_card(page, label, f"'{label}' card", timeout_ms=2_500):
            return True
    return False


def _dashboard_ready_for_capture(
    page: Page, labels: Iterable[str], view_id: str | None
) -> bool:
    """True when this dashboard actually rendered, not a blank shell."""
    if _page_seems_blank(page):
        return False
    if view_id and f"/view/{view_id}" not in (page.url or ""):
        log.warning(
            "Expected /view/%s but the URL is %s.", view_id, page.url
        )
        return False
    try:
        title_el = page.locator(sel.DASHBOARD_TITLE_CSS).first
        title_el.wait_for(state="visible", timeout=8_000)
        title = _normalize_label(title_el.inner_text())
    except Exception:  # noqa: BLE001
        log.warning("Dashboard title did not appear (url=%s).", page.url)
        return False
    if not title:
        return False
    normalized_labels = [_normalize_label(label) for label in labels]
    if normalized_labels and not any(
        label in title or title in label for label in normalized_labels
    ):
        log.warning(
            "Dashboard title '%s' does not match %s.", title, list(labels)
        )
        return False
    return True


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
        f"app-dashboard-card:has-text({label!r})",
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


def open_dashboard(page: Page, dashboard: dict) -> None:
    """Open a dashboard by clicking its card on the Qualtrics category page.

    Direct ``page.goto`` of ``/categories/356/view/<id>`` renders a blank
    shell after the AIQ 4.1.6 release (the lazy route chunk is served as
    HTML). Clicking the card stays inside the Angular app, which is the
    same path that still renders Spend Overview and Trips Statistics.
    Between dashboards we click the angle-left Go Back button so the next
    card is chosen from the grid.
    """
    canonical = dashboard["name"]
    labels: list[str] = list(dashboard["ui_labels"])
    view_id = _view_id_from_url(dashboard.get("url"))
    last_error: AutomationError | None = None

    for attempt in (1, 2):
        log.info(
            "Opening dashboard '%s' by clicking its card (attempt %d).",
            canonical, attempt,
        )
        try:
            _ensure_card_grid(page)
            _ensure_company_tab(page)
            if not _click_dashboard_card(page, labels):
                seen = _visible_card_titles(page)
                raise AutomationError(
                    f"Could not find a card for '{canonical}' "
                    f"(labels {labels}). Cards on screen: {seen or '(none)'}. "
                    f"URL: {page.url}."
                )
            if view_id:
                try:
                    page.wait_for_url(f"**/view/{view_id}**", timeout=20_000)
                except PWTimeoutError:
                    log.warning(
                        "URL did not become /view/%s (now %s).",
                        view_id, page.url,
                    )
            _wait_for_dashboard_ready(page)
            if _dashboard_ready_for_capture(page, labels, view_id):
                log.info(
                    "Dashboard '%s' is open at %s.", canonical, page.url
                )
                return
            raise AutomationError(
                f"Dashboard '{canonical}' did not render "
                f"(url={page.url})."
            )
        except AutomationError as exc:
            last_error = exc
            log.warning("%s", exc)
            if attempt == 1:
                try:
                    _open_card_grid_from_dashboard(page)
                except AutomationError as grid_exc:
                    last_error = grid_exc
                    log.warning("%s", grid_exc)

    err_dir = Path("output/test/errors")
    err_dir.mkdir(parents=True, exist_ok=True)
    diag = err_dir / f"open_failed_{canonical.replace(' ', '_')}.png"
    try:
        page.screenshot(path=str(diag), full_page=False)
    except Exception:  # noqa: BLE001
        pass
    raise AutomationError(
        f"{last_error} Diagnostic screenshot: {diag}."
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
