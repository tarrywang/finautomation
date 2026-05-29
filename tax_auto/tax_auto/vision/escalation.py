"""Escalation chain: Sonnet → Opus → human. Enforces per-run LLM call cap."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from tax_auto.config import get_settings
from tax_auto.obs.logging import bind
from tax_auto.vision.fallback import resolve_with_claude
from tax_auto.vision.schemas import Decision

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Per-run counter — process-local; in a multi-process world this should live in DB.
_CALL_COUNTER: dict[str, int] = defaultdict(int)


def _budget_left(run_id: str) -> bool:
    s = get_settings()
    return _CALL_COUNTER[run_id] < s.llm_max_calls_per_run


def escalate_resolve(
    page: Page, step: str, intent: str, failed_selectors: list[str], run_id: str = "ad-hoc"
) -> Decision | None:
    """Try Sonnet → if parse fails or low confidence → Opus. Returns None on full miss.

    Per-run budget enforced: after llm_max_calls_per_run, returns None immediately
    (caller — waits.py — will raise SelectorMissError and worker handles fail/notify).
    """
    log = bind(run_id=run_id)
    s = get_settings()

    if not _budget_left(run_id):
        log.warning(f"[escalation] budget exhausted ({s.llm_max_calls_per_run}/run)")
        return None

    # Tier 1: Sonnet
    try:
        _CALL_COUNTER[run_id] += 1
        decision = resolve_with_claude(
            page, step, intent, failed_selectors, run_id, model=s.llm_model_primary,
        )
        if decision.is_blocked:
            return decision
        if decision.actions and decision.actions[0].confidence >= 0.5:
            return decision
        log.info("[escalation] Sonnet confidence low, escalating to Opus")
    except Exception as e:  # noqa: BLE001
        log.warning(f"[escalation] Sonnet failed: {e!s}, escalating to Opus")

    # Tier 2: Opus
    if not _budget_left(run_id):
        log.warning("[escalation] budget exhausted before Opus")
        return None
    try:
        _CALL_COUNTER[run_id] += 1
        decision = resolve_with_claude(
            page, step, intent, failed_selectors, run_id, model=s.llm_model_fallback,
        )
        return decision
    except Exception as e:  # noqa: BLE001
        log.error(f"[escalation] Opus also failed: {e!s} → human needed")
        return None


def reset_counter(run_id: str) -> None:
    """Drop the in-memory counter for a finished run."""
    _CALL_COUNTER.pop(run_id, None)


def get_counter(run_id: str) -> int:
    return _CALL_COUNTER.get(run_id, 0)
