"""sync_invoices.py — pull invoices from 发票通 and upsert into Postgres warehouse.

Usage:
    uv run python scripts/sync_invoices.py --tax-no 91310101MACNGPBT27 --month 2026-04
    uv run python scripts/sync_invoices.py --tax-no 91310101MA7GMP7N5X --month 2026-04 --data-type 2  # 销项
    uv run python scripts/sync_invoices.py --tax-no <X> --from 2026-04-01 --to 2026-04-30
"""

from __future__ import annotations

import argparse
import calendar
import logging
import sys
from datetime import date
from pathlib import Path

# Make tax_auto importable when running this script directly
_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))

from dotenv import load_dotenv  # noqa: E402

# Load .env from financeautomation/
_ENV = _HERE.parents[2] / ".env"
if _ENV.exists():
    load_dotenv(_ENV)

from tax_auto.fapiao.client import FapiaoClient, FapiaoCredentials, FapiaoError  # noqa: E402
from tax_auto.fapiao.ingest import (  # noqa: E402
    archive_raw_payload,
    create_sync_run,
    ingest_invoices,
    mark_sync_run_complete,
    mark_sync_run_failed,
)
from tax_auto.fapiao.parser import parse_response  # noqa: E402
from tax_auto.warehouse.session import get_sessionmaker  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("sync_invoices")


def _parse_month(s: str) -> tuple[date, date]:
    """`2026-04` → (date(2026,4,1), date(2026,4,30))."""
    y, m = (int(x) for x in s.split("-"))
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def main() -> int:
    p = argparse.ArgumentParser(description="Pull invoices from 发票通 → Postgres warehouse")
    p.add_argument("--tax-no", required=True)
    p.add_argument(
        "--data-type",
        choices=["1", "2"],
        default="1",
        help="1=进项(default), 2=销项",
    )
    p.add_argument("--month", help="YYYY-MM shortcut for from/to")
    p.add_argument("--from", dest="from_date", help="YYYY-MM-DD")
    p.add_argument("--to", dest="to_date", help="YYYY-MM-DD")
    p.add_argument("--page-size", type=int, default=50)
    args = p.parse_args()

    if args.month:
        start, end = _parse_month(args.month)
    elif args.from_date and args.to_date:
        start = date.fromisoformat(args.from_date)
        end = date.fromisoformat(args.to_date)
    else:
        p.error("either --month or both --from and --to are required")

    creds = FapiaoCredentials.from_env()
    SessionLocal = get_sessionmaker()

    label = f"taxNo={args.tax_no} dataType={args.data_type} [{start} ~ {end}]"
    logger.info("─" * 60)
    logger.info("starting sync · %s", label)

    with SessionLocal() as s:
        sr = create_sync_run(
            s,
            tax_no=args.tax_no,
            data_type=args.data_type,
            period_start=start,
            period_end=end,
        )
        s.commit()
        sync_run_id = sr.id

        try:
            with FapiaoClient(creds) as client:
                raw, decoded = client.sync_invoices_realtime(
                    tax_no=args.tax_no,
                    data_type=args.data_type,
                    billing_date_start=start,
                    billing_date_end=end,
                    page_size=args.page_size,
                )
        except FapiaoError as e:
            logger.error("API failed: %s", e)
            mark_sync_run_failed(
                s, sr, code=e.code, message=e.message, request_id=e.request_id
            )
            s.commit()
            return 1

        # Archive raw payload
        archive_raw_payload(s, sync_run_id, decoded)

        # Parse & upsert
        parsed = parse_response(decoded)
        inv_n, item_n = ingest_invoices(
            s,
            tax_no=args.tax_no,
            data_type=args.data_type,
            sync_run_id=sync_run_id,
            parsed=parsed,
        )

        # Estimate raw size for the SyncRun row
        import json as _json
        raw_size = len(_json.dumps(decoded, ensure_ascii=False).encode("utf-8"))

        mark_sync_run_complete(
            s, sr,
            invoice_count=inv_n,
            request_id=raw.get("requestId"),
            raw_size=raw_size,
        )
        s.commit()

        logger.info(
            "✅ done · %d invoices upserted, %d 商品 lines, raw=%dKB",
            inv_n, item_n, raw_size // 1024,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
