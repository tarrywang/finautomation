"""DB schema + customer registry."""

from __future__ import annotations

from tax_auto.storage.customers import (
    add_customer,
    disable_customer,
    get_customer,
    list_active_customers,
)
from sqlmodel import select

from tax_auto.storage.db import init_db, session_scope
from tax_auto.storage.models import AuditLog, Customer, Invoice, Run, Session, Task


def test_init_db_creates_all_tables() -> None:
    init_db()
    with session_scope() as sess:
        # Smoke test: querying each model should not raise
        for model in (Customer, Session, Task, Run, Invoice, AuditLog):
            sess.exec(select(model)).all()


def test_add_then_get_customer() -> None:
    init_db()
    add_customer("91310000TESTAAAA1", alias="塔锐测试")
    c = get_customer("91310000TESTAAAA1")
    assert c is not None
    assert c.alias == "塔锐测试"
    assert c.active is True
    assert c.notification_level == "INFO"


def test_update_customer_idempotent() -> None:
    init_db()
    add_customer("91310000TESTBBBB1", alias="v1")
    add_customer("91310000TESTBBBB1", alias="v2", notification_level="CRITICAL")
    c = get_customer("91310000TESTBBBB1")
    assert c is not None
    assert c.alias == "v2"
    assert c.notification_level == "CRITICAL"


def test_disable_customer_removes_from_active_list() -> None:
    init_db()
    add_customer("91310000TESTCCCC1", alias="x")
    assert any(c.tax_id == "91310000TESTCCCC1" for c in list_active_customers())
    disable_customer("91310000TESTCCCC1")
    assert not any(c.tax_id == "91310000TESTCCCC1" for c in list_active_customers())


def test_invoice_amount_is_integer_cents() -> None:
    """Architecture decision: never float for money (ADR equivalent)."""
    init_db()
    add_customer("91310000TESTDDDD1", alias="x")
    with session_scope() as sess:
        sess.add(Run(id="01HRUN001", tax_id="91310000TESTDDDD1", state="DONE"))
    with session_scope() as sess:
        inv = Invoice(
            run_id="01HRUN001", tax_id="91310000TESTDDDD1",
            invoice_no="24300000000001",
            invoice_date="2026-04-15", amount_cents=12345,
            file_path="x.ofd", file_format="OFD",
        )
        sess.add(inv)
    with session_scope() as sess:
        loaded = sess.exec(select(Invoice)).first()
        assert loaded is not None
        assert isinstance(loaded.amount_cents, int)
        assert loaded.amount_cents == 12345
