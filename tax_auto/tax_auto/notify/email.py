"""SMTP-based email notifications. Replaces the original 飞书 webhook plan."""

from __future__ import annotations

import smtplib
import ssl
import time
from collections import deque
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

from tax_auto.config import get_settings
from tax_auto.obs.logging import bind

Level = Literal["INFO", "WARN", "CRITICAL"]

# Process-local sliding-window rate limiter
_SEND_LOG: deque[float] = deque()


def _within_rate_limit() -> bool:
    s = get_settings()
    now = time.time()
    # Drop entries older than 1h
    while _SEND_LOG and now - _SEND_LOG[0] > 3600:
        _SEND_LOG.popleft()
    return len(_SEND_LOG) < s.notify_rate_limit_per_hour


def _record_send() -> None:
    _SEND_LOG.append(time.time())


def send_email(
    subject: str,
    body: str,
    level: Level = "INFO",
    attachments: list[Path] | None = None,
) -> bool:
    """Send a plain-text email. Returns True on success, False on misconfig / rate-limit.

    Misconfig (missing SMTP password or notify_to) is logged but never raises —
    notification failures must not bring down the main flow.
    """
    log = bind()
    s = get_settings()

    if not s.notify_to or s.smtp_password is None:
        log.warning(f"[email] not sending '{subject}': SMTP not configured")
        return False
    if not _within_rate_limit():
        log.warning(f"[email] rate-limited; dropping '{subject}'")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"[tax_auto:{level}] {subject}"
    msg["From"] = s.notify_from or s.smtp_user
    msg["To"] = s.notify_to
    msg.set_content(body)

    for att in attachments or []:
        if not att.exists():
            continue
        msg.add_attachment(
            att.read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=att.name,
        )

    try:
        if s.smtp_use_ssl:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=15) as smtp:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
                smtp.send_message(msg)
        _record_send()
        log.info(f"[email] sent: {level} {subject}")
        return True
    except Exception as e:
        log.error(f"[email] send failed: {type(e).__name__}: {e}")
        return False
