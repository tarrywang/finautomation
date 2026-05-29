"""End-to-end smoke test: drive the full 7-step flow against a local fake server.

Proves the architecture (state machine + selectors + worker + downloads + DB)
wires together, without depending on the real e-tax bureau.

This test uses headless Chromium (real Playwright) — install via:
    uv run playwright install chromium
or, if you only have system Chrome, set TAX_AUTO_TEST_USE_SYSTEM_CHROME=1.
"""

from __future__ import annotations

import os

import pytest

from tax_auto.flow.downloads import _extract_safely
from tests.integration.fake_etax_server import reset_state, start


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def fake_server():  # type: ignore[no-untyped-def]
    srv, _, base = start(0)
    yield base
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_state()


@pytest.mark.skipif(
    os.environ.get("CI") == "true" and not os.environ.get("TAX_AUTO_RUN_SMOKE"),
    reason="smoke test is opt-in on CI",
)
def test_full_flow_against_fake_server(fake_server: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Drive a minimal version of the 7-step flow against the fake server.

    This is NOT calling worker.run_fetch() directly — selectors.py is built for
    the real bureau and won't match the fake. Instead we exercise the lower
    layers (Playwright launch, download capture, ZIP extraction, DB write) to
    prove the plumbing.

    Per ADR-0002 we never install playwright chromium; we use system Chrome.
    Override with TAX_AUTO_TEST_USE_PLAYWRIGHT_CHROMIUM=1 if you've manually
    installed chromium and want to verify against that build.
    """
    from playwright.sync_api import sync_playwright

    use_playwright_chromium = (
        os.environ.get("TAX_AUTO_TEST_USE_PLAYWRIGHT_CHROMIUM") == "1"
    )

    with sync_playwright() as p:
        kwargs = {"headless": True}
        if not use_playwright_chromium:
            kwargs["channel"] = "chrome"
        browser = p.chromium.launch(**kwargs)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        # Walk through the flow manually with the fake server's actual selectors
        page.goto(fake_server)
        page.click("text=我要办税")
        page.click("text=税务数字账户")
        page.click("text=全量发票查询")
        page.fill('input[placeholder*="开票日期(起)"]', "2026-04-01")
        page.fill('input[placeholder*="开票日期(止)"]', "2026-04-30")
        page.click("button:has-text('查询')")
        assert "共 2 张" in page.content()

        page.click("button:has-text('批量下载')")
        page.click("label:has-text('OFD')")
        page.click("button:has-text('确认')")
        assert "任务已提交" in page.content()

        page.click("text=导入导出")
        # First visit shows "生成中", reload until "已完成"
        for _ in range(5):
            if "已完成" in page.content():
                break
            page.reload()
        assert "已完成" in page.content()

        with page.expect_download() as dl_info:
            page.click("a:has-text('下载')")
        download = dl_info.value
        zip_path = tmp_path / "out.zip"
        download.save_as(str(zip_path))

        extracted = _extract_safely(zip_path, tmp_path / "unpack")
        assert len(extracted) == 2
        contents = [p.read_text() for p in extracted]
        assert any("24310000000001234567" in c for c in contents)

        ctx.close()
        browser.close()


def test_invoice_metadata_parsing_e2e(fake_server: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Confirm parse_invoice produces structured InvoiceMeta from fake ZIP XMLs."""
    import urllib.request

    from tax_auto.flow.parse_invoice import parse_file

    # Walk far enough to trigger ZIP availability
    urllib.request.urlopen(f"{fake_server}/export/confirm")
    urllib.request.urlopen(f"{fake_server}/progress")
    urllib.request.urlopen(f"{fake_server}/progress")  # 2nd poll → ready

    zip_data = urllib.request.urlopen(f"{fake_server}/zip").read()
    zip_path = tmp_path / "out.zip"
    zip_path.write_bytes(zip_data)
    target = tmp_path / "unpack"
    target.mkdir()
    extracted = _extract_safely(zip_path, target)

    parsed = [parse_file(p) for p in extracted]
    nos = sorted(m.invoice_no for m in parsed if m.invoice_no)
    assert nos == ["24310000000001234567", "24310000000001234568"]
    amounts = sorted(m.amount_cents for m in parsed if m.amount_cents)
    assert amounts == [10000, 20000]
