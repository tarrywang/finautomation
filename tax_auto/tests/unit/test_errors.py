"""Exception hierarchy + SelectorMissError payload."""

from __future__ import annotations

import pytest

from tax_auto.core.errors import (
    DataIntegrityError,
    ExportTimeout,
    FaceVerifyRequired,
    HumanRequired,
    InvalidConfig,
    NetworkTimeoutError,
    SelectorMissError,
    SessionError,
    SessionExpired,
    SessionLocked,
    TaxAutoError,
    TaxBureauChanged,
    TransientError,
)


def test_all_inherit_from_base() -> None:
    for exc_cls in (
        TransientError, NetworkTimeoutError,
        SessionError, SessionExpired, SessionLocked,
        SelectorMissError, FaceVerifyRequired,
        ExportTimeout, DataIntegrityError,
        TaxBureauChanged, InvalidConfig, HumanRequired,
    ):
        assert issubclass(exc_cls, TaxAutoError), f"{exc_cls.__name__} missing base"


def test_session_layer_inheritance() -> None:
    assert issubclass(SessionExpired, SessionError)
    assert issubclass(SessionLocked, SessionError)


def test_network_timeout_is_transient() -> None:
    assert issubclass(NetworkTimeoutError, TransientError)


def test_selector_miss_carries_context() -> None:
    err = SelectorMissError(step="step_3", selectors=["a", "b"], intent="click 查询")
    assert err.step == "step_3"
    assert err.selectors == ["a", "b"]
    assert err.intent == "click 查询"
    assert "step_3" in str(err)
    assert "click 查询" in str(err)


def test_human_required_carries_screenshot() -> None:
    err = HumanRequired("扫脸超时", screenshot_path="/tmp/x.png")
    assert err.reason == "扫脸超时"
    assert err.screenshot_path == "/tmp/x.png"


def test_catching_base_catches_all() -> None:
    """Any TaxAutoError descendant should be catchable via TaxAutoError."""
    for raiser, exc_cls in [
        (lambda: (_ for _ in ()).throw(SessionExpired("x")), SessionExpired),
        (lambda: (_ for _ in ()).throw(DataIntegrityError("x")), DataIntegrityError),
        (lambda: (_ for _ in ()).throw(FaceVerifyRequired("x")), FaceVerifyRequired),
    ]:
        with pytest.raises(TaxAutoError):
            raiser()
