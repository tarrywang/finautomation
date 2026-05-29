"""Vision Action/Decision schemas + selector deny-list."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tax_auto.vision.schemas import Action, Decision


def test_minimal_click_action() -> None:
    a = Action(kind="click", selector="text=查询")
    assert a.kind == "click"
    assert a.confidence == pytest.approx(0.7)


def test_dangerous_selector_javascript_blocked() -> None:
    with pytest.raises(ValidationError):
        Action(kind="click", selector="javascript:alert(1)")


def test_dangerous_selector_eval_blocked() -> None:
    with pytest.raises(ValidationError):
        Action(kind="fill", selector="x; eval(payload)", value="foo")


def test_dangerous_selector_page_goto_blocked() -> None:
    with pytest.raises(ValidationError):
        Action(kind="click", selector="x'; page.goto('//evil')")


def test_dangerous_selector_script_tag_blocked() -> None:
    with pytest.raises(ValidationError):
        Action(kind="click", selector="<script>x</script>")


def test_decision_blocked_requires_reason() -> None:
    with pytest.raises(ValidationError):
        Decision(is_blocked=True)
    d = Decision(is_blocked=True, blocked_reason="logged out")
    assert d.is_blocked
    assert d.blocked_reason == "logged out"


def test_decision_max_3_actions() -> None:
    too_many = [
        Action(kind="click", selector=f"text=x{i}") for i in range(4)
    ]
    with pytest.raises(ValidationError):
        Decision(actions=too_many)


def test_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Action(kind="click", selector="text=x", confidence=1.5)
    with pytest.raises(ValidationError):
        Action(kind="click", selector="text=x", confidence=-0.1)
