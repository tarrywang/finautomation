"""Extract metadata (invoice_no, date, amount, seller) from downloaded files.

OFD parsing is via `easyofd` (optional dep — install via `pip install '.[ofd]'`).
PDF via `pdfplumber`. XML (全电票) via stdlib ElementTree.

This module is best-effort: returns InvoiceMeta with whatever fields it could
extract; missing fields are None and the file is still archived.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

from tax_auto.obs.logging import bind

FileFormat = Literal["OFD", "PDF", "XML"]


@dataclass
class InvoiceMeta:
    invoice_no: str | None = None
    invoice_code: str | None = None
    invoice_date: str | None = None  # YYYY-MM-DD
    amount_cents: int | None = None
    seller_tax_id: str | None = None
    seller_name: str | None = None
    file_format: FileFormat | None = None


_AMOUNT_RE = re.compile(r"([0-9]+(?:\.[0-9]{1,2})?)")
_DATE_RE = re.compile(r"(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})")
_INVOICE_NO_RE = re.compile(r"\d{8,20}")


def _yuan_to_cents(s: str) -> int | None:
    try:
        return round(float(s) * 100)
    except (TypeError, ValueError):
        return None


def _parse_date(s: str) -> str | None:
    m = _DATE_RE.search(s)
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def parse_file(path: Path) -> InvoiceMeta:
    """Dispatch on extension. Never raises — returns empty InvoiceMeta on failure."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".xml":
            return _parse_xml(path)
        if suffix == ".pdf":
            return _parse_pdf(path)
        if suffix == ".ofd":
            return _parse_ofd(path)
    except Exception as e:
        bind().warning(f"parse_invoice failed for {path.name}: {type(e).__name__}: {e}")
    return InvoiceMeta()


def _parse_xml(path: Path) -> InvoiceMeta:
    """全电票 XML. Tags vary by version, so we scan loosely."""
    tree = ET.parse(path)
    root = tree.getroot()
    meta = InvoiceMeta(file_format="XML")
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        text = (el.text or "").strip()
        if not text:
            continue
        if "invoiceno" in tag or "fphm" in tag:
            if _INVOICE_NO_RE.fullmatch(text):
                meta.invoice_no = text
        elif "invoicecode" in tag or "fpdm" in tag:
            meta.invoice_code = text
        elif "totalamount" in tag or "jshj" in tag or "amountwithtax" in tag:
            if meta.amount_cents is None:
                meta.amount_cents = _yuan_to_cents(text)
        elif "invoicedate" in tag or "kprq" in tag:
            meta.invoice_date = _parse_date(text)
        elif "sellertaxid" in tag or "xfnsrsbh" in tag:
            meta.seller_tax_id = text
        elif "sellername" in tag or "xfmc" in tag:
            meta.seller_name = text
    # Fallback: derive invoice_no from filename if XML did not provide one
    if meta.invoice_no is None:
        m = _INVOICE_NO_RE.search(path.stem)
        if m:
            meta.invoice_no = m.group(0)
    return meta


def _parse_pdf(path: Path) -> InvoiceMeta:
    """PDF: best-effort text extraction via pdfplumber if installed."""
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError:
        meta = InvoiceMeta(file_format="PDF")
        m = _INVOICE_NO_RE.search(path.stem)
        if m:
            meta.invoice_no = m.group(0)
        return meta

    meta = InvoiceMeta(file_format="PDF")
    with pdfplumber.open(path) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    for line in text.splitlines():
        line = line.strip()
        if "发票号码" in line or "Invoice No" in line.lower():
            m = _INVOICE_NO_RE.search(line)
            if m and meta.invoice_no is None:
                meta.invoice_no = m.group(0)
        if "开票日期" in line or "Invoice Date" in line.lower():
            d = _parse_date(line)
            if d:
                meta.invoice_date = d
        if "价税合计" in line and meta.amount_cents is None:
            m = _AMOUNT_RE.search(line.split("价税合计", 1)[-1])
            if m:
                meta.amount_cents = _yuan_to_cents(m.group(1))
    return meta


def _parse_ofd(path: Path) -> InvoiceMeta:
    """OFD: optional easyofd, otherwise filename-derived skeleton."""
    try:
        from easyofd import OFD  # type: ignore[import-not-found]
    except ImportError:
        meta = InvoiceMeta(file_format="OFD")
        m = _INVOICE_NO_RE.search(path.stem)
        if m:
            meta.invoice_no = m.group(0)
        return meta

    meta = InvoiceMeta(file_format="OFD")
    ofd = OFD(str(path))
    info = ofd.get_invoice_info() if hasattr(ofd, "get_invoice_info") else {}
    if isinstance(info, dict):
        meta.invoice_no = info.get("invoice_no") or info.get("发票号码")
        meta.invoice_code = info.get("invoice_code") or info.get("发票代码")
        meta.invoice_date = _parse_date(str(info.get("invoice_date") or info.get("开票日期") or ""))
        amt = info.get("total_amount") or info.get("价税合计") or ""
        if amt:
            meta.amount_cents = _yuan_to_cents(str(amt))
        meta.seller_name = info.get("seller_name") or info.get("销售方名称")
        meta.seller_tax_id = info.get("seller_tax_id") or info.get("销售方税号")
    if meta.invoice_no is None:
        m = _INVOICE_NO_RE.search(path.stem)
        if m:
            meta.invoice_no = m.group(0)
    return meta
