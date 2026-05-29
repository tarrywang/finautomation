"""Selector catalog basic invariants."""

from __future__ import annotations

import pytest

from tax_auto.flow.selectors import SELECTORS, get_candidates


def test_all_steps_present() -> None:
    expected = {
        "step_2_navigate",
        "step_3_fill_query",
        "step_4_select_export",
        "step_5_face_verify",
        "step_6_poll_export",
    }
    assert expected.issubset(SELECTORS.keys())


def test_every_key_has_at_least_one_candidate() -> None:
    for step, keys in SELECTORS.items():
        for key, cands in keys.items():
            assert len(cands) >= 1, f"{step}/{key} has no candidates"
            assert all(isinstance(c, str) and c for c in cands)


def test_get_candidates_raises_on_unknown() -> None:
    with pytest.raises(KeyError):
        get_candidates("step_999", "anything")
    with pytest.raises(KeyError):
        get_candidates("step_3_fill_query", "no_such_key")


def test_navigation_chain_complete() -> None:
    """All 3 navigation menu items must exist for step_2."""
    for key in ("menu_办税", "menu_税务数字账户", "link_全量发票查询",
                "page_loaded_anchor"):
        cands = get_candidates("step_2_navigate", key)
        assert cands
