"""ZIP download + extraction. Used by step_7."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from tax_auto.config import get_settings
from tax_auto.obs.logging import bind

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


def _target_dir(tax_id: str, month_key: str) -> Path:
    """runtime/output/invoices/{tax_id}/{YYYY-MM}/"""
    d = get_settings().output_dir / "invoices" / tax_id / month_key
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_and_extract(
    page: Page,
    link_locator: Locator,
    tax_id: str,
    run_id: str,
    month_key: str,
) -> tuple[list[Path], Path]:
    """Click the link, wait for download event, save ZIP, extract, return file list.

    Returns (extracted_files, target_dir).
    """
    log = bind(run_id=run_id, tax_id=tax_id)
    target = _target_dir(tax_id, month_key)

    with page.expect_download(timeout=60_000) as dl_info:
        link_locator.click()
    download = dl_info.value
    zip_path = target / f"_export_{run_id}.zip"
    download.save_as(str(zip_path))
    log.info(f"download saved → {zip_path}  size={zip_path.stat().st_size} bytes")

    extracted = _extract_safely(zip_path, target)
    log.info(f"extracted {len(extracted)} files")

    # Write a per-month manifest so we have a quick index without DB
    manifest = {
        "tax_id": tax_id,
        "month": month_key,
        "run_id": run_id,
        "zip_name": zip_path.name,
        "files": [str(p.relative_to(target)) for p in extracted],
        "count": len(extracted),
    }
    (target / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return extracted, target


def _extract_safely(zip_path: Path, target: Path) -> list[Path]:
    """Extract with path traversal guard (zip-slip)."""
    out: list[Path] = []
    target_abs = target.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            dest = (target / member).resolve()
            if not str(dest).startswith(str(target_abs)):
                raise ValueError(f"zip-slip attempt: {member!r}")
            zf.extract(member, target)
            if not member.endswith("/"):
                out.append(target / member)
    return out
