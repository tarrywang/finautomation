"""ZIP extraction integrity + zip-slip guard."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tax_auto.flow.downloads import _extract_safely


def test_extract_normal_zip(tmp_path: Path) -> None:
    zp = tmp_path / "in.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a.ofd", b"alpha")
        zf.writestr("nested/b.ofd", b"beta")
    target = tmp_path / "out"
    target.mkdir()
    files = _extract_safely(zp, target)
    names = sorted(p.name for p in files)
    assert names == ["a.ofd", "b.ofd"]
    assert (target / "a.ofd").read_bytes() == b"alpha"
    assert (target / "nested" / "b.ofd").read_bytes() == b"beta"


def test_extract_rejects_zip_slip(tmp_path: Path) -> None:
    """Attempting to write outside target dir must raise."""
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("../escape.ofd", b"nope")
    target = tmp_path / "out"
    target.mkdir()
    with pytest.raises(ValueError, match="zip-slip"):
        _extract_safely(zp, target)
