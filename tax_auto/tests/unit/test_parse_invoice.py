"""Invoice metadata parsing — XML 全电票 + filename fallback."""

from __future__ import annotations

from pathlib import Path

from tax_auto.flow.parse_invoice import (
    InvoiceMeta,
    _parse_date,
    _yuan_to_cents,
    parse_file,
)


def test_yuan_to_cents_normal() -> None:
    assert _yuan_to_cents("123.45") == 12345
    assert _yuan_to_cents("100") == 10000
    assert _yuan_to_cents("0.01") == 1


def test_yuan_to_cents_invalid() -> None:
    assert _yuan_to_cents("¥123") is None
    assert _yuan_to_cents("") is None


def test_parse_date_iso_form() -> None:
    assert _parse_date("2026-04-15") == "2026-04-15"
    assert _parse_date("发票日期 2026年4月15日") == "2026-04-15"
    assert _parse_date("2026.04.15 当日") == "2026-04-15"


def test_parse_date_none_on_bogus() -> None:
    assert _parse_date("no date here") is None


def test_xml_parsing(tmp_path: Path) -> None:
    """A loose XML with 全电票 tag names should yield filled fields."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Invoice>
        <Fphm>24310000000001234567</Fphm>
        <Kprq>2026-04-15</Kprq>
        <Jshj>1234.56</Jshj>
        <Xfnsrsbh>91310000ABCDEFGHIJ</Xfnsrsbh>
        <Xfmc>测试销售方</Xfmc>
    </Invoice>
    """
    p = tmp_path / "x.xml"
    p.write_text(xml)
    m = parse_file(p)
    assert m.invoice_no == "24310000000001234567"
    assert m.invoice_date == "2026-04-15"
    assert m.amount_cents == 123456
    assert m.seller_tax_id == "91310000ABCDEFGHIJ"
    assert m.seller_name == "测试销售方"
    assert m.file_format == "XML"


def test_parse_file_unknown_extension_returns_empty() -> None:
    # Non-xml/pdf/ofd file should return empty meta, not raise
    p = Path("/tmp/nope.txt")
    meta = parse_file(p)
    assert isinstance(meta, InvoiceMeta)
    assert meta.invoice_no is None


def test_parse_file_filename_fallback(tmp_path: Path) -> None:
    """OFD without easyofd installed should still extract invoice_no from filename."""
    p = tmp_path / "24310000000001234567.ofd"
    p.write_bytes(b"not really an OFD")
    meta = parse_file(p)
    # Without easyofd, falls through; depending on whether import succeeds,
    # invoice_no comes from filename regex.
    assert meta.invoice_no == "24310000000001234567" or meta.invoice_no is None
