"""Vision fallback — call Claude with a screenshot + intent → get an Action plan.

Logged to runtime/traces/{run_id}/llm_calls.jsonl for full audit.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic

from tax_auto.config import get_settings
from tax_auto.obs.logging import bind
from tax_auto.vision.prompts import SYSTEM_PROMPT, build_user_message
from tax_auto.vision.schemas import Decision

if TYPE_CHECKING:
    from playwright.sync_api import Page

# Anthropic pricing in USD per million tokens, approximate Sonnet 4.6 / Opus 4.7
_PRICING_PER_M = {
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-opus-4-7": {"in": 15.0, "out": 75.0},
}


def _client() -> anthropic.Anthropic:
    s = get_settings()
    if s.anthropic_api_key is None:
        raise RuntimeError("anthropic_api_key not configured (Keychain or env)")
    return anthropic.Anthropic(api_key=s.anthropic_api_key.get_secret_value())


def _llm_calls_log(run_id: str) -> Path:
    s = get_settings()
    d = s.traces_dir / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "llm_calls.jsonl"


def _append_log(run_id: str, entry: dict) -> None:  # type: ignore[type-arg]
    with _llm_calls_log(run_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _extract_json(text: str) -> dict:  # type: ignore[type-arg]
    """Tolerate models that wrap JSON in ```json fences."""
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in: {text[:200]!r}")
    return json.loads(m.group(0))


def _cost_cents(model: str, in_tok: int, out_tok: int) -> int:
    p = _PRICING_PER_M.get(model)
    if p is None:
        return 0
    usd = (in_tok / 1_000_000) * p["in"] + (out_tok / 1_000_000) * p["out"]
    return round(usd * 100)


def resolve_with_claude(
    page: Page,
    step: str,
    intent: str,
    failed_selectors: list[str],
    run_id: str = "ad-hoc",
    model: str | None = None,
) -> Decision:
    """Take a screenshot, ask Claude for the right selector. Returns parsed Decision.

    Raises on transport failure or schema-invalid response — caller (escalation.py)
    decides whether to retry / upgrade to Opus / give up.
    """
    log = bind(run_id=run_id)
    settings = get_settings()
    model = model or settings.llm_model_primary

    shot = page.screenshot(full_page=False)
    b64 = base64.b64encode(shot).decode()

    user_msg = build_user_message(step, intent, failed_selectors)

    started = datetime.now(UTC)
    msg = _client().messages.create(
        model=model,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": user_msg},
                ],
            }
        ],
    )

    raw_text = "".join(b.text for b in msg.content if hasattr(b, "text"))
    in_tok = msg.usage.input_tokens
    out_tok = msg.usage.output_tokens
    cost = _cost_cents(model, in_tok, out_tok)

    log_entry = {
        "ts": started.isoformat(),
        "model": model,
        "step": step,
        "intent": intent,
        "failed_selectors": failed_selectors,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_cents": cost,
        "raw_text": raw_text,
    }

    try:
        data = _extract_json(raw_text)
        decision = Decision.model_validate(data)
        log_entry["parsed"] = decision.model_dump()
        _append_log(run_id, log_entry)
        log.info(
            f"[vision] {step} model={model} cost=¢{cost} actions={len(decision.actions)} "
            f"blocked={decision.is_blocked}"
        )
        return decision
    except Exception as e:
        log_entry["parse_error"] = f"{type(e).__name__}: {e}"
        _append_log(run_id, log_entry)
        log.warning(f"[vision] parse failed: {e}")
        raise
