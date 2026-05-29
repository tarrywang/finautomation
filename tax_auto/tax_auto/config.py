"""Configuration loading: Keychain > env > .env > defaults.

Anthropic API key is read from macOS Keychain by default:
    security add-generic-password -s tax_auto -a anthropic_api_key -w 'sk-ant-...'
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_keychain(service: str, account: str) -> str | None:
    """Read a secret from macOS Keychain. Returns None if missing."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


class Settings(BaseSettings):
    """Application settings. Resolution order: CLI > env > .env > Keychain > defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Paths ───────────────────────────────────────────────────────
    runtime_dir: Path = Field(default=Path("runtime"))

    # ── Secrets (with Keychain fallback) ────────────────────────────
    anthropic_api_key: SecretStr | None = None
    smtp_password: SecretStr | None = None

    # ── Email notification ──────────────────────────────────────────
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_use_ssl: bool = True
    notify_from: str = ""
    notify_to: str = ""
    notify_rate_limit_per_hour: int = 20

    # ── Tunables ────────────────────────────────────────────────────
    log_level: str = "INFO"
    tz: str = "Asia/Shanghai"

    timeout_navigation_ms: int = 30_000
    timeout_export_poll_s: int = 600
    timeout_face_verify_s: int = 180

    llm_model_primary: str = "claude-sonnet-4-6"
    llm_model_fallback: str = "claude-opus-4-7"
    llm_max_calls_per_run: int = 5

    max_concurrent_workers: int = 2
    cooldown_after_failure_min: int = 30

    # ── Derived paths ───────────────────────────────────────────────
    @property
    def session_dir(self) -> Path:
        return self.runtime_dir / "session"

    @property
    def output_dir(self) -> Path:
        return self.runtime_dir / "output"

    @property
    def traces_dir(self) -> Path:
        return self.runtime_dir / "traces"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.runtime_dir / "metadata.db"

    def resolve_secrets(self) -> "Settings":
        """Fill empty secrets from Keychain. Returns self for chaining."""
        if self.anthropic_api_key is None:
            kc = _read_keychain("tax_auto", "anthropic_api_key")
            if kc:
                self.anthropic_api_key = SecretStr(kc)
        if self.smtp_password is None:
            kc = _read_keychain("tax_auto", "smtp_password")
            if kc:
                self.smtp_password = SecretStr(kc)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Use this everywhere."""
    return Settings().resolve_secrets()


settings = get_settings()
