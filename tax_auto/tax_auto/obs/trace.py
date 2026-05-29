"""Playwright trace recording + screenshot archival."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tax_auto.config import get_settings
from tax_auto.obs.logging import bind

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page


def _run_dir(run_id: str) -> Path:
    d = get_settings().traces_dir / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def maybe_start_trace(ctx: BrowserContext, run_id: str) -> None:
    """Start tracing if not already started. Idempotent-ish (Playwright will raise if
    started twice — we swallow that)."""
    try:
        ctx.tracing.start(
            name=run_id,
            screenshots=True,
            snapshots=True,
            sources=False,
        )
        bind(run_id=run_id).debug("tracing started")
    except Exception as e:
        bind(run_id=run_id).debug(f"tracing already running or failed to start: {e}")


def stop_trace(ctx: BrowserContext, run_id: str) -> Path | None:
    """Stop tracing and write trace.zip to runtime/traces/{run_id}/."""
    out = _run_dir(run_id) / "trace.zip"
    try:
        ctx.tracing.stop(path=str(out))
        bind(run_id=run_id).info(f"trace saved → {out}")
        return out
    except Exception as e:
        bind(run_id=run_id).warning(f"trace stop failed: {e}")
        return None


def screenshot_to_run_dir(page: Page, run_id: str, label: str) -> Path:
    """Quick screenshot, e.g. for face-verify notification."""
    out = _run_dir(run_id) / f"{label}.png"
    page.screenshot(path=str(out), full_page=False)
    return out


def dump_dom_snapshot(page: Page, run_id: str, label: str) -> Path:
    """Capture current rendered HTML — useful when reviewing post-mortem."""
    out = _run_dir(run_id) / f"{label}.html"
    out.write_text(page.content(), encoding="utf-8")
    return out
