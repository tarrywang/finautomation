"""The 7-step fetch flow. See docs/architecture.md §6.

Each step is a pure function with signature (page, ctx) → StepResult.
The orchestrator (worker.run) drives the state machine + persistence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tax_auto.config import get_settings
from tax_auto.core.errors import (
    DataIntegrityError,
    ExportTimeout,
    FaceVerifyRequired,
    SelectorMissError,
)
from tax_auto.core.session import assert_session_alive
from tax_auto.core.state_machine import RunState
from tax_auto.flow import waits
from tax_auto.obs.logging import bind

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page

ETAX_QUERY_URL = "https://etax.shanghai.chinatax.gov.cn/"  # TODO(W1): exact deep link


# ── Run context: shared mutable bag passed step-to-step ────────────
@dataclass
class RunContext:
    run_id: str
    tax_id: str
    date_from: str  # YYYY-MM-DD
    date_to: str    # YYYY-MM-DD
    dry_run: bool = False  # if True, step_4 stops before actually clicking 下载

    # Filled progressively
    expected_count: int = 0
    export_task_id: str | None = None
    zip_path: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    ok: bool
    next_state: RunState
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: BaseException | None = None


# ────────────────────────── Step 1 ─────────────────────────────────
def step_1_login_check(page: Page, ctx: RunContext) -> StepResult:
    """Verify session is still valid (not redirected to login)."""
    log = bind(run_id=ctx.run_id, tax_id=ctx.tax_id)
    log.info("step_1 login_check: opening etax landing")
    page.goto(ETAX_QUERY_URL)
    waits.wait_for_idle(page)
    assert_session_alive(page, ctx.tax_id)  # raises SessionExpired
    log.info("step_1 ✓ session alive")
    return StepResult(ok=True, next_state=RunState.NAVIGATING)


# ────────────────────────── Step 2 ─────────────────────────────────
def step_2_navigate_to_query(page: Page, ctx: RunContext) -> StepResult:
    """Navigate: 我要办税 → 税务数字账户 → 全量发票查询."""
    log = bind(run_id=ctx.run_id, tax_id=ctx.tax_id)
    log.info("step_2 navigate_to_query")

    waits.safe_click(page, "step_2_navigate", "menu_办税", intent="进入办税主菜单")
    waits.wait_for_idle(page)
    waits.safe_click(page, "step_2_navigate", "menu_税务数字账户", intent="进入数字账户")
    waits.wait_for_idle(page)
    waits.safe_click(page, "step_2_navigate", "link_全量发票查询", intent="打开全量发票查询页")
    waits.wait_for_idle(page)
    # Verify we landed correctly
    waits.safe_wait_visible(page, "step_2_navigate", "page_loaded_anchor",
                             intent="确认查询页加载完成")
    return StepResult(ok=True, next_state=RunState.QUERYING)


# ────────────────────────── Step 3 ─────────────────────────────────
_COUNT_RE = re.compile(r"共\s*(\d+)\s*[条张]")


def step_3_fill_query_form(page: Page, ctx: RunContext) -> StepResult:
    """Fill date range, click 查询, read result count."""
    log = bind(run_id=ctx.run_id, tax_id=ctx.tax_id)
    log.info("step_3 fill_query_form", date_from=ctx.date_from, date_to=ctx.date_to)

    waits.safe_fill(page, "step_3_fill_query", "input_date_from", ctx.date_from,
                    intent="填开票日期起")
    waits.safe_fill(page, "step_3_fill_query", "input_date_to", ctx.date_to,
                    intent="填开票日期止")
    waits.safe_click(page, "step_3_fill_query", "button_query", intent="提交查询")
    waits.wait_for_idle(page)

    # Empty result is a valid terminal state
    if waits.is_visible(page, "step_3_fill_query", "empty_state"):
        log.info("step_3 ✓ empty result")
        ctx.expected_count = 0
        return StepResult(ok=True, next_state=RunState.DONE,
                          artifacts={"expected_count": 0})

    # Parse "共 N 张/条"
    try:
        label = waits.safe_wait_visible(
            page, "step_3_fill_query", "result_count_label",
            intent="读取结果总数",
        )
        text = label.inner_text()
        match = _COUNT_RE.search(text)
        ctx.expected_count = int(match.group(1)) if match else 0
    except SelectorMissError:
        ctx.expected_count = 0

    log.info(f"step_3 ✓ expected_count={ctx.expected_count}")
    return StepResult(ok=True, next_state=RunState.DOWNLOADING,
                      artifacts={"expected_count": ctx.expected_count})


# ────────────────────────── Step 4 ─────────────────────────────────
def step_4_select_and_export(page: Page, ctx: RunContext) -> StepResult:
    """Select all + click batch download + pick OFD format + confirm."""
    log = bind(run_id=ctx.run_id, tax_id=ctx.tax_id)
    log.info("step_4 select_and_export")

    if ctx.dry_run:
        log.info("step_4 dry-run: stopping before click")
        return StepResult(ok=True, next_state=RunState.DONE, artifacts={"dry_run": True})

    waits.safe_click(page, "step_4_select_export", "checkbox_select_all",
                     intent="全选当前页发票")
    waits.safe_click(page, "step_4_select_export", "button_batch_download",
                     intent="点击批量下载")
    waits.wait_for_idle(page)
    # Default OFD per ADR-0006 — fall back to PDF if OFD missing
    try:
        waits.safe_click(page, "step_4_select_export", "format_ofd_radio",
                         intent="选 OFD 格式")
    except SelectorMissError:
        log.warning("OFD radio missing → falling back to PDF")
        waits.safe_click(page, "step_4_select_export", "format_pdf_radio",
                         intent="选 PDF 格式")
    waits.safe_click(page, "step_4_select_export", "button_confirm_export",
                     intent="确认提交导出任务")
    waits.safe_wait_visible(page, "step_4_select_export", "toast_submitted",
                            intent="等任务提交成功 toast")
    log.info("step_4 ✓ export task submitted")
    return StepResult(ok=True, next_state=RunState.POLLING)


# ────────────────────────── Step 5 ─────────────────────────────────
def step_5_handle_face_verify(page: Page, ctx: RunContext) -> StepResult:
    """Detect 扫脸 modal; raise FaceVerifyRequired so worker can notify + wait."""
    if waits.is_visible(page, "step_5_face_verify", "modal_face_verify", timeout_ms=3000):
        raise FaceVerifyRequired(f"{ctx.tax_id} requires face verification")
    return StepResult(ok=True, next_state=RunState.POLLING)


# ────────────────────────── Step 6 ─────────────────────────────────
def step_6_poll_export_zip(page: Page, ctx: RunContext) -> StepResult:
    """Poll the export-progress page until the ZIP becomes available."""
    log = bind(run_id=ctx.run_id, tax_id=ctx.tax_id)
    settings = get_settings()
    deadline_ms = settings.timeout_export_poll_s * 1000
    poll_interval_s = 5

    waits.safe_click(page, "step_6_poll_export", "link_导入导出", intent="跳转任务进度页")
    waits.wait_for_idle(page)

    import time
    started = time.monotonic()
    while True:
        if waits.is_visible(page, "step_6_poll_export", "status_done", timeout_ms=1500):
            log.info("step_6 ✓ zip ready")
            link = waits.safe_wait_visible(page, "step_6_poll_export", "link_download_zip",
                                            intent="拿到下载链接")
            ctx.artifacts["download_locator"] = link
            return StepResult(ok=True, next_state=RunState.ARCHIVING)

        elapsed_s = time.monotonic() - started
        if elapsed_s * 1000 > deadline_ms:
            raise ExportTimeout(
                f"ZIP not ready within {settings.timeout_export_poll_s}s"
            )
        log.debug(f"step_6 polling... elapsed={elapsed_s:.0f}s")
        page.wait_for_timeout(poll_interval_s * 1000)
        page.reload()
        waits.wait_for_idle(page)


# ────────────────────────── Step 7 ─────────────────────────────────
def step_7_download_and_archive(
    page: Page, ctx: RunContext, ctx_browser: BrowserContext
) -> StepResult:
    """Download the ZIP, unzip, record invoices, verify integrity."""
    from tax_auto.flow.downloads import download_and_extract

    log = bind(run_id=ctx.run_id, tax_id=ctx.tax_id)
    log.info("step_7 download_and_archive")

    download_link = ctx.artifacts.get("download_locator")
    if download_link is None:
        raise SelectorMissError(
            step="step_7", selectors=["download_locator"],
            intent="step_6 没把 download_locator 传过来",
        )

    extracted_paths, target_dir = download_and_extract(
        page=page, link_locator=download_link, tax_id=ctx.tax_id,
        run_id=ctx.run_id, month_key=ctx.date_from[:7],
    )
    ctx.zip_path = str(target_dir)
    actual_count = len(extracted_paths)

    if ctx.expected_count > 0 and actual_count != ctx.expected_count:
        raise DataIntegrityError(
            f"expected {ctx.expected_count} invoices, got {actual_count}"
        )

    log.info(f"step_7 ✓ archived {actual_count} files to {target_dir}")
    return StepResult(
        ok=True,
        next_state=RunState.DONE,
        artifacts={"file_count": actual_count, "dir": str(target_dir)},
    )
