"""
Centralized selectors / DOM hints for the AIQ web application.

RULE: If a selector needs to change, change it HERE, nowhere else.

Every value with the prefix ``PLACEHOLDER_`` is a guess or an unknown that
must be confirmed against the real DOM using Chrome DevTools. See the
README section "How to fix a selector" for the exact steps.

Each entry documents WHY it exists and WHAT to inspect if it stops working.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Authentication detection
# ---------------------------------------------------------------------------
#
# We do NOT know the real logged-in signal for your AIQ tenant. The safest
# approach used by the automation is:
#
#   1. Navigate to config.AIQ_URL
#   2. If the URL still contains one of AUTH_URL_MARKERS (login/SSO pages),
#      or if any of AUTH_REQUIRED_SELECTORS is visible, we assume NOT logged in
#      and pause for you to log in manually.
#   3. Once you've logged in, press ENTER in the terminal to continue.
#
# If you can tell me a stable "you-are-logged-in" indicator (e.g. an avatar
# button, a user menu, a specific nav item), add it to LOGGED_IN_SELECTORS
# and the automation will detect login automatically.

AUTH_URL_MARKERS: list[str] = [
    "login",
    "signin",
    "sso",
    "adfs",
    "okta",
    "microsoftonline",
    # NOTE: "auth" is intentionally NOT in this list. AIQ's own landing URL
    # is https://aiq.cerebriai.com/auth, so treating "auth" as an
    # auth-page marker would cause every run to think we're not logged in.
    # The password-input DOM check below is the authoritative signal.
]

# Selectors whose presence means we're still on a login / SSO screen.
AUTH_REQUIRED_SELECTORS: list[str] = [
    'input[type="password"]',
]

# Selectors whose presence means we ARE logged in. Optional.
# TODO(marc): add a stable "post-login" element from AIQ, e.g. the user
# avatar or the main nav. Inspect it in DevTools and paste a CSS selector.
LOGGED_IN_SELECTORS: list[str] = [
    # e.g. 'button[aria-label="Account menu"]',
]


# ---------------------------------------------------------------------------
# Qualtrics section navigation
# ---------------------------------------------------------------------------
#
# How do we get from the AIQ landing page to the Qualtrics area?
# Preferred strategies, in order:
#
#   1. If Qualtrics has a stable direct URL, set config.QUALTRICS_URL and
#      the automation will just navigate to it.
#   2. Otherwise the automation will look for a nav link/button by name.

QUALTRICS_NAV_NAMES: list[str] = [
    "Qualtrics",
]


# ---------------------------------------------------------------------------
# Organization selector (PrimeNG p-select, "Select Organization")
# ---------------------------------------------------------------------------
#
# Inside AIQ's Qualtrics category page there's a PrimeNG dropdown (rendered
# as <p-select ...>) whose current value MUST match config.ORGANIZATION_NAME
# before we open any dashboard.
#
# Sample outerHTML observed on the real page (see project notes):
#
#   <p-select ... placeholder="Select Organization" id="pn_id_74" ...>
#     <span role="combobox" class="p-select-label ..."
#           aria-label="Qualtrics" ...>Qualtrics</span>
#     ...
#     <div role="button" aria-label="dropdown trigger" ...>
#       ...chevron svg...
#     </div>
#   </p-select>
#
# So we look for:
#   * The <p-select> host by CSS ("p-select[placeholder='Select Organization']").
#   * The current value from its inner ".p-select-label" text (or aria-label).
#   * The dropdown-trigger button by role/aria-label to open the listbox.
#   * The chosen option in the listbox by role/name.

ORG_SELECT_HOST_CSS: str = "p-select[placeholder='Select Organization']"

# The child element that displays the current value.
ORG_SELECT_LABEL_CSS: str = ".p-select-label"

# The clickable element that opens the dropdown listbox. In PrimeNG this is
# usually a sibling div with role="button" and aria-label="dropdown trigger",
# but clicking the label itself also works — we try both.
ORG_SELECT_TRIGGER_CSS: str = "[aria-label='dropdown trigger']"

# Options in the opened listbox are usually rendered as role="option" with
# their visible text; some PrimeNG builds also expose them via
# data-pc-section="option".
ORG_SELECT_OPTION_CSS: str = "[role='option'], li[data-pc-section='option']"


# ---------------------------------------------------------------------------
# Dashboard tiles / links
# ---------------------------------------------------------------------------
#
# Each dashboard is opened by clicking an element whose visible text
# matches one of its ui_labels (see config.DASHBOARDS). We try, in order,
# role="link", role="button", role="tab", then a text locator.
#
# If your build uses a totally custom element (e.g. a div with a data
# attribute), add a CSS fallback in DASHBOARD_CSS_FALLBACKS keyed by
# the canonical dashboard name.

DASHBOARD_ROLE_CANDIDATES: list[str] = ["link", "button", "tab", "menuitem"]

DASHBOARD_CSS_FALLBACKS: dict[str, str] = {
    # "Spend Overview": '[data-dashboard-id="spend-overview"]',
}


# ---------------------------------------------------------------------------
# Dashboard "loaded" signal
# ---------------------------------------------------------------------------
#
# After clicking a dashboard, how do we know it has finished loading its
# widgets? Options, in order of preference:
#
#   1. Wait for network to be idle for a moment (default).
#   2. Wait for a "loading spinner" element to DISAPPEAR.
#   3. Wait for at least one widget container to appear.
#
# TODO(marc): if AIQ shows a specific loading spinner while dashboards
# render, paste its selector into DASHBOARD_LOADING_SPINNER so we can
# wait for it to detach.

DASHBOARD_LOADING_SPINNER: str | None = None  # e.g. ".dashboard-loading, [data-loading='true']"

# A selector that (once present) indicates the dashboard body is rendered.
# Optional. If left None, we only rely on network-idle + a short settle.
DASHBOARD_READY_SELECTOR: str | None = None


# ---------------------------------------------------------------------------
# Per-widget spinners
# ---------------------------------------------------------------------------
#
# Dashboards in AIQ are composed of many independent widgets that each
# fetch their own data asynchronously and display a spinner / progress bar
# while loading. `WIDGET_LOADING_SELECTORS` is the union of every CSS
# selector we treat as "still loading". A screenshot will not be taken
# until every one of these selectors evaluates to zero visible matches
# (or WIDGET_LOAD_TIMEOUT_MS elapses).
#
# If a spinner slips through the defaults below (screenshots still show
# spinning widgets), inspect it in DevTools and add its selector here.
# Right-click the spinner → Inspect → copy its class and add e.g.
#   ".my-aiq-widget-loading"
# to this list.

WIDGET_LOADING_SELECTORS: list[str] = [
    # PrimeNG (AIQ's UI framework)
    ".p-progress-spinner",
    ".p-progressbar",
    # ARIA standard "this region is currently updating"
    "[aria-busy='true']",
    # Angular Material (defensive; AIQ may embed some MDC components)
    "mat-spinner",
    "mat-progress-spinner",
    "mat-progress-bar",
    # Bootstrap
    ".spinner-border",
    ".spinner-grow",
    # Generic conventions
    "svg.spinner",
    "[data-loading='true']",
]


# ---------------------------------------------------------------------------
# Scrollable container detection
# ---------------------------------------------------------------------------
#
# The dashboard is often rendered inside its own scrolling element rather
# than the browser window. automation/screenshots.py auto-detects the
# largest visible scrollable element inside the dashboard region.
#
# If auto-detection ever picks the wrong element, paste a CSS selector
# here to force it.

DASHBOARD_SCROLL_CONTAINER: str | None = None
