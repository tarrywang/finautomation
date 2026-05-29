"""Upsert parsed invoices + items into the Postgres warehouse.

Idempotent: re-running the same sync overwrites latest fields per
(tax_no, data_type, sdfphm). 商品 lines are deleted + re-inserted because
the natural key {row_no, invoice_id} doesn't survive across syncs cleanly.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..warehouse.models import Company, Invoice, InvoiceItem, RawPayload, SyncRun
from .parser import ParsedInvoice

logger = logging.getLogger(__name__)


def _upsert_company(s: Session, tax_no: str, name: str | None, is_self: bool = False) -> None:
    """Insert a company row if missing; update name/last_sync_at on every call."""
    stmt = pg_insert(Company).values(
        tax_no=tax_no,
        name=name,
        is_self=is_self,
        last_sync_at=datetime.now(),
    )
    update_cols = {"last_sync_at": stmt.excluded.last_sync_at}
    if name:  # only overwrite name if we have a fresh one
        update_cols["name"] = stmt.excluded.name
    if is_self:  # never unflag a self-company
        update_cols["is_self"] = stmt.excluded.is_self
    stmt = stmt.on_conflict_do_update(index_elements=["tax_no"], set_=update_cols)
    s.execute(stmt)


def ingest_invoices(
    s: Session,
    *,
    tax_no: str,
    data_type: str,
    sync_run_id: int,
    parsed: list[ParsedInvoice],
) -> tuple[int, int]:
    """Upsert parsed invoices + their items.

    Returns:
        (invoices_upserted, items_inserted)
    """
    # 1. Register our self-company + every counterparty seen
    _upsert_company(s, tax_no, name=None, is_self=True)
    for pi in parsed:
        if pi.xfsbh:
            _upsert_company(s, pi.xfsbh, name=pi.xfmc, is_self=False)
        if pi.gfsbh:
            _upsert_company(s, pi.gfsbh, name=pi.gfmc, is_self=False)

    invoices_upserted = 0
    items_inserted = 0
    now = datetime.now()

    for pi in parsed:
        if not pi.sdfphm:
            logger.warning("skipping invoice without sdfphm: section=%s", pi.raw_section)
            continue

        # Upsert invoice header (by natural key)
        stmt = pg_insert(Invoice).values(
            tax_no=tax_no,
            data_type=data_type,
            sdfphm=pi.sdfphm,
            fphm=pi.fphm,
            fpdm=pi.fpdm,
            xfsbh=pi.xfsbh,
            xfmc=pi.xfmc,
            gfsbh=pi.gfsbh,
            gfmc=pi.gfmc,
            kprq=pi.kprq,
            fppz=pi.fppz,
            fpzt=pi.fpzt,
            fpfxdj=pi.fpfxdj,
            sfzsfp=pi.sfzsfp,
            jshj=pi.jshj,
            je=pi.je,
            se=pi.se,
            slv=pi.slv,
            tdywlx=pi.tdywlx,
            bz=pi.bz,
            kpr=pi.kpr,
            deductible=pi.deductible,
            deductible_period=pi.deductible_period,
            raw_section=pi.raw_section,
            raw_data=pi.raw_data,
            sync_run_id=sync_run_id,
            last_seen_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_invoice_natural_key",
            set_={
                # only overwrite mutable fields, preserve first_seen_at
                "fphm": stmt.excluded.fphm,
                "fpdm": stmt.excluded.fpdm,
                "xfsbh": stmt.excluded.xfsbh,
                "xfmc": stmt.excluded.xfmc,
                "gfsbh": stmt.excluded.gfsbh,
                "gfmc": stmt.excluded.gfmc,
                "kprq": stmt.excluded.kprq,
                "fppz": stmt.excluded.fppz,
                "fpzt": stmt.excluded.fpzt,
                "fpfxdj": stmt.excluded.fpfxdj,
                "sfzsfp": stmt.excluded.sfzsfp,
                "jshj": stmt.excluded.jshj,
                "je": stmt.excluded.je,
                "se": stmt.excluded.se,
                "slv": stmt.excluded.slv,
                "tdywlx": stmt.excluded.tdywlx,
                "bz": stmt.excluded.bz,
                "kpr": stmt.excluded.kpr,
                "deductible": stmt.excluded.deductible,
                "deductible_period": stmt.excluded.deductible_period,
                "raw_section": stmt.excluded.raw_section,
                "raw_data": stmt.excluded.raw_data,
                "sync_run_id": stmt.excluded.sync_run_id,
                "last_seen_at": stmt.excluded.last_seen_at,
            },
        ).returning(Invoice.id)
        invoice_id = s.execute(stmt).scalar_one()
        invoices_upserted += 1

        # Replace items wholesale (商品 lines)
        if pi.items:
            s.execute(
                InvoiceItem.__table__.delete().where(
                    InvoiceItem.invoice_id == invoice_id
                )
            )
            s.execute(
                InvoiceItem.__table__.insert(),
                [
                    {
                        "invoice_id": invoice_id,
                        "row_no": it.row_no,
                        "commodity_name": it.commodity_name,
                        "spec": it.spec,
                        "unit": it.unit,
                        "qty": it.qty,
                        "unit_price": it.unit_price,
                        "amount": it.amount,
                        "tax_rate": it.tax_rate,
                        "tax": it.tax,
                        "tax_classify_code": it.tax_classify_code,
                        "raw_data": it.raw_data,
                    }
                    for it in pi.items
                ],
            )
            items_inserted += len(pi.items)

    return invoices_upserted, items_inserted


def archive_raw_payload(s: Session, sync_run_id: int, decoded: dict) -> int:
    rp = RawPayload(sync_run_id=sync_run_id, decoded_json=decoded)
    s.add(rp)
    s.flush()
    assert rp.id is not None
    return rp.id


def create_sync_run(
    s: Session,
    *,
    tax_no: str,
    data_type: str,
    period_start,
    period_end,
) -> SyncRun:
    sr = SyncRun(
        tax_no=tax_no,
        data_type=data_type,
        period_start=period_start,
        period_end=period_end,
        status="pending",
    )
    s.add(sr)
    s.flush()
    return sr


def mark_sync_run_complete(
    s: Session,
    sr: SyncRun,
    *,
    invoice_count: int,
    request_id: str | None = None,
    raw_size: int | None = None,
) -> None:
    sr.status = "success"
    sr.ended_at = datetime.now()
    sr.invoice_count = invoice_count
    sr.request_id = request_id
    sr.raw_response_size_bytes = raw_size


def mark_sync_run_failed(
    s: Session,
    sr: SyncRun,
    *,
    code: str,
    message: str,
    request_id: str | None = None,
) -> None:
    sr.status = "failed"
    sr.ended_at = datetime.now()
    sr.error_code = code
    sr.error_message = message
    sr.request_id = request_id
