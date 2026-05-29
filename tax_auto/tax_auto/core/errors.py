"""Exception taxonomy for tax_auto. See docs/architecture.md §8."""

from __future__ import annotations


class TaxAutoError(Exception):
    """Base class for all tax_auto errors."""


# ── Transient: auto-retry up to 3x with exponential backoff ────────
class TransientError(TaxAutoError):
    """Recoverable error worth retrying."""


class NetworkTimeoutError(TransientError):
    """Network-level timeout / DNS failure / TCP reset."""


# ── Session layer ──────────────────────────────────────────────────
class SessionError(TaxAutoError):
    """Base for session-related failures."""


class SessionExpired(SessionError):
    """Cookies invalid; user redirected back to login page."""


class SessionLocked(SessionError):
    """Another worker holds the file lock for this tax_id."""


# ── Selector layer → triggers vision fallback ──────────────────────
class SelectorMissError(TaxAutoError):
    """No selector candidate matched. Carries context for vision fallback."""

    def __init__(self, step: str, selectors: list[str], intent: str) -> None:
        self.step = step
        self.selectors = selectors
        self.intent = intent
        super().__init__(f"[{step}] selectors {selectors!r} all missed; intent={intent!r}")


# ── Business layer ─────────────────────────────────────────────────
class FaceVerifyRequired(TaxAutoError):
    """Second-factor face scan modal appeared; human must intervene."""


class ExportTimeout(TaxAutoError):
    """ZIP generation polling exceeded timeout_export_poll_s."""


class DataIntegrityError(TaxAutoError):
    """Downloaded file count != invoice list count (or similar mismatch)."""


# ── Fatal: do not retry ────────────────────────────────────────────
class TaxBureauChanged(TaxAutoError):
    """Vision fallback exhausted — UI likely had a major revision."""


class InvalidConfig(TaxAutoError):
    """User-facing config error (missing secret, bad customer file, ...)."""


class HumanRequired(TaxAutoError):
    """Escalation final tier — needs a human now. State machine → NEEDS_HUMAN."""

    def __init__(self, reason: str, screenshot_path: str | None = None) -> None:
        self.reason = reason
        self.screenshot_path = screenshot_path
        super().__init__(reason)
