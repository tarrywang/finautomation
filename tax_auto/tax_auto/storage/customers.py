"""Customer registry helpers — thin wrappers over the customers table."""

from __future__ import annotations

from sqlmodel import select

from tax_auto.core.errors import InvalidConfig
from tax_auto.storage.db import session_scope
from tax_auto.storage.models import Customer


def add_customer(
    tax_id: str,
    alias: str,
    contact: str | None = None,
    notification_level: str = "INFO",
    use_china_llm: bool = False,
) -> None:
    """Insert or update a customer."""
    with session_scope() as sess:
        row = sess.get(Customer, tax_id)
        if row is None:
            sess.add(
                Customer(
                    tax_id=tax_id,
                    alias=alias,
                    contact=contact,
                    notification_level=notification_level,
                    use_china_llm=use_china_llm,
                )
            )
        else:
            row.alias = alias
            row.contact = contact
            row.notification_level = notification_level
            row.use_china_llm = use_china_llm
            row.active = True
            sess.add(row)


def disable_customer(tax_id: str) -> None:
    with session_scope() as sess:
        row = sess.get(Customer, tax_id)
        if row is None:
            raise InvalidConfig(f"unknown tax_id: {tax_id}")
        row.active = False
        sess.add(row)


def list_active_customers() -> list[Customer]:
    """Return detached copies so callers can use them after session close."""
    with session_scope() as sess:
        rows = sess.exec(select(Customer).where(Customer.active == True)).all()  # noqa: E712
        # Expunge so attribute access works after the session closes
        for r in rows:
            sess.expunge(r)
        return list(rows)


def get_customer(tax_id: str) -> Customer | None:
    with session_scope() as sess:
        row = sess.get(Customer, tax_id)
        if row is not None:
            sess.expunge(row)
        return row
