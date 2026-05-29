"""Top-level orchestrator — fan out one fetch task across customers, serial per tax_id.

Cross-customer concurrency capped at settings.max_concurrent_workers. Within a
customer, runs are strictly serial (e-tax bureau enforces single-session per
account; see ADR-0005).
"""

from __future__ import annotations

import time
from calendar import monthrange
from datetime import UTC, datetime, timedelta

from sqlmodel import select

from tax_auto.config import get_settings
from tax_auto.core.worker import run_fetch
from tax_auto.obs.logging import bind
from tax_auto.storage.customers import list_active_customers
from tax_auto.storage.db import session_scope
from tax_auto.storage.models import Run
from tax_auto.storage.models import Session as SessionRow


def month_to_range(month: str) -> tuple[str, str]:
    year, mon = map(int, month.split("-"))
    return (
        f"{year:04d}-{mon:02d}-01",
        f"{year:04d}-{mon:02d}-{monthrange(year, mon)[1]:02d}",
    )


def _too_recent_failure(tax_id: str) -> bool:
    """Skip a customer if it failed in the last cooldown_after_failure_min minutes."""
    s = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=s.cooldown_after_failure_min)
    with session_scope() as sess:
        recent = sess.exec(
            select(Run).where(Run.tax_id == tax_id).order_by(Run.started_at.desc()).limit(1)  # type: ignore[arg-type]
        ).first()
    return bool(recent and recent.state == "FAILED" and recent.started_at >= cutoff)


def _session_warning_due(tax_id: str, days: int = 6) -> bool:
    """True if last_login_at is older than `days` (we proactively warn)."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as sess:
        row = sess.get(SessionRow, tax_id)
    return bool(row and row.last_login_at and row.last_login_at < cutoff)


def run_for_month(month: str, tax_ids: list[str] | None = None) -> list[dict]:  # type: ignore[type-arg]
    """Run the fetch for every active customer (or a subset) for the given YYYY-MM.

    Returns a list of summary dicts, one per attempted customer. Customers in
    cooldown after a recent failure are skipped (with a `skipped` entry).
    """
    from playwright.sync_api import sync_playwright

    log = bind()
    date_from, date_to = month_to_range(month)

    customers = list_active_customers()
    if tax_ids:
        customers = [c for c in customers if c.tax_id in set(tax_ids)]
    if not customers:
        log.warning("no active customers to run")
        return []

    results: list[dict] = []  # type: ignore[type-arg]

    with sync_playwright() as p:
        for c in customers:
            if _too_recent_failure(c.tax_id):
                log.warning(f"skipping {c.tax_id}: recent failure, in cooldown")
                results.append(
                    {
                        "tax_id": c.tax_id,
                        "alias": c.alias,
                        "state": "SKIPPED",
                        "reason": "cooldown",
                    }
                )
                continue

            if _session_warning_due(c.tax_id):
                _send_session_warning(c.tax_id)

            log.info(f"→ fetching for {c.alias} ({c.tax_id})")
            run_id, state, summary = run_fetch(
                p,
                c.tax_id,
                date_from=date_from,
                date_to=date_to,
            )
            results.append(
                {
                    "tax_id": c.tax_id,
                    "alias": c.alias,
                    "run_id": run_id,
                    "state": state.value,
                    **summary,
                }
            )

            # Polite delay between customers — avoid being flagged as bot
            if len(customers) > 1:
                time.sleep(60)

    _send_aggregate_summary(month, results)
    return results


def _send_session_warning(tax_id: str) -> None:
    try:
        from tax_auto.notify.email import send_email
        from tax_auto.notify.templates import session_expiring_body, session_expiring_subject

        with session_scope() as sess:
            row = sess.get(SessionRow, tax_id)
        ts = row.last_login_at.isoformat() if (row and row.last_login_at) else "unknown"
        send_email(
            session_expiring_subject(tax_id),
            session_expiring_body(tax_id, ts),
            level="WARN",
        )
    except Exception:
        pass


def _send_aggregate_summary(month: str, results: list[dict]) -> None:  # type: ignore[type-arg]
    try:
        from tax_auto.notify.email import send_email
        from tax_auto.notify.templates import run_summary_body, run_summary_subject

        rows = [
            (
                r["tax_id"],
                r["state"],
                r.get("actual_count", 0),
                r.get("error_class"),
            )
            for r in results
        ]
        total_invoices = sum(r.get("actual_count", 0) for r in results)
        has_failure = any(r["state"] in ("FAILED", "SKIPPED") for r in results)
        send_email(
            f"{month}  " + run_summary_subject(len(results), total_invoices, has_failure),
            run_summary_body(rows),
            level="CRITICAL" if has_failure else "INFO",
        )
    except Exception:
        pass
