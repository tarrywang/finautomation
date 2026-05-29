"""Verify state machine forbids illegal transitions."""

from __future__ import annotations

import pytest

from tax_auto.core.state_machine import (
    IllegalTransition,
    RunState,
    SessionState,
    assert_transition,
    can_session_transition,
    can_transition,
)


def test_legal_happy_path() -> None:
    legal_seq = [
        RunState.PENDING, RunState.AUTHENTICATING, RunState.NAVIGATING,
        RunState.QUERYING, RunState.DOWNLOADING, RunState.POLLING,
        RunState.ARCHIVING, RunState.DONE,
    ]
    for src, dst in zip(legal_seq, legal_seq[1:]):
        assert can_transition(src, dst), f"{src} → {dst} should be legal"


def test_pending_cannot_jump_to_done() -> None:
    assert not can_transition(RunState.PENDING, RunState.DONE)
    with pytest.raises(IllegalTransition):
        assert_transition(RunState.PENDING, RunState.DONE)


def test_terminal_states_have_no_outgoing() -> None:
    for terminal in (RunState.DONE, RunState.FAILED):
        for dst in RunState:
            assert not can_transition(terminal, dst), (
                f"{terminal} should not transition to {dst}"
            )


def test_querying_can_short_circuit_to_done_on_empty_result() -> None:
    """If query returns 0 invoices, we go straight from QUERYING to DONE."""
    assert can_transition(RunState.QUERYING, RunState.DONE)


def test_vision_fallback_can_return_to_any_mid_state() -> None:
    for dst in (RunState.NAVIGATING, RunState.QUERYING,
                RunState.DOWNLOADING, RunState.POLLING):
        assert can_transition(RunState.VISION_FALLBACK, dst)


def test_failed_is_reachable_from_everywhere_but_done() -> None:
    for src in RunState:
        if src in (RunState.DONE, RunState.FAILED):
            continue
        assert can_transition(src, RunState.FAILED), f"FAILED unreachable from {src}"


def test_session_expired_can_only_recover_via_fresh() -> None:
    assert can_session_transition(SessionState.EXPIRED, SessionState.FRESH)
    # Cannot skip back directly to VALID
    assert not can_session_transition(SessionState.EXPIRED, SessionState.VALID)
