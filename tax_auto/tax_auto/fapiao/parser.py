"""Parse decoded 发票通 sync response into flat invoice + item rows.

Response shape (from PDF #4.1 + observed data):
{
  "XXHZB":      [...],   # 信息汇总表 — one row per 商品 line (商品 lines)
  "FPJCXX":     [...],   # 发票基础信息 — deduplicated invoice headers (no 商品 lines)
  "JZFW":       [...],   # 建筑服务
  "HWYSFW":     [...],   # 货物运输服务
  "LKYSFW":     [...],   # 旅客运输服务
  "TLDZKP":     [...],   # 通行费电子发票
  "BDCJYZLFW":  [...],   # 不动产经营租赁服务
  "isQD":       <...>,   # 是否清单 (not iterated)
}

Unique invoice key: `SDFPHM` (数电发票号码,20 位).
XXHZB rows for the same SDFPHM = multiple 商品 lines of one invoice.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# Sections that contain one row per invoice商品 line (商品 lines):
ITEM_SECTIONS = {"XXHZB"}

# Sections that contain deduplicated invoice headers (no商品 rows):
HEADER_ONLY_SECTIONS = {"FPJCXX", "JZFW", "HWYSFW", "LKYSFW", "TLDZKP", "BDCJYZLFW"}

ALL_INVOICE_SECTIONS = ITEM_SECTIONS | HEADER_ONLY_SECTIONS


@dataclass
class ParsedInvoice:
    """One invoice header + its商品 lines."""
    sdfphm: str | None
    fphm: str | None
    fpdm: str | None
    xfsbh: str | None
    xfmc: str | None
    gfsbh: str | None
    gfmc: str | None
    kprq: datetime | None
    fppz: str | None
    fpzt: str | None
    fpfxdj: str | None
    sfzsfp: str | None
    jshj: Decimal | None
    je: Decimal | None
    se: Decimal | None
    slv: str | None
    tdywlx: str | None
    bz: str | None
    kpr: str | None
    deductible: str | None
    deductible_period: str | None
    raw_section: str
    raw_data: dict[str, Any]
    items: list[ParsedItem] = field(default_factory=list)


@dataclass
class ParsedItem:
    row_no: int
    commodity_name: str | None
    spec: str | None
    unit: str | None
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    tax_rate: str | None
    tax: Decimal | None
    tax_classify_code: str | None
    raw_data: dict[str, Any]


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _dt(v: Any) -> datetime | None:
    """Parse 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' into naive UTC datetime."""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _extract_header(row: dict[str, Any], section: str) -> ParsedInvoice:
    """Pull invoice-header fields from a section row (works for XXHZB & FPJCXX shapes)."""
    return ParsedInvoice(
        sdfphm=_str(row.get("SDFPHM")),
        fphm=_str(row.get("FPHM")),
        fpdm=_str(row.get("FPDM")),
        xfsbh=_str(row.get("XFSBH")),
        xfmc=_str(row.get("XFMC")),
        gfsbh=_str(row.get("GFSBH")),
        gfmc=_str(row.get("GFMC")),
        kprq=_dt(row.get("KPRQ")),
        fppz=_str(row.get("FPPZ")),
        fpzt=_str(row.get("FPZT")),
        fpfxdj=_str(row.get("FPFXDJ")),
        sfzsfp=_str(row.get("SFZSFP")),
        jshj=_dec(row.get("JSHJ")),
        je=_dec(row.get("JE")),
        se=_dec(row.get("SE")),
        slv=_str(row.get("SLV")),
        tdywlx=_str(row.get("TDYWLX")),
        bz=_str(row.get("BZ")),
        kpr=_str(row.get("KPR")),
        deductible=_str(row.get("deductible") or row.get("GXZT")),
        deductible_period=_str(row.get("deductiblePeriod") or row.get("GXSQ")),
        raw_section=section,
        raw_data=row,
    )


def _extract_item(row: dict[str, Any], row_no: int) -> ParsedItem:
    return ParsedItem(
        row_no=row_no,
        commodity_name=_str(row.get("HWHYSLWMC")),
        spec=_str(row.get("GGXH")),
        unit=_str(row.get("DW")),
        qty=_dec(row.get("SL")),
        unit_price=_dec(row.get("DJ")),
        amount=_dec(row.get("JE")),
        tax_rate=_str(row.get("SLV")),
        tax=_dec(row.get("SE")),
        tax_classify_code=_str(row.get("SSFLBM")),
        raw_data=row,
    )


def parse_response(decoded: dict[str, Any]) -> list[ParsedInvoice]:
    """Take the base64-decoded sync response and return unique invoices.

    Strategy:
      1. Iterate XXHZB to build {sdfphm → invoice + 商品 lines}.
         Header comes from the FIRST XXHZB row for that sdfphm; subsequent rows
         contribute additional商品 lines.
      2. Iterate HEADER_ONLY sections; if sdfphm not seen in XXHZB, use that
         section's row as a header-only invoice (no商品 lines).
      3. Return invoices in iteration order.

    Logs sections we don't know how to handle.
    """
    by_sdfphm: dict[str, ParsedInvoice] = {}
    item_row_counter: dict[str, int] = defaultdict(int)

    # Step 1: XXHZB — items, header from first row
    for row in decoded.get("XXHZB", []) or []:
        sdfphm = _str(row.get("SDFPHM"))
        if not sdfphm:
            logger.warning("XXHZB row missing SDFPHM, skipping: %s", row.get("XH"))
            continue
        if sdfphm not in by_sdfphm:
            by_sdfphm[sdfphm] = _extract_header(row, "XXHZB")
        # row_no for this invoice's商品 lines
        item_row_counter[sdfphm] += 1
        by_sdfphm[sdfphm].items.append(
            _extract_item(row, item_row_counter[sdfphm])
        )

    # Step 2: header-only sections — only fill if not already seen
    for section in HEADER_ONLY_SECTIONS:
        rows = decoded.get(section, []) or []
        for row in rows:
            sdfphm = _str(row.get("SDFPHM"))
            if not sdfphm:
                continue
            if sdfphm not in by_sdfphm:
                by_sdfphm[sdfphm] = _extract_header(row, section)

    # Step 3: warn about unknown sections
    known = ALL_INVOICE_SECTIONS | {"isQD"}
    for key in decoded:
        if key not in known:
            v = decoded[key]
            n = len(v) if isinstance(v, list) else "?"
            logger.warning("unknown section in response: %s (%s rows)", key, n)

    return list(by_sdfphm.values())
