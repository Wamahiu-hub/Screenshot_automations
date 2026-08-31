"""
Impersonation flows for AIQ.

Contrary to the initial assumption, AIQ does NOT require Ctrl+Shift+A to
open a hidden popup — for admin users, a "Login As User" PrimeNG p-select
is always rendered in the top-nav (right beside the "Qualtrics" organization
dropdown). It behaves like PrimeNG's filterable select:

    * Click the host to open the overlay listbox.
    * The overlay usually contains a filter <input> at the top.
    * Typing a name filters the option list.
    * Clicking the matching option switches the session to that user.

We keep the Ctrl+Shift+A key press as a best-effort activation for
deployments where the popup is hidden until that shortcut is pressed.
"""

from __future__ import annotations

import logging
import time

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeoutError

import config


log = logging.getLogger(__name__)


class ImpersonationError(RuntimeError):
    """Raised when impersonation cannot be completed."""


# Selector for the "Switch User..." p-select that replaces "Login As User"
# once an impersonation is active.
_SWITCH_USER_HOST_SELECTORS: list[str] = [
    "p-select.login-as-select:has-text('Switch User')",
    "p-select.login-as-select",
]

# Header badge / info that indicates an active impersonation.
_IMPERSONATION_BADGE_SELECTORS: list[str] = [
    "text=IMPERSONATING",
    "[class*='impersonat' i]",
]


_EXIT_BUTTON_SELECTORS: list[str] = [
    "button[aria-label='Exit']",
    "button:has-text('Exit')",
    "[role='button'][aria-label='Exit']",
    ".impersonation-exit",
    "a:has-text('Exit')",
]


def exit_impersonation(page: Page, timeout_seconds: float = 20.0) -> None:
    """Click the top-nav 'Exit' button to leave an active impersonation.

    Silently returns if no impersonation is currently active. Raises
    :class:`ImpersonationError` if we detect an active impersonation but
    cannot click the Exit control or if the impersonation banner remains
    after clicking.
    """
    if not is_impersonation_active(page):
        return

    log.info("Active impersonation detected; clicking Exit to return to admin.")

    clicked = False
    for css in _EXIT_BUTTON_SELECTORS:
        loc = page.locator(css).first
        try:
            loc.wait_for(state="visible", timeout=2_000)
            loc.click()
            clicked = True
            break
        except PWTimeoutError:
            continue
        except Exception:  # noqa: BLE001
            continue

    if not clicked:
        _dump_debug_screenshot(page, "exit_impersonation_no_button")
        raise ImpersonationError(
            "Detected an active impersonation but couldn't find/click the "
            "'Exit' button in the top-nav. Diagnostic screenshot saved to "
            "output/test/errors/."
        )

    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        pass

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_impersonation_active(page):
            log.info("Successfully exited impersonation.")
            return
        time.sleep(0.5)

    _dump_debug_screenshot(page, "exit_impersonation_still_active")
    raise ImpersonationError(
        "Clicked 'Exit' but the impersonation banner is still visible."
    )


def is_impersonation_active(
    page: Page,
    expected_name: str | None = None,
    wait_seconds: float = 8.0,
) -> bool:
    """Return True if an impersonation session is currently active.

    Polls for up to ``wait_seconds`` because the AIQ Angular app renders the
    impersonation banner + "Switch User..." dropdown after network-idle, so
    a single-shot check can miss it on freshly-loaded pages.

    If ``expected_name`` is given, additionally verify the impersonated
    user's display name contains that string. If we can see we're
    impersonating but can't confirm the name matches, we conservatively
    return False so the caller can re-impersonate.
    """
    deadline = time.monotonic() + wait_seconds
    active = False
    while time.monotonic() < deadline and not active:
        for css in _IMPERSONATION_BADGE_SELECTORS + _SWITCH_USER_HOST_SELECTORS:
            try:
                loc = page.locator(css).first
                loc.wait_for(state="visible", timeout=500)
                active = True
                break
            except PWTimeoutError:
                continue
            except Exception:  # noqa: BLE001
                continue
        if not active:
            time.sleep(0.3)

    if not active or not expected_name:
        return active

    # Try to find the displayed name in the impersonation banner.
    try:
        loc = page.get_by_text(expected_name, exact=False).first
        loc.wait_for(state="visible", timeout=3_000)
        return True
    except PWTimeoutError:
        return False
    except Exception:  # noqa: BLE001
        return False


# Host <p-select> for "Login As User". Confirmed in AIQ DOM:
#   <p-select class="login-as-select p-component ..." optionlabel="name">
#     <span role="combobox" aria-label="Login As User">Login As User</span>
#     <div role="button" aria-label="dropdown trigger">...</div>
#   </p-select>
# It has NO `placeholder` attribute; the placeholder text is displayed as
# an inner span, and its uniquely identifying class is `login-as-select`.
_HOST_SELECTORS: list[str] = [
    "p-select.login-as-select",
    "p-select:has([aria-label='Login As User'])",
    "p-select:has-text('Login As User')",
]

# Element(s) that open the overlay when clicked. Tried in order per host.
_TRIGGER_SELECTORS: list[str] = [
    "[aria-label='dropdown trigger']",
    ".p-select-dropdown",
    ".p-select-label",
    "span[role='combobox']",
]

# Filter input rendered inside the opened overlay listbox. Optional — some
# builds render a flat listbox with no filter, in which case we skip.
_FILTER_INPUT_SELECTORS: list[str] = [
    "input.p-select-filter",
    "input.p-autocomplete-input",
    "[role='listbox'] input",
    ".p-overlay input[type='text']",
]

# Individual option elements inside the opened overlay listbox.
_OPTION_CONTAINER_SELECTORS: list[str] = [
    "[role='option']",
    ".p-select-option",
    "li[data-pc-section='option']",
    ".p-autocomplete-item",
]


def _dump_debug_screenshot(page: Page, tag: str) -> None:
    try:
        out = config.OUTPUT_DIRECTORY / "test" / "errors"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{tag}.png"
        page.screenshot(path=str(path), full_page=True)
        log.info("Wrote diagnostic screenshot: %s", path)
    except Exception:  # noqa: BLE001
        log.exception("Failed to write diagnostic screenshot for %s", tag)


def _first_visible(page: Page, css_list: list[str], timeout_ms: int) -> Locator | None:
    """Return the first CSS in ``css_list`` that becomes visible, or None."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        for css in css_list:
            loc = page.locator(css).first
            try:
                loc.wait_for(state="visible", timeout=800)
                return loc
            except PWTimeoutError:
                continue
        time.sleep(0.2)
    return None


def _first_visible_child(
    parent: Locator, css_list: list[str], timeout_ms: int
) -> Locator | None:
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        for css in css_list:
            loc = parent.locator(css).first
            try:
                loc.wait_for(state="visible", timeout=800)
                return loc
            except PWTimeoutError:
                continue
        time.sleep(0.2)
    return None


def _find_option_by_text(page: Page, text: str, timeout_ms: int) -> Locator | None:
    """Find a visible option element whose text contains ``text``."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        for css in _OPTION_CONTAINER_SELECTORS:
            loc = page.locator(css, has_text=text).first
            try:
                loc.wait_for(state="visible", timeout=800)
                return loc
            except PWTimeoutError:
                continue
        time.sleep(0.3)
    return None


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def impersonate(page: Page, user_name: str) -> None:
    """
    Impersonate ``user_name`` via the "Login As User" p-select.

    Raises :class:`ImpersonationError` on any failure, and writes a
    diagnostic screenshot to ``output/test/errors/``.
    """
    log.info("Impersonating user: '%s'", user_name)

    # 1) The "Login As User" widget is not always rendered — Ctrl+Shift+A
    #    toggles it into the DOM. Focus the page body first so the shortcut
    #    is captured by the Angular app, not the browser chrome.
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass
    try:
        page.locator("body").click(position={"x": 5, "y": 5}, timeout=2_000)
    except Exception:  # noqa: BLE001
        pass

    host = _first_visible(page, _HOST_SELECTORS, timeout_ms=1_500)
    if host is None:
        # Not yet visible → press the shortcut and wait for the p-select to
        # appear. Retry the shortcut once if the first press doesn't take.
        for attempt in (1, 2):
            page.keyboard.press("Control+Shift+A")
            time.sleep(0.6)
            host = _first_visible(page, _HOST_SELECTORS, timeout_ms=4_000)
            if host is not None:
                break
            log.warning(
                "Ctrl+Shift+A attempt %d did not surface the "
                "'Login As User' widget; retrying.", attempt,
            )

    if host is None:
        _dump_debug_screenshot(page, f"impersonate_no_host_{_slug(user_name)}")
        raise ImpersonationError(
            "Could not find the 'Login As User' <p-select> host after "
            "pressing Ctrl+Shift+A. Diagnostic screenshot saved to "
            "output/test/errors/. Update automation.impersonation."
            "_HOST_SELECTORS."
        )

    # 3) Open the dropdown by clicking its trigger.
    trigger = _first_visible_child(host, _TRIGGER_SELECTORS, timeout_ms=3_000)
    (trigger or host).click()
    time.sleep(0.4)

    # 4) If there's a filter input inside the overlay, type the name so the
    #    option list narrows to Alex Belov (or whoever we're impersonating).
    filter_input = _first_visible(page, _FILTER_INPUT_SELECTORS, timeout_ms=2_000)
    if filter_input is not None:
        try:
            filter_input.click()
        except Exception:  # noqa: BLE001
            pass
        # fill() sets the value + dispatches an "input" event synchronously.
        # It's much more reliable than type() (which simulates
        # focus/keydown/keyup and can hang on PrimeNG's Angular listeners).
        try:
            filter_input.fill(user_name)
        except Exception:  # noqa: BLE001
            try:
                filter_input.press_sequentially(user_name, delay=40)
            except Exception:  # noqa: BLE001
                log.exception(
                    "Both fill() and press_sequentially() failed on filter input"
                )
        time.sleep(0.8)
    else:
        log.info("No filter input in overlay; will search options directly.")

    # 5) Click the option matching the user name (fall back to first name).
    option = _find_option_by_text(page, user_name, timeout_ms=6_000)
    if option is None and user_name.strip():
        first = user_name.split()[0]
        option = _find_option_by_text(page, first, timeout_ms=3_000)

    if option is None:
        _dump_debug_screenshot(page, f"impersonate_no_option_{_slug(user_name)}")
        raise ImpersonationError(
            f"Opened the 'Login As User' dropdown but could not find an "
            f"option matching '{user_name}'. Diagnostic screenshot saved "
            "to output/test/errors/."
        )

    option.click()

    # 6) Give AIQ time to swap sessions and re-render the UI.
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PWTimeoutError:
        pass
    time.sleep(1.5)
    log.info("Impersonation of '%s' appears complete.", user_name)
