"""Playwright browser/context factory. Single source of truth for browser launch.

All workers MUST use `make_persistent_context()` — never call `chromium.launch*`
directly. This guarantees:
  - System Chrome (channel='chrome'), not Playwright chromium  → ADR-0002
  - Headed mode, not headless                                   → ADR-0002 / Tenet #2
  - Per-tenant user_data_dir with chmod 700                     → Architecture §10
  - Consistent viewport / locale / timezone
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from tax_auto.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import BrowserContext, Playwright


def _ensure_session_dir(tax_id: str) -> Path:
    """Create per-tenant session dir with restrictive permissions."""
    settings = get_settings()
    path = settings.session_dir / tax_id
    path.mkdir(parents=True, exist_ok=True)
    # chmod 700 — only owner can access (session cookies are sensitive)
    os.chmod(path, stat.S_IRWXU)
    return path


@contextmanager
def make_persistent_context(
    p: Playwright,
    tax_id: str,
    *,
    headless: bool = False,
    extra_args: list[str] | None = None,
) -> Iterator[BrowserContext]:
    """Launch a per-tenant persistent Chrome context.

    Yields a BrowserContext that closes on exit. Use as:

        with sync_playwright() as p:
            with make_persistent_context(p, tax_id) as ctx:
                page = ctx.new_page()
                ...

    extra_args appends to the default Chrome flags — used by scripts/recon.py
    to enable --auto-open-devtools-for-tabs during exploration.
    """
    user_data_dir = _ensure_session_dir(tax_id)
    settings = get_settings()

    base_args = ["--disable-blink-features=AutomationControlled"]
    args = base_args + list(extra_args or [])

    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(user_data_dir),
        channel="chrome",  # system Chrome, not playwright chromium — ADR-0002
        headless=headless,
        viewport={"width": 1440, "height": 900},
        locale="zh-CN",
        timezone_id=settings.tz,
        accept_downloads=True,
        # Tame "automation" fingerprints — best-effort, not a perfect cloak
        args=args,
    )
    try:
        yield ctx
    finally:
        ctx.close()
