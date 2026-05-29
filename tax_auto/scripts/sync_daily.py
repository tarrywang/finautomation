"""sync_daily.py — pull recent invoices for every self-company × 进/销项.

Designed to run from launchd nightly. Strategy:
- Query Postgres for companies marked is_self=True
- For each, sync 进项 + 销项 over a rolling window (default last 14 days)
- Idempotent: upsert by (tax_no, data_type, sdfphm), so repeat runs are safe
- Logs to stdout (captured by launchd to runtime/logs/sync_daily.out.log)

Usage:
    uv run python scripts/sync_daily.py             # last 14 days
    uv run python scripts/sync_daily.py --days 30   # last 30 days
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Make tax_auto importable when running this script directly
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from dotenv import load_dotenv  # noqa: E402

_ENV = _HERE.parents[2] / ".env"
if _ENV.exists():
    load_dotenv(_ENV)

from sqlalchemy import select  # noqa: E402

from tax_auto.fapiao.client import FapiaoClient, FapiaoCredentials, FapiaoError  # noqa: E402
from tax_auto.fapiao.ingest import (  # noqa: E402
    archive_raw_payload,
    create_sync_run,
    ingest_invoices,
    mark_sync_run_complete,
    mark_sync_run_failed,
)
from tax_auto.fapiao.parser import parse_response  # noqa: E402
from tax_auto.warehouse.models import Company  # noqa: E402
from tax_auto.warehouse.session import get_sessionmaker  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("sync_daily")


def sync_one(
    client: FapiaoClient,
    s,
    *,
    tax_no: str,
    data_type: str,
    start: date,
    end: date,
) -> tuple[int, int] | None:
    """Sync one (tax_no, data_type) window. Returns (invoices, items) or None on failure."""
    label = f"{tax_no} dataType={data_type} [{start} ~ {end}]"
    sr = create_sync_run(s, tax_no=tax_no, data_type=data_type, period_start=start, period_end=end)
    s.commit()
    sync_run_id = sr.id

    try:
        raw, decoded = client.sync_invoices_realtime(
            tax_no=tax_no,
            data_type=data_type,
            billing_date_start=start,
            billing_date_end=end,
            page_size=50,
        )
    except FapiaoError as e:
        logger.error("✗ %s — API failed: %s", label, e)
        mark_sync_run_failed(s, sr, code=e.code, message=e.message, request_id=e.request_id)
        s.commit()
        return None

    archive_raw_payload(s, sync_run_id, decoded)
    parsed = parse_response(decoded)
    inv_n, item_n = ingest_invoices(
        s,
        tax_no=tax_no,
        data_type=data_type,
        sync_run_id=sync_run_id,
        parsed=parsed,
    )
    raw_size = len(json.dumps(decoded, ensure_ascii=False).encode("utf-8"))
    mark_sync_run_complete(
        s,
        sr,
        invoice_count=inv_n,
        request_id=raw.get("requestId"),
        raw_size=raw_size,
    )
    s.commit()
    logger.info("✓ %s — %d invoices, %d items, raw=%dKB", label, inv_n, item_n, raw_size // 1024)
    return inv_n, item_n


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=14, help="rolling window size in days (max 30)")
    p.add_argument(
        "--end",
        help="end date YYYY-MM-DD (default: today)",
    )
    args = p.parse_args()

    days = min(max(args.days, 1), 30)  # API limit: window must be within 1 month
    end = date.fromisoformat(args.end) if args.end else date.today()
    start = end - timedelta(days=days - 1)

    SessionLocal = get_sessionmaker()
    creds = FapiaoCredentials.from_env()

    t0 = time.time()
    logger.info("═" * 70)
    logger.info("daily sync · window [%s ~ %s] (%d days)", start, end, days)

    with SessionLocal() as s:
        self_companies = (
            s.execute(select(Company).where(Company.is_self.is_(True)).order_by(Company.tax_no))
            .scalars()
            .all()
        )
        logger.info("self companies in warehouse: %d", len(self_companies))
        if not self_companies:
            logger.warning(
                "no self companies registered yet — run sync_invoices.py manually once first"
            )
            return 0

        total_invoices = 0
        total_items = 0
        failures: list[str] = []

        with FapiaoClient(creds) as client:
            for c in self_companies:
                for dt in ("1", "2"):
                    result = sync_one(
                        client,
                        s,
                        tax_no=c.tax_no,
                        data_type=dt,
                        start=start,
                        end=end,
                    )
                    if result is None:
                        failures.append(f"{c.tax_no}/{dt}")
                    else:
                        n_inv, n_it = result
                        total_invoices += n_inv
                        total_items += n_it

    elapsed = time.time() - t0
    logger.info("═" * 70)
    logger.info(
        "done · %d/%d 同步成功 · %d invoices, %d items · %.1fs",
        2 * len(self_companies) - len(failures),
        2 * len(self_companies),
        total_invoices,
        total_items,
        elapsed,
    )
    if failures:
        logger.warning("失败: %s", ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
