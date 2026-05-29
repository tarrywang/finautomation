"""Pydantic schemas for vision fallback LLM I/O. See architecture §7.3.

The contract is intentionally narrow:
- LLM may only return semantic selectors (text=, role=, name=)
- A small denylist blocks selectors that would let the model execute arbitrary code
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ActionKind = Literal["click", "fill", "select", "wait", "abort"]


# Substrings that, if present in a selector, indicate something dangerous or out-of-band.
# Erring on the side of false positives — vision should NEVER need these.
_SELECTOR_DENY: tuple[str, ...] = (
    "javascript:",
    "eval(",
    "evaluate(",
    "page.goto",
    "Function(",
    "fetch(",
    "<script",
    "import(",
)


class Action(BaseModel):
    """A single low-level action the model wants Playwright to perform."""

    kind: ActionKind
    selector: str = Field(min_length=1, max_length=500)
    value: str | None = Field(default=None, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    reasoning: str = Field(default="", max_length=500)

    @field_validator("selector")
    @classmethod
    def _no_dangerous_selectors(cls, v: str) -> str:
        low = v.lower()
        for bad in _SELECTOR_DENY:
            if bad.lower() in low:
                raise ValueError(f"selector contains denied token {bad!r}: {v!r}")
        return v


class Decision(BaseModel):
    """Model's response: either a sequence of actions OR a 'blocked' signal."""

    actions: list[Action] = Field(default_factory=list, max_length=3)
    is_blocked: bool = False
    blocked_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _must_explain_when_blocked(self) -> "Decision":
        if self.is_blocked and not self.blocked_reason:
            raise ValueError("blocked_reason required when is_blocked=true")
        return self
