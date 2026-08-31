"""
Post-impersonation user verification (Phase 2).

The intended contract is documented here so Phase 1 can be wired into
the same architecture without any restructuring later.
"""

from __future__ import annotations

from playwright.sync_api import Page


def verify_current_user(page: Page, expected_user: str) -> bool:
    """
    Confirm that the application is currently operating AS ``expected_user``.

    The exact strategy depends on where AIQ displays the current user
    (avatar tooltip, account menu, header text, etc.). We will fill this
    in with a real selector once Phase 1 confirms the base navigation.

    Not implemented until Phase 1 is verified end-to-end.
    """
    raise NotImplementedError("User verification is a Phase 2 feature.")
