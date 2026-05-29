"""Browser session lifecycle. See docs/architecture.md §3 + §10."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlmodel import select

from tax_auto.config import get_settings
from tax_auto.core.browser import make_persistent_context
from tax_auto.core.errors import SessionExpired, SessionLocked
from tax_auto.core.state_machine import SessionState
from tax_auto.obs.logging import bind
from tax_auto.storage.db import session_scope
from tax_auto.storage.models import Customer, Session as SessionRow

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import BrowserContext, Page, Playwright

# Shanghai e-tax landmark URLs
LOGIN_URL = "https://tpass.shanghai.chinatax.gov.cn:8443/"
ETAX_HOST_FRAGMENT = "etax.shanghai.chinatax.gov.cn"
TPASS_HOST_FRAGMENT = "tpass.shanghai.chinatax.gov.cn"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _upsert_session(tax_id: str, user_data_dir: str, status: SessionState) -> None:
    """Insert-or-update sessions row + ensure customer exists."""
    with session_scope() as sess:
        if not sess.get(Customer, tax_id):
            sess.add(Customer(tax_id=tax_id, alias=tax_id))
        row = sess.get(SessionRow, tax_id)
        now = _utcnow()
        if row is None:
            sess.add(SessionRow(
                tax_id=tax_id,
                user_data_dir=user_data_dir,
                last_login_at=now if status == SessionState.FRESH else None,
                status=status.value,
            ))
        else:
            if status == SessionState.FRESH:
                row.last_login_at = now
            row.user_data_dir = user_data_dir
            row.status = status.value
            sess.add(row)


def _mark_status(tax_id: str, status: SessionState) -> None:
    with session_scope() as sess:
        row = sess.get(SessionRow, tax_id)
        if row:
            row.status = status.value
            row.last_used_at = _utcnow()
            sess.add(row)


def login_interactive(p: Playwright, tax_id: str, timeout_s: int = 300) -> None:
    """Headed Chrome for human to complete password + 扫脸. Saves session to disk.

    Blocks until URL redirects to etax host (= login succeeded) or timeout.
    """
    log = bind(tax_id=tax_id, run_id="login")
    log.info("opening login page, please complete password + 扫脸 in the browser")

    with make_persistent_context(p, tax_id, headless=False) as ctx:
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        try:
            page.wait_for_url(f"**/{ETAX_HOST_FRAGMENT}/**", timeout=timeout_s * 1000)
        except Exception as e:
            raise SessionExpired(f"login did not complete within {timeout_s}s") from e

        user_data_dir = str((get_settings().session_dir / tax_id).resolve())
        _upsert_session(tax_id, user_data_dir, SessionState.FRESH)
        log.info("✓ login succeeded, session persisted")


@contextmanager
def open_session(p: Playwright, tax_id: str) -> Iterator[tuple[BrowserContext, Page]]:
    """Open a per-tenant context with cross-process file lock.

    Yields (ctx, first page). Raises SessionLocked if another worker holds it.
    Marks LOCKED → VALID on enter, releases on exit.
    """
    settings = get_settings()
    lock_path = settings.session_dir / tax_id / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch(exist_ok=True)

    lock_fp = lock_path.open("r+")
    try:
        try:
            fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise SessionLocked(f"{tax_id} session is held by another process") from e

        _mark_status(tax_id, SessionState.LOCKED)
        try:
            with make_persistent_context(p, tax_id, headless=False) as ctx:
                page = ctx.new_page()
                yield ctx, page
            # Successful exit
            _mark_status(tax_id, SessionState.VALID)
        except Exception:
            # Don't downgrade to EXPIRED here — caller decides via is_session_alive
            _mark_status(tax_id, SessionState.VALID)
            raise
    finally:
        try:
            fcntl.flock(lock_fp, fcntl.LOCK_UN)
        finally:
            lock_fp.close()


def is_session_alive(page: Page) -> bool:
    """True if we're not on the login page right now."""
    return TPASS_HOST_FRAGMENT not in (page.url or "")


def assert_session_alive(page: Page, tax_id: str) -> None:
    """Raise SessionExpired if redirected back to login."""
    if not is_session_alive(page):
        _mark_status(tax_id, SessionState.EXPIRED)
        raise SessionExpired(f"{tax_id} session expired — re-run `tax-auto login`")


def list_sessions() -> list[tuple[str, str, str | None]]:
    """Return (tax_id, status, last_login_at_iso) for all known sessions."""
    with session_scope() as sess:
        rows = sess.exec(select(SessionRow)).all()
        return [
            (r.tax_id, r.status, r.last_login_at.isoformat() if r.last_login_at else None)
            for r in rows
        ]
