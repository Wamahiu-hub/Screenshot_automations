"""
Screenshot capture that scrolls a dashboard from top to bottom.

Key ideas:

* The scrollable element on a dashboard is usually NOT the browser window.
  We auto-detect the largest, visible, actually-scrollable element on the
  page and scroll that one. If auto-detection is wrong, override with
  ``selectors.DASHBOARD_SCROLL_CONTAINER``.
* We stop scrolling when scrollTop can no longer advance — no fixed
  iteration count — with a safety cap of ``config.MAX_SCROLLS`` iterations.
* We take a screenshot of the current viewport at each step. If a scroll
  doesn't actually move the scroller by more than a few pixels, we treat
  that as "reached the bottom" and stop, avoiding duplicate screenshots.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from playwright.sync_api import JSHandle, Page, TimeoutError as PWTimeoutError

import config
from automation import selectors as sel


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def capture_entire_dashboard(
    page: Page,
    dashboard_name: str,
    output_directory: Path,
) -> list[Path]:
    """
    Screenshot an open dashboard from top to bottom.

    Returns a list of file paths that were written.
    """
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    scroller = _resolve_scroller(page)
    _scroll_to_top(page, scroller)
    _wait_settle(page, thorough=True)

    written: list[Path] = []
    idx = 0
    prev_scroll_top = -1.0

    for step in range(1, config.MAX_SCROLLS + 1):
        _wait_settle(page, thorough=False)
        idx += 1
        path = output_directory / f"{idx:03d}.png"
        # Bump timeout above the default 15s; heavy dashboards (many fonts /
        # widget re-renders) can spend >15s just in the "waiting for fonts
        # to load" phase before Playwright can capture.
        page.screenshot(path=str(path), full_page=False, timeout=45_000)
        written.append(path)
        log.info("[%s] captured %s", dashboard_name, path.name)

        metrics = _scroll_metrics(page, scroller)
        log.debug(
            "[%s] scrollTop=%.1f viewport=%.1f total=%.1f",
            dashboard_name, metrics["top"], metrics["viewport"], metrics["total"],
        )

        if metrics["top"] + metrics["viewport"] >= metrics["total"] - 2:
            log.info("[%s] reached bottom after %d screenshot(s).",
                     dashboard_name, idx)
            break

        if abs(metrics["top"] - prev_scroll_top) < 2 and step > 1:
            log.info("[%s] scrollTop did not advance; treating as bottom.",
                     dashboard_name)
            break

        prev_scroll_top = metrics["top"]

        step_px = max(200, int(metrics["viewport"] * 0.9))
        _scroll_by(page, scroller, step_px)
        time.sleep(config.SCROLL_DELAY)

    else:
        log.warning(
            "[%s] hit MAX_SCROLLS=%d without confirming bottom.",
            dashboard_name, config.MAX_SCROLLS,
        )

    return written


# ---------------------------------------------------------------------------
# Scroll container resolution
# ---------------------------------------------------------------------------

_FIND_SCROLLER_JS = """
(overrideSelector) => {
  if (overrideSelector) {
    const el = document.querySelector(overrideSelector);
    if (el) return el;
  }
  // If the document itself has meaningful scroll room, use it.
  const de = document.scrollingElement || document.documentElement;
  const docScrollable = (de.scrollHeight - de.clientHeight) > 50;

  let best = null;
  let bestScore = 0;
  const walker = document.querySelectorAll('*');
  for (const el of walker) {
    const cs = getComputedStyle(el);
    const oy = cs.overflowY;
    if (!(oy === 'auto' || oy === 'scroll' || oy === 'overlay')) continue;
    const cr = el.getBoundingClientRect();
    if (cr.width < 400 || cr.height < 300) continue;
    const extra = el.scrollHeight - el.clientHeight;
    if (extra <= 20) continue;
    // Prefer big, deeply-scrollable containers visible in the viewport.
    const score = extra * (cr.width * cr.height);
    if (score > bestScore) {
      bestScore = score;
      best = el;
    }
  }
  if (best) return best;
  return docScrollable ? de : null;
}
"""


def _resolve_scroller(page: Page) -> JSHandle | None:
    """Return a JSHandle for the scroll container, or None to mean window."""
    override = sel.DASHBOARD_SCROLL_CONTAINER
    handle = page.evaluate_handle(_FIND_SCROLLER_JS, override)
    # If it resolved to null, evaluate_handle returns a JSHandle of null.
    try:
        as_element = handle.as_element()
    except Exception:  # noqa: BLE001
        as_element = None

    if as_element is None:
        log.info("No inner scroll container detected; using window scroll.")
        return None

    tag = page.evaluate("(el) => el.tagName + (el.id ? '#' + el.id : '')", as_element)
    log.info("Scroll container detected: <%s>", tag)
    return as_element


# ---------------------------------------------------------------------------
# Scroll operations
# ---------------------------------------------------------------------------

def _scroll_to_top(page: Page, scroller: JSHandle | None) -> None:
    if scroller is None:
        page.evaluate("() => window.scrollTo({top: 0, left: 0, behavior: 'instant'})")
    else:
        page.evaluate("(el) => { el.scrollTop = 0; }", scroller)


def _scroll_by(page: Page, scroller: JSHandle | None, delta_px: int) -> None:
    if scroller is None:
        page.evaluate("(dy) => window.scrollBy(0, dy)", delta_px)
    else:
        page.evaluate("(args) => { args.el.scrollTop += args.dy; }",
                      {"el": scroller, "dy": delta_px})


def _scroll_metrics(page: Page, scroller: JSHandle | None) -> dict:
    if scroller is None:
        return page.evaluate(
            """() => {
                const de = document.scrollingElement || document.documentElement;
                return {
                  top: de.scrollTop,
                  viewport: window.innerHeight,
                  total: de.scrollHeight,
                };
            }"""
        )
    return page.evaluate(
        """(el) => ({
            top: el.scrollTop,
            viewport: el.clientHeight,
            total: el.scrollHeight,
        })""",
        scroller,
    )


# ---------------------------------------------------------------------------
# Settle helpers
# ---------------------------------------------------------------------------

def _wait_settle(page: Page, thorough: bool = False) -> None:
    """
    Give lazily-rendered widgets a chance to fully load before capturing.

    Order of checks:
      1. Wait for network to go quiet (long on ``thorough``, short otherwise).
      2. Poll until every element matched by ``sel.WIDGET_LOADING_SELECTORS``
         is invisible for two consecutive checks — that's what actually
         guarantees no spinner is on-screen at capture time.
      3. Small fixed post-settle sleep for paint stability.

    ``thorough=True`` is used before the FIRST screenshot of a dashboard
    (where widgets fetch data from scratch); ``thorough=False`` is used
    before each subsequent between-scroll screenshot.
    """
    idle_timeout_ms = 8_000 if thorough else 2_500
    try:
        page.wait_for_load_state("networkidle", timeout=idle_timeout_ms)
    except PWTimeoutError:
        pass

    spinner_timeout_ms = (
        config.WIDGET_LOAD_TIMEOUT_MS
        if thorough
        else config.WIDGET_LOAD_TIMEOUT_MS_SCROLL
    )
    _wait_for_widgets_loaded(page, timeout_ms=spinner_timeout_ms)

    time.sleep(config.SCREENSHOT_DELAY)


_VISIBLE_SPINNER_COUNT_JS = """
(selectors) => {
    if (!selectors) return 0;
    const els = document.querySelectorAll(selectors);
    let count = 0;
    for (const el of els) {
        const cr = el.getBoundingClientRect();
        if (cr.width < 4 || cr.height < 4) continue;
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden') continue;
        if (cs.display === 'none') continue;
        if (parseFloat(cs.opacity || '1') === 0) continue;
        count++;
    }
    return count;
}
"""


def _wait_for_widgets_loaded(page: Page, timeout_ms: int) -> None:
    """Poll until no configured spinner is visible for two consecutive checks.

    Two consecutive zero-counts guard against the transient window where
    one widget's spinner has just detached and the next widget's spinner
    has not yet attached. Without that guard we would occasionally fire
    the screenshot during that ~50ms gap and still capture spinners.
    """
    combined = ", ".join(sel.WIDGET_LOADING_SELECTORS or [])
    if not combined:
        return

    poll_seconds = max(config.WIDGET_LOAD_POLL_MS, 100) / 1000.0
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    consecutive_zeros = 0
    last_count = -1

    while time.monotonic() < deadline:
        try:
            count = page.evaluate(_VISIBLE_SPINNER_COUNT_JS, combined)
        except Exception:  # noqa: BLE001
            count = 0

        if count == 0:
            consecutive_zeros += 1
            if consecutive_zeros >= 2:
                if last_count > 0:
                    log.debug("Widgets loaded after %.1fs.",
                              (timeout_ms/1000.0) - (deadline - time.monotonic()))
                return
        else:
            consecutive_zeros = 0

        last_count = count
        time.sleep(poll_seconds)

    log.warning(
        "Widget-load wait timed out after %.1fs (still %d spinner(s) visible); "
        "screenshotting anyway.",
        timeout_ms / 1000.0, max(last_count, 0),
    )
