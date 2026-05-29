"""SPA wait helpers + multi-tier selector probing with vision fallback hook.

The public API is small on purpose:
  - safe_click(page, step, key, intent)
  - safe_fill(page, step, key, value, intent)
  - safe_wait_visible(page, step, key, intent)

Each tries every candidate selector from selectors.py in order. On full miss,
it invokes vision_fallback (lazily imported to avoid circular import).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tax_auto.core.errors import SelectorMissError
from tax_auto.flow.selectors import get_candidates
from tax_auto.obs.logging import bind

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


def wait_for_idle(page: Page, extra_ms: int = 200) -> None:
    """Wait for networkidle + small settling buffer.

    SPAs trigger many AJAX bursts; networkidle alone occasionally races.
    extra_ms guards against that without resorting to brittle time.sleep().
    """
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(extra_ms)


def _try_locators(page: Page, candidates: list[str], timeout_ms: int) -> Locator | None:
    """Return the first locator that resolves to >=1 visible element. None if all miss."""
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except Exception:  # noqa: BLE001 — Playwright TimeoutError + variants
            continue
    return None


def _resolve_via_vision(
    page: Page, step: str, key: str, intent: str, candidates: list[str]
) -> Locator | None:
    """Lazy import to avoid circular dep (vision uses bind() too).

    Returns a Locator the caller can act on, or None if vision can't decide either.
    """
    try:
        from tax_auto.vision.escalation import escalate_resolve
    except ImportError:
        return None
    decision = escalate_resolve(page, step=step, intent=intent, failed_selectors=candidates)
    if decision is None or decision.is_blocked or not decision.actions:
        return None
    # Use the FIRST action's selector — multi-action sequences are rare for selector resolution
    sel = decision.actions[0].selector
    try:
        loc = page.locator(sel).first
        loc.wait_for(state="visible", timeout=3000)
        bind().info(f"[vision] {step}/{key} resolved → {sel!r}")
        return loc
    except Exception:  # noqa: BLE001
        return None


def safe_click(
    page: Page, step: str, key: str, intent: str, timeout_ms: int = 8000
) -> None:
    """Click first matching candidate; fall back to vision on miss."""
    cands = get_candidates(step, key)
    loc = _try_locators(page, cands, timeout_ms)
    if loc is None:
        loc = _resolve_via_vision(page, step, key, intent, cands)
    if loc is None:
        raise SelectorMissError(step=step, selectors=cands, intent=intent)
    loc.click()


def safe_fill(
    page: Page, step: str, key: str, value: str, intent: str, timeout_ms: int = 8000
) -> None:
    """Fill first matching candidate; fall back to vision on miss."""
    cands = get_candidates(step, key)
    loc = _try_locators(page, cands, timeout_ms)
    if loc is None:
        loc = _resolve_via_vision(page, step, key, intent, cands)
    if loc is None:
        raise SelectorMissError(step=step, selectors=cands, intent=intent)
    loc.fill(value)


def safe_wait_visible(
    page: Page, step: str, key: str, intent: str, timeout_ms: int = 8000
) -> Locator:
    """Return locator if visible within timeout; fall back to vision."""
    cands = get_candidates(step, key)
    loc = _try_locators(page, cands, timeout_ms)
    if loc is None:
        loc = _resolve_via_vision(page, step, key, intent, cands)
    if loc is None:
        raise SelectorMissError(step=step, selectors=cands, intent=intent)
    return loc


def is_visible(page: Page, step: str, key: str, timeout_ms: int = 1500) -> bool:
    """Non-raising: True if any candidate is visible within timeout."""
    cands = get_candidates(step, key)
    return _try_locators(page, cands, timeout_ms) is not None
