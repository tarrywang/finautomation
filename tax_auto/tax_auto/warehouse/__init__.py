"""Postgres-backed invoice warehouse.

Separate from tax_auto's SQLite operational store. This module owns:
- Postgres connection / session
- Pulled invoice data (companies, invoices, invoice_items)
- Sync run history + raw payload archive
- Alembic migrations
"""
