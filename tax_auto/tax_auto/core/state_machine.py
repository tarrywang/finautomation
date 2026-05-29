"""Run / Session state machines. See docs/architecture.md §5."""

from __future__ import annotations

from enum import Enum

from tax_auto.core.errors import TaxAutoError


class RunState(str, Enum):
    """Lifecycle of a single fetch run."""

    PENDING = "PENDING"
    AUTHENTICATING = "AUTHENTICATING"
    NAVIGATING = "NAVIGATING"
    QUERYING = "QUERYING"
    DOWNLOADING = "DOWNLOADING"
    POLLING = "POLLING"
    ARCHIVING = "ARCHIVING"
    VISION_FALLBACK = "VISION_FALLBACK"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    DONE = "DONE"
    FAILED = "FAILED"


class SessionState(str, Enum):
    """Browser session lifecycle per tax_id."""

    FRESH = "FRESH"  # just logged in, never used
    VALID = "VALID"  # confirmed working
    LOCKED = "LOCKED"  # another worker has it
    EXPIRED = "EXPIRED"  # cookies invalid, needs re-login


# ── Allowed Run transitions ────────────────────────────────────────
# Encode as adjacency set. Anything not listed is rejected.
_RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.PENDING: frozenset({RunState.AUTHENTICATING, RunState.FAILED}),
    RunState.AUTHENTICATING: frozenset(
        {
            RunState.NAVIGATING,
            RunState.NEEDS_HUMAN,
            RunState.FAILED,
        }
    ),
    RunState.NAVIGATING: frozenset(
        {
            RunState.QUERYING,
            RunState.VISION_FALLBACK,
            RunState.NEEDS_HUMAN,
            RunState.FAILED,
        }
    ),
    RunState.QUERYING: frozenset(
        {
            RunState.DOWNLOADING,
            RunState.VISION_FALLBACK,
            RunState.NEEDS_HUMAN,
            RunState.FAILED,
            RunState.DONE,  # empty result is a valid done state
        }
    ),
    RunState.DOWNLOADING: frozenset(
        {
            RunState.POLLING,
            RunState.VISION_FALLBACK,
            RunState.NEEDS_HUMAN,
            RunState.FAILED,
        }
    ),
    RunState.POLLING: frozenset(
        {
            RunState.ARCHIVING,
            RunState.VISION_FALLBACK,
            RunState.NEEDS_HUMAN,
            RunState.FAILED,
        }
    ),
    RunState.ARCHIVING: frozenset({RunState.DONE, RunState.FAILED}),
    # Vision fallback returns to the state it interrupted — encoded by enabling everything
    # that can lead INTO vision fallback. Caller tracks the prior state.
    RunState.VISION_FALLBACK: frozenset(
        {
            RunState.NAVIGATING,
            RunState.QUERYING,
            RunState.DOWNLOADING,
            RunState.POLLING,
            RunState.NEEDS_HUMAN,
            RunState.FAILED,
        }
    ),
    RunState.NEEDS_HUMAN: frozenset(
        {
            RunState.NAVIGATING,
            RunState.QUERYING,
            RunState.DOWNLOADING,
            RunState.POLLING,
            RunState.ARCHIVING,
            RunState.FAILED,
        }
    ),
    # Terminal states
    RunState.DONE: frozenset(),
    RunState.FAILED: frozenset(),
}

TERMINAL_RUN_STATES: frozenset[RunState] = frozenset({RunState.DONE, RunState.FAILED})


class IllegalTransition(TaxAutoError):
    """Attempted a transition not allowed by the state machine."""


def can_transition(src: RunState, dst: RunState) -> bool:
    return dst in _RUN_TRANSITIONS.get(src, frozenset())


def assert_transition(src: RunState, dst: RunState) -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(f"{src.value} → {dst.value} not allowed")


# ── Session transitions ────────────────────────────────────────────
_SESSION_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.FRESH: frozenset({SessionState.VALID, SessionState.LOCKED, SessionState.EXPIRED}),
    SessionState.VALID: frozenset({SessionState.LOCKED, SessionState.EXPIRED}),
    SessionState.LOCKED: frozenset({SessionState.VALID, SessionState.EXPIRED}),
    SessionState.EXPIRED: frozenset({SessionState.FRESH}),  # only via re-login
}


def can_session_transition(src: SessionState, dst: SessionState) -> bool:
    return dst in _SESSION_TRANSITIONS.get(src, frozenset())
