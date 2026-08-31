"""
Central configuration for the AIQ / Qualtrics screenshot automation.

Everything that might change between environments or over time lives here,
NOT in the code that uses it. Change values here, not in the automation package.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from a local .env file (if present) so secrets /
# environment-specific URLs never live in source control. Everything below can
# still be overridden with real OS environment variables.
load_dotenv(Path(__file__).resolve().parent / ".env")


# ---------------------------------------------------------------------------
# URLs and application entry points
# ---------------------------------------------------------------------------

# Real AIQ landing URL. Set this in your local .env file, e.g.:
#   AIQ_URL=https://aiq.cerebriai.com/auth
AIQ_URL: str = os.environ.get("AIQ_URL", "").strip()
if not AIQ_URL:
    raise RuntimeError(
        "AIQ_URL is not set. Create a .env file next to config.py with a line like:\n"
        "  AIQ_URL=https://aiq.cerebriai.com/auth"
    )

# Optional: a URL fragment or path that only shows up AFTER the user has been
# authenticated (used as a lightweight "am I logged in?" signal). If you don't
# know one, leave it as None and the automation will fall back to a manual
# "press ENTER when logged in" prompt.
AUTHENTICATED_URL_HINT: str | None = None  # e.g. "/home" or "/dashboard"

# If AIQ requires an explicit navigation to reach the Qualtrics area
# (e.g. a menu item), leave AIQ_URL pointing at the main landing page and
# let automation/qualtrics.py handle the click. If Qualtrics has its own
# direct URL you know is stable, set it here (or in .env as QUALTRICS_URL).
QUALTRICS_URL: str | None = os.environ.get("QUALTRICS_URL") or None

# The organization the p-select ("Select Organization") dropdown must be
# set to before opening any dashboard. Set with ORGANIZATION_NAME in .env.
ORGANIZATION_NAME: str = os.environ.get("ORGANIZATION_NAME", "Qualtrics").strip()


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------

# The logical (canonical) name is what we use in folder names and logs.
# `ui_labels` lists the text values we should try to match in the DOM
# (some deployments show "Trips Statistics" instead of "Trip Statistics").
# `url` is the direct AIQ URL for the dashboard (preferred when known);
# `ui_labels` are visible tile texts we fall back to clicking if `url` is None.
DASHBOARDS: list[dict] = [
    {"index": 1, "name": "Spend Overview",        "ui_labels": ["Spend Overview"],       "url": "https://aiq.cerebriai.com/categories/356/view/1971"},
    {"index": 2, "name": "Expense Details",       "ui_labels": ["Expense Details"],      "url": "https://aiq.cerebriai.com/categories/356/view/1017"},
    {"index": 3, "name": "Budget Tracking",       "ui_labels": ["Budget Tracking"],      "url": "https://aiq.cerebriai.com/categories/356/view/1049"},
    {"index": 4, "name": "Travel Spend Details",  "ui_labels": ["Travel Spend Details"], "url": "https://aiq.cerebriai.com/categories/356/view/1257"},
    {"index": 5, "name": "Spend Dept",            "ui_labels": ["Spend Dept"],           "url": "https://aiq.cerebriai.com/categories/356/view/2068"},
    {"index": 6, "name": "Trip Statistics",       "ui_labels": ["Trip Statistics", "Trips Statistics"], "url": "https://aiq.cerebriai.com/categories/356/view/1896"},
]


# ---------------------------------------------------------------------------
# Users (Phase 2 - impersonation)
# ---------------------------------------------------------------------------

ALEX_BELOV: str = "Alex Belov"
RACHITA_SUNDAR: str = "Rachita Sundar"

# Users to capture via impersonation (Ctrl+Shift+A → "Login As User").
# Every value here MUST match the display name shown in the AIQ
# impersonation autocomplete. Add / remove entries here to expand the run.
IMPERSONATED_USERS: list[str] = [
    ALEX_BELOV,
    RACHITA_SUNDAR,
]


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

# Seconds to wait after opening a dashboard, before starting to screenshot.
# Only used as a MINIMUM; the automation also waits on network-idle / DOM
# state before this delay elapses.
DASHBOARD_LOAD_DELAY: float = 2.0

# Seconds to wait after each scroll, to let lazily-rendered widgets appear.
SCROLL_DELAY: float = 1.2

# Extra seconds after taking a screenshot before scrolling further.
SCREENSHOT_DELAY: float = 0.2

# Max milliseconds to wait for all widget spinners on a dashboard to
# disappear BEFORE the first screenshot of that dashboard. Set high enough
# that slow widgets (Trip Statistics, complex treemaps) can finish rendering.
WIDGET_LOAD_TIMEOUT_MS: int = 45_000

# Shorter cap used before each subsequent scroll-screenshot. Widgets that
# come into view during a scroll are usually quicker to load than the
# initial payload.
WIDGET_LOAD_TIMEOUT_MS_SCROLL: int = 12_000

# The widget-loading check requires TWO consecutive "no spinners visible"
# polls before proceeding, to avoid firing during the brief gap when one
# spinner detaches and the next hasn't attached yet.
WIDGET_LOAD_POLL_MS: int = 300

# Hard safety cap so we can never loop forever if bottom detection misfires.
MAX_SCROLLS: int = 40

# Playwright default operation timeout, in ms.
DEFAULT_TIMEOUT_MS: int = 15_000

# Longer timeout used only for the initial page load / navigation.
NAVIGATION_TIMEOUT_MS: int = 60_000


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

# Persistent Chromium user-data directory. Reusing this folder is how we
# keep the authenticated session between runs. Do NOT commit this folder
# to source control.
PROJECT_ROOT: Path = Path(__file__).resolve().parent
BROWSER_PROFILE_DIR: Path = PROJECT_ROOT / "browser_profile"

# Viewport used for screenshots. Keep this stable so PNGs stitch cleanly.
VIEWPORT: dict = {"width": 1600, "height": 1000}

# Run headed so you can log in manually when the session expires.
HEADLESS: bool = False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

OUTPUT_DIRECTORY: Path = PROJECT_ROOT / "output"
