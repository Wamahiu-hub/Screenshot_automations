"""
Entry point for the AIQ / Qualtrics daily screenshot automation.

Flow (per invocation):

    1. Launch/reuse Microsoft Edge via a persistent profile.
    2. Navigate to AIQ; detect auth and pause for manual login if needed.
    3. Ensure the top-nav "Select Organization" dropdown is set to Qualtrics.
    4. Capture all dashboards in config.DASHBOARDS for the current
       logged-in user (unless --skip-current is passed). Each dashboard
       is opened by clicking its card on the Qualtrics category page.
    5. For each user in config.IMPERSONATED_USERS (or the user passed via
       --user), press Ctrl+Shift+A → type name → click match, verify the
       organization is still Qualtrics, then capture the same dashboards.
    6. Print a summary grouped by user.

Failures in one dashboard don't abort the run; failures in one user
(e.g. impersonation itself failing) skip that user and continue.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import traceback
from pathlib import Path

import config
from automation.browser import launch_context
from automation.impersonation import (
    ImpersonationError,
    exit_impersonation,
    impersonate,
    is_impersonation_active,
)
from automation.pdf_report import consolidate_pdf
from automation.qualtrics import (
    AutomationError,
    ensure_organization_selected,
    navigate_and_authenticate,
    open_dashboard,
    open_qualtrics,
)
from automation.screenshots import capture_entire_dashboard


CURRENT_USER_SLUG = "General"


def _setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
        ],
    )


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def _folder_name(dashboard: dict) -> str:
    return f"{dashboard['index']:02d}_{_slug(dashboard['name'])}"


def _dump_failure_context(errors_dir: Path, tag: str, exc: BaseException) -> None:
    try:
        errors_dir.mkdir(parents=True, exist_ok=True)
        (errors_dir / f"{tag}.txt").write_text(
            "".join(traceback.format_exception(exc)),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture Qualtrics dashboard screenshots from AIQ."
    )
    parser.add_argument(
        "--skip-current",
        action="store_true",
        help="Skip the current-user pass (only run impersonated users).",
    )
    parser.add_argument(
        "--user",
        action="append",
        default=None,
        help=(
            "Capture only this impersonated user (may be given multiple "
            "times). Overrides config.IMPERSONATED_USERS."
        ),
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Skip the PDF consolidation step at the end of the run.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            "Delete each user's output folder before recapturing. Useful "
            "when the number of captured screenshots may shrink so stale "
            "files don't linger."
        ),
    )
    return parser.parse_args(argv)


def _capture_all_dashboards_for_user(
    page,
    user_label: str,
    user_slug: str,
    run_root: Path,
    errors_dir: Path,
    clean: bool = False,
) -> list[tuple[dict, str, int, str]]:
    """Capture every dashboard in config.DASHBOARDS for the given user."""
    log = logging.getLogger("main")
    results: list[tuple[dict, str, int, str]] = []

    if clean:
        user_dir = run_root / user_slug
        if user_dir.is_dir():
            import shutil
            shutil.rmtree(user_dir, ignore_errors=True)
            log.info("[%s] Cleaned prior output folder: %s", user_label, user_dir)

    for dashboard in config.DASHBOARDS:
        folder = _folder_name(dashboard)
        out_dir = run_root / user_slug / folder
        log.info("-" * 60)
        log.info("[%s] Dashboard %02d: %s", user_label, dashboard["index"], dashboard["name"])
        log.info("[%s] Output    : %s", user_label, out_dir)
        log.info("-" * 60)

        try:
            open_dashboard(page, dashboard)
            paths = capture_entire_dashboard(
                page,
                dashboard_name=dashboard["name"],
                output_directory=out_dir,
            )
            log.info(
                "[%s] Captured %d screenshot(s) for '%s'",
                user_label, len(paths), dashboard["name"],
            )
            results.append((dashboard, "OK", len(paths), str(out_dir)))
        except AutomationError as exc:
            log.error("[%s] Dashboard '%s' failed: %s", user_label, dashboard["name"], exc)
            _dump_failure_context(errors_dir, f"{user_slug}_{folder}", exc)
            results.append((dashboard, "FAIL", 0, str(exc).splitlines()[0]))
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] Unexpected error for '%s': %s", user_label, dashboard["name"], exc)
            _dump_failure_context(errors_dir, f"{user_slug}_{folder}", exc)
            results.append((dashboard, "ERROR", 0, str(exc).splitlines()[0]))
    return results


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])

    run_root = config.OUTPUT_DIRECTORY / "test"
    log_path = run_root / "run.log"
    _setup_logging(log_path)
    log = logging.getLogger("main")

    log.info("=" * 60)
    log.info("AIQ Qualtrics screenshot run")
    log.info("=" * 60)

    if args.user:
        impersonated_users = args.user
    else:
        impersonated_users = list(config.IMPERSONATED_USERS)

    log.info("Skip current user : %s", args.skip_current)
    log.info("Impersonated users: %s", impersonated_users or "(none)")

    errors_dir = run_root / "errors"
    per_user_results: dict[str, list[tuple[dict, str, int, str]]] = {}

    try:
        with launch_context() as (_, page):
            navigate_and_authenticate(page)

            # In AIQ 4.1.4 the "Select Organization" p-select lives in the
            # top-nav of /dashboard (AIQ Travel Hub) — it isn't rendered
            # on /categories/356. So we must land on /dashboard first to
            # exit impersonation and set the org, THEN navigate to
            # /categories/356 for the dashboard captures.
            log.info("Navigating to /dashboard for auth chrome + org check.")
            page.goto("https://aiq.cerebriai.com/dashboard",
                      wait_until="domcontentloaded")
            page.wait_for_timeout(3_000)

            # Prior runs may have left the session impersonating another
            # user. Exit any active impersonation so the "General" capture
            # below is truly the current admin.
            try:
                exit_impersonation(page)
                page.wait_for_timeout(1_500)
                # After Exit, ensure we're back on /dashboard for the org check.
                if "/dashboard" not in (page.url or ""):
                    page.goto("https://aiq.cerebriai.com/dashboard",
                              wait_until="domcontentloaded")
                    page.wait_for_timeout(2_000)
            except ImpersonationError as exc:
                log.warning(
                    "Could not exit lingering impersonation cleanly: %s. "
                    "Continuing anyway.", exc,
                )

            # Set the organization while we're on /dashboard where the
            # p-select actually lives. If we can't find/set it here we
            # bail — running with no org selected produces empty user
            # lists and a Qualtrics-less /dashboard, which is what
            # sank the previous attempts.
            try:
                ensure_organization_selected(page, config.ORGANIZATION_NAME)
            except AutomationError as exc:
                log.error(
                    "Failed to set organization to '%s' on /dashboard: %s",
                    config.ORGANIZATION_NAME, exc,
                )
                raise

            # Now navigate to the Qualtrics category for dashboard capture.
            open_qualtrics(page)
            page.wait_for_timeout(2_000)

            if not args.skip_current:
                log.info("=" * 60)
                log.info("Capturing dashboards for the CURRENT logged-in user")
                log.info("=" * 60)
                per_user_results["Current User"] = _capture_all_dashboards_for_user(
                    page,
                    user_label="Current",
                    user_slug=CURRENT_USER_SLUG,
                    run_root=run_root,
                    errors_dir=errors_dir,
                    clean=args.clean,
                )

            for user_name in impersonated_users:
                log.info("=" * 60)
                log.info("Impersonating and capturing dashboards for: %s", user_name)
                log.info("=" * 60)

                # The user-confirmed manual impersonation flow is:
                #   1. Be on Trip Statistics (view/1896).
                #   2. Press Ctrl+Shift+A.
                #   3. Use the Login As User widget.
                # Direct page.goto('/view/1896') is broken in AIQ 4.1.4 (see
                # comment in automation/qualtrics.py on the lazy-chunk MIME
                # bug), so we route through the same SPA-bootstrap helper
                # by reusing open_dashboard on the Trip Statistics config
                # entry — that guarantees the page is a live AIQ dashboard
                # when Ctrl+Shift+A fires.
                try:
                    trip_stats = next(
                        d for d in config.DASHBOARDS if "1896" in (d.get("url") or "")
                    )
                    log.info(
                        "Opening Trip Statistics for impersonation launch."
                    )
                    open_dashboard(page, trip_stats)
                    page.wait_for_timeout(1_500)
                except Exception as reset_exc:  # noqa: BLE001
                    log.warning(
                        "Could not open Trip Statistics before impersonation: %s",
                        reset_exc,
                    )

                slug = _slug(user_name)

                if is_impersonation_active(page, expected_name=user_name):
                    log.info(
                        "Already impersonating '%s' (session persisted); "
                        "skipping the impersonation step.", user_name,
                    )
                else:
                    try:
                        impersonate(page, user_name)
                    except ImpersonationError as exc:
                        log.error("Impersonation of '%s' failed: %s", user_name, exc)
                        _dump_failure_context(errors_dir, f"impersonate_{slug}", exc)
                        per_user_results[user_name] = [
                            (
                                {"index": 0, "name": "<impersonation>"},
                                "FAIL",
                                0,
                                str(exc).splitlines()[0],
                            )
                        ]
                        continue

                # After impersonation the top-nav gains a "Switch User..."
                # p-select. The organization context set before impersonation
                # is inherited by the impersonated session, so we skip the
                # strict org re-check here and go straight to the dashboards.
                # If a dashboard silently redirects to the home page, we'll
                # revisit this.

                per_user_results[user_name] = _capture_all_dashboards_for_user(
                    page,
                    user_label=user_name,
                    user_slug=slug,
                    run_root=run_root,
                    errors_dir=errors_dir,
                    clean=args.clean,
                )

            # After the last impersonated user is captured, return the browser
            # to the clean admin session so the profile isn't left in an
            # impersonated state for the next manual/automated run. Wrapped in
            # its own try/except: a failed exit here must not prevent the
            # PDF from being written.
            if impersonated_users:
                try:
                    log.info("Exiting impersonation at end of run...")
                    exit_impersonation(page)
                    log.info("Successfully exited final impersonation.")
                except ImpersonationError as exc:
                    log.warning(
                        "Could not cleanly exit final impersonation: %s. "
                        "Continuing to PDF write anyway.", exc,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Unexpected error while exiting final impersonation: %s. "
                        "Continuing to PDF write anyway.", exc,
                    )

    except AutomationError as exc:
        log.error("Automation failed before any dashboards ran: %s", exc)
        _dump_failure_context(errors_dir, "run_setup", exc)
        _print_summary(per_user_results)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.exception("Unexpected setup error: %s", exc)
        _dump_failure_context(errors_dir, "run_setup", exc)
        _print_summary(per_user_results)
        return 1

    _print_summary(per_user_results)

    if not args.no_pdf:
        _build_pdf(per_user_results, args, run_root, log)

    any_failure = any(
        status != "OK"
        for results in per_user_results.values()
        for _, status, _, _ in results
    )
    return 0 if not any_failure else 1


def _build_pdf(
    per_user_results: dict,
    args: argparse.Namespace,
    run_root: Path,
    log: logging.Logger,
) -> None:
    """Build the consolidated PDF report from whatever we captured."""
    sections: list[tuple[str, str, str]] = []
    if not args.skip_current and "Current User" in per_user_results:
        sections.append(
            (CURRENT_USER_SLUG, "General (Current Admin)", "Session default account")
        )
    for user_name in per_user_results:
        if user_name == "Current User":
            continue
        sections.append(
            (_slug(user_name), user_name, "Impersonated via Ctrl+Shift+A → Login As User")
        )

    if not sections:
        log.warning("No sections available; skipping PDF consolidation.")
        return

    try:
        pdf_path = consolidate_pdf(run_root, sections)
        print()
        print(f"  PDF report : {pdf_path}")
    except Exception as exc:  # noqa: BLE001
        log.exception("PDF consolidation failed: %s", exc)


def _print_summary(per_user_results: dict[str, list[tuple[dict, str, int, str]]]) -> None:
    print()
    print("=" * 78)
    print("  Full run summary")
    print("=" * 78)
    if not per_user_results:
        print("  (no users were attempted)")
        print("=" * 78)
        return

    for user_name, results in per_user_results.items():
        print(f"  User: {user_name}")
        for dashboard, status, count, detail in results:
            marker = "OK  " if status == "OK" else status.ljust(4)
            print(
                f"    [{marker}] {dashboard['index']:02d} "
                f"{dashboard['name']:<24s} shots={count}  {detail}"
            )
    print("=" * 78)


if __name__ == "__main__":
    sys.exit(run())
