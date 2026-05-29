"""Pytest fixtures common to all tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect runtime/ to a temp dir per test, so DB / sessions don't leak."""
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    # Clear cached settings between tests
    from tax_auto import config as cfg_mod
    cfg_mod.get_settings.cache_clear()
    return tmp_path


@pytest.fixture
def settings():  # type: ignore[no-untyped-def]
    """Fresh Settings each test."""
    from tax_auto.config import get_settings
    return get_settings()
