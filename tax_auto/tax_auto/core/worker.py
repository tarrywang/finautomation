"""Worker — runs one fetch task end-to-end, driving the state machine.

This is the orchestration layer. It owns:
  - state machine transitions + DB persistence
  - error → state mapping
  - face-verify wait + notify
  - run lifecycle: start → progress → DONE/FAILED
"""

from __future__ import annotations

import time
import traceback
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ulid import ULID

from tax_auto.config import get_settings
from tax_auto.core.errors import (
    DataIntegrityError,
    ExportTimeout,
    FaceVerifyRequired,
    HumanRequired,
    SelectorMissError,
    SessionExpired,
    TaxAutoError,
)
from tax_auto.core.session import open_session
from tax_auto.core.state_machine import RunState
from tax_auto.flow.steps import (
    RunContext,
    step_1_login_check,
    step_2_navigate_to_query,
    step_3_fill_query_form,
    step_4_select_and_export,
    step_5_handle_face_verify,
    step_6_poll_export_zip,
    step_7_download_and_archive,
)
from tax_auto.obs.logging import bind
from tax_auto.storage.db import session_scope
from tax_auto.storage.models import AuditLog, Run

if TYPE_CHECKING:
    from playwright.sync_api import Playwright


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _audit(run_id: str, action: str, detail: str | None = None) -> None:
    with session_scope() as sess:
        sess.add(AuditLog(run_id=run_id, actor="system", action=action, detail_json=detail))


def _persist_run(
    run_id: str,
    *,
    state: RunState,
    last_step: str | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
    ended: bool = False,
) -> None:
    with session_scope() as sess:
        row = sess.get(Run, run_id)
        if row:
            row.state = state.value
            if last_step:
                row.last_step = last_step
            if error_class:
                row.error_class = error_class
            if error_message:
                row.error_message = error_message[:2000]
            if ended:
                row.ended_at = _utcnow()
            sess.add(row)


def _create_run(tax_id: str, params: dict) -> str:  # type: ignore[type-arg]
    run_id = str(ULID())
    import json

    with session_scope() as sess:
        sess.add(Run(id=run_id, tax_id=tax_id, state=RunState.PENDING.value))
        sess.add(
            AuditLog(
                run_id=run_id, actor="system", action="RUN_START", detail_json=json.dumps(params)
            )
        )
    return run_id


def run_fetch(
    p: Playwright,
    tax_id: str,
    date_from: str,
    date_to: str,
    dry_run: bool = False,
) -> tuple[str, RunState, dict]:  # type: ignore[type-arg]
    """Run the full 7-step flow. Returns (run_id, final_state, summary_dict).

    The summary dict contains: state, expected_count, actual_count, duration_s,
    error_class, error_message.
    """
    settings = get_settings()
    run_id = _create_run(tax_id, {"date_from": date_from, "date_to": date_to, "dry_run": dry_run})
    log = bind(run_id=run_id, tax_id=tax_id)
    started = time.monotonic()
    log.info(f"run started: {date_from} → {date_to} dry_run={dry_run}")

    ctx = RunContext(
        run_id=run_id, tax_id=tax_id, date_from=date_from, date_to=date_to, dry_run=dry_run
    )
    state = RunState.AUTHENTICATING
    _persist_run(run_id, state=state)
    summary: dict = {"state": None, "duration_s": 0.0}  # type: ignore[type-arg]

    try:
        with open_session(p, tax_id) as (browser_ctx, page):
            # Optional trace recording — see W4 obs/trace.py
            try:
                from tax_auto.obs.trace import maybe_start_trace, stop_trace

                maybe_start_trace(browser_ctx, run_id)
            except ImportError:
                pass

            steps = [
                ("step_1_login_check", step_1_login_check, RunState.NAVIGATING),
                ("step_2_navigate_to_query", step_2_navigate_to_query, RunState.QUERYING),
                ("step_3_fill_query_form", step_3_fill_query_form, RunState.DOWNLOADING),
                ("step_4_select_and_export", step_4_select_and_export, RunState.POLLING),
                ("step_5_handle_face_verify", step_5_handle_face_verify, RunState.POLLING),
                ("step_6_poll_export_zip", step_6_poll_export_zip, RunState.ARCHIVING),
            ]

            for name, fn, next_state in steps:
                state = next_state if state == RunState.PENDING else state
                _persist_run(run_id, state=state, last_step=name)
                try:
                    result = fn(page, ctx)
                except FaceVerifyRequired as e:
                    log.warning(f"{name}: face verify required → notifying + waiting")
                    _persist_run(
                        run_id,
                        state=RunState.NEEDS_HUMAN,
                        last_step=name,
                        error_class="FaceVerifyRequired",
                        error_message=str(e),
                    )
                    _audit(run_id, "FACE_VERIFY_PROMPT", str(e))
                    _notify_face_verify(tax_id, run_id, page)
                    _wait_for_face_verify_to_clear(page, settings.timeout_face_verify_s)
                    # After human clears it, retry the same step
                    result = fn(page, ctx)

                if not result.ok:
                    state = RunState.FAILED
                    break
                state = result.next_state
                if state in (RunState.DONE, RunState.FAILED):
                    break

            # Step 7 only when we made it past step 6
            if state == RunState.ARCHIVING:
                _persist_run(run_id, state=state, last_step="step_7_download_and_archive")
                result = step_7_download_and_archive(page, ctx, browser_ctx)
                state = result.next_state
                summary["expected_count"] = ctx.expected_count
                summary["actual_count"] = result.artifacts.get("file_count", 0)
                summary["output_dir"] = result.artifacts.get("dir")

            try:
                from tax_auto.obs.trace import stop_trace

                stop_trace(browser_ctx, run_id)
            except ImportError:
                pass

        _persist_run(run_id, state=state, ended=True)
        _audit(run_id, "RUN_END", state.value)
        log.info(f"run ended: state={state.value}")

    except SessionExpired as e:
        log.error(f"session expired: {e}")
        state = RunState.FAILED
        _persist_run(
            run_id, state=state, error_class="SessionExpired", error_message=str(e), ended=True
        )
        summary["error_class"] = "SessionExpired"
        summary["error_message"] = str(e)
    except (HumanRequired, SelectorMissError, ExportTimeout, DataIntegrityError, TaxAutoError) as e:
        log.error(f"flow error: {type(e).__name__}: {e}")
        state = RunState.FAILED
        _persist_run(
            run_id, state=state, error_class=type(e).__name__, error_message=str(e), ended=True
        )
        summary["error_class"] = type(e).__name__
        summary["error_message"] = str(e)
    except Exception as e:
        log.exception("unhandled error in run")
        state = RunState.FAILED
        _persist_run(
            run_id,
            state=state,
            error_class="Unexpected",
            error_message=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            ended=True,
        )
        summary["error_class"] = type(e).__name__
        summary["error_message"] = str(e)

    summary["state"] = state.value
    summary["duration_s"] = round(time.monotonic() - started, 1)
    return run_id, state, summary


def _notify_face_verify(tax_id: str, run_id: str, page) -> None:  # type: ignore[no-untyped-def]
    """Snap a screenshot + email user. Lazy imports to avoid hard dep."""
    try:
        from tax_auto.obs.trace import screenshot_to_run_dir

        shot = screenshot_to_run_dir(page, run_id, "face_verify")
    except Exception:
        shot = None
    try:
        from tax_auto.notify.email import send_email
        from tax_auto.notify.templates import face_verify_body, face_verify_subject

        send_email(
            subject=face_verify_subject(tax_id),
            body=face_verify_body(tax_id, run_id, shot),
            level="CRITICAL",
        )
    except Exception:
        bind(run_id=run_id, tax_id=tax_id).warning("email notification failed (continuing)")


def _wait_for_face_verify_to_clear(page, timeout_s: int) -> None:  # type: ignore[no-untyped-def]
    """Block until the 扫脸 modal disappears or timeout."""
    from tax_auto.flow.selectors import get_candidates

    cands = get_candidates("step_5_face_verify", "modal_face_verify")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        any_visible = False
        for sel in cands:
            try:
                if page.locator(sel).first.is_visible(timeout=1000):
                    any_visible = True
                    break
            except Exception:
                continue
        if not any_visible:
            return
        page.wait_for_timeout(2000)
    raise FaceVerifyRequired("face verify did not clear in time")
