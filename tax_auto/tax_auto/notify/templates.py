"""Email subject/body templates. Plain text — no HTML to keep it portable."""

from __future__ import annotations

from pathlib import Path


def face_verify_subject(tax_id: str) -> str:
    return f"⚠️ {tax_id} 触发二次扫脸,等待人工"


def face_verify_body(tax_id: str, run_id: str, screenshot: Path | None) -> str:
    shot_line = f"截图: {screenshot}" if screenshot else "(截图未生成)"
    return (
        f"客户: {tax_id}\n"
        f"运行: {run_id}\n"
        f"步骤: step_5_handle_face_verify\n"
        f"{shot_line}\n\n"
        "操作建议:\n"
        "  1. 在 Mac mini 上打开 Chrome 完成扫脸\n"
        "  2. 脚本会在扫脸通过后自动继续(默认等 180s)\n"
        "  3. 若已超时,重跑:`uv run tax-auto fetch run --tax-id <id> --month <YYYY-MM>`\n"
    )


def run_summary_subject(tax_id_count: int, invoice_count: int, has_failure: bool) -> str:
    icon = "🚨" if has_failure else "✅"
    return f"{icon} 拉到 {tax_id_count} 家 / {invoice_count} 张发票"


def run_summary_body(rows: list[tuple[str, str, int, str | None]]) -> str:
    """rows: [(tax_id, state, invoice_count, error_class), ...]"""
    lines = []
    for tax_id, state, count, err in rows:
        if err:
            lines.append(f"  ❌ {tax_id}  state={state}  err={err}")
        else:
            lines.append(f"  ✅ {tax_id}  state={state}  invoices={count}")
    return "本次运行结果:\n\n" + "\n".join(lines) + "\n"


def session_expiring_subject(tax_id: str) -> str:
    return f"🔑 {tax_id} session 即将过期,请续登"


def session_expiring_body(tax_id: str, last_login_iso: str) -> str:
    return (
        f"客户: {tax_id}\n"
        f"上次登录: {last_login_iso}\n\n"
        "请运行:\n"
        f"  uv run tax-auto login --tax-id {tax_id}\n"
    )
