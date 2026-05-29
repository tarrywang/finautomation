"""Email templates + rate limiter (no real SMTP)."""

from __future__ import annotations

from pathlib import Path

from tax_auto.notify.email import _SEND_LOG, _within_rate_limit, send_email
from tax_auto.notify.templates import (
    face_verify_body,
    face_verify_subject,
    run_summary_body,
    run_summary_subject,
    session_expiring_body,
    session_expiring_subject,
)


def test_face_verify_subject_contains_tax_id() -> None:
    s = face_verify_subject("91310000XX")
    assert "91310000XX" in s
    assert "扫脸" in s


def test_face_verify_body_includes_screenshot_when_given() -> None:
    b = face_verify_body("91310000XX", "01HRUN", Path("/tmp/x.png"))
    assert "/tmp/x.png" in b
    assert "01HRUN" in b
    assert "扫脸" in b or "Chrome" in b


def test_face_verify_body_no_screenshot_handled() -> None:
    b = face_verify_body("91310000XX", "01HRUN", None)
    assert "截图未生成" in b


def test_run_summary_subject_success_vs_failure() -> None:
    assert "✅" in run_summary_subject(3, 100, False)
    assert "🚨" in run_summary_subject(3, 100, True)


def test_run_summary_body_renders_rows() -> None:
    body = run_summary_body([
        ("91310000A", "DONE", 38, None),
        ("91310000B", "FAILED", 0, "SelectorMissError"),
    ])
    assert "91310000A" in body and "✅" in body and "38" in body
    assert "91310000B" in body and "❌" in body and "SelectorMissError" in body


def test_session_expiring_body_has_login_command() -> None:
    b = session_expiring_body("91310000XX", "2026-04-01T10:00:00+00:00")
    assert "tax-auto login" in b
    assert "91310000XX" in b


def test_send_email_without_smtp_config_returns_false(
    monkeypatch: "pytest.MonkeyPatch",  # noqa: F821
) -> None:
    """No SMTP password configured → must NOT raise, returns False.

    Explicitly force-clear the resolved password — once the user has stored
    smtp_password in Keychain, settings.resolve_secrets() picks it up, and
    a naive call to send_email would actually try to send.
    """
    import tax_auto.notify.email as email_mod
    from tax_auto.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "smtp_password", None)
    monkeypatch.setattr(s, "notify_to", "")
    ok = email_mod.send_email("test subj", "test body", level="INFO")
    assert ok is False


def test_rate_limiter_window() -> None:
    """Sliding 1-hour window prevents email storms."""
    _SEND_LOG.clear()
    # 20 default limit; push 20 timestamps "just now"
    import time
    now = time.time()
    for _ in range(20):
        _SEND_LOG.append(now)
    assert _within_rate_limit() is False  # 20 ≥ 20

    _SEND_LOG.clear()
    # Push 19 — should still allow
    for _ in range(19):
        _SEND_LOG.append(now)
    assert _within_rate_limit() is True
