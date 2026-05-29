"""tax_auto CLI entry point (Typer). Commands implemented progressively across W1-W6."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="tax-auto",
    help="Shanghai e-tax invoice automation · Playwright + Claude vision fallback",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print version."""
    from tax_auto import __version__

    typer.echo(f"tax-auto {__version__}")


@app.command()
def doctor() -> None:
    """Verify environment: paths, secrets, system Chrome (ADR-0002)."""
    from tax_auto.config import get_settings

    s = get_settings()
    typer.echo(f"runtime_dir       : {s.runtime_dir.resolve()}")
    typer.echo(f"anthropic_api_key : {'✓ set' if s.anthropic_api_key else '✗ MISSING'}")
    typer.echo(f"smtp_password     : {'✓ set' if s.smtp_password else '✗ MISSING'}")
    typer.echo(f"notify_to         : {s.notify_to or '(unset)'}")
    typer.echo(f"llm_model_primary : {s.llm_model_primary}")
    typer.echo(f"max_llm_calls/run : {s.llm_max_calls_per_run}")

    # Verify system Chrome is drivable via Playwright (ADR-0002)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome", headless=True)
            ver = b.version
            b.close()
        typer.echo(f"system_chrome     : ✓ {ver}")
    except Exception as e:
        typer.echo(f"system_chrome     : ✗ {type(e).__name__}: {e}")
        raise typer.Exit(code=1) from e


# Placeholders for W2+ commands — registered so `--help` shows the surface area.


@app.command()
def login(
    tax_id: str = typer.Option(..., "--tax-id", help="统一社会信用代码"),
    timeout: int = typer.Option(300, "--timeout", help="seconds to wait for login"),
) -> None:
    """Interactive login: opens headed Chrome, waits for you to complete password + 扫脸."""
    from playwright.sync_api import sync_playwright

    from tax_auto.core.session import login_interactive
    from tax_auto.storage.db import init_db

    init_db()
    with sync_playwright() as p:
        login_interactive(p, tax_id, timeout_s=timeout)
    typer.echo(f"✓ {tax_id} logged in. Session cookies saved to runtime/session/{tax_id}/")


@app.command("sessions")
def list_sessions_cmd() -> None:
    """List all known sessions and their status."""
    from tax_auto.core.session import list_sessions
    from tax_auto.storage.db import init_db

    init_db()
    rows = list_sessions()
    if not rows:
        typer.echo("(no sessions yet — run `tax-auto login --tax-id ...`)")
        return
    for tax_id, status, last_login in rows:
        typer.echo(f"{tax_id}  {status:<8}  last_login={last_login or '-'}")


fetch_app = typer.Typer(help="[W2-W3] Fetch invoices from e-tax bureau")
app.add_typer(fetch_app, name="fetch")


@fetch_app.command("list")
def fetch_list(
    tax_id: str = typer.Option(..., "--tax-id"),
    date_from: str = typer.Option(..., "--from"),
    date_to: str = typer.Option(..., "--to"),
) -> None:
    """Navigate to query page and print invoice count. No download (dry-run)."""
    from playwright.sync_api import sync_playwright

    from tax_auto.core.worker import run_fetch
    from tax_auto.storage.db import init_db

    init_db()
    with sync_playwright() as p:
        run_id, state, summary = run_fetch(
            p,
            tax_id,
            date_from=date_from,
            date_to=date_to,
            dry_run=True,
        )
    typer.echo(f"run_id={run_id}  state={state.value}  summary={summary}")
    if state.value == "FAILED":
        raise typer.Exit(code=1)


@fetch_app.command("run")
def fetch_run(
    tax_id: str = typer.Option(..., "--tax-id"),
    month: str = typer.Option(..., "--month", help="YYYY-MM"),
) -> None:
    """Full 7-step flow: query → select → export → poll → download → archive."""
    from calendar import monthrange

    from playwright.sync_api import sync_playwright

    from tax_auto.core.worker import run_fetch
    from tax_auto.storage.db import init_db

    try:
        year, mon = map(int, month.split("-"))
    except ValueError as e:
        raise typer.BadParameter("--month must be YYYY-MM") from e
    date_from = f"{year:04d}-{mon:02d}-01"
    date_to = f"{year:04d}-{mon:02d}-{monthrange(year, mon)[1]:02d}"

    init_db()
    with sync_playwright() as p:
        run_id, state, summary = run_fetch(
            p,
            tax_id,
            date_from=date_from,
            date_to=date_to,
            dry_run=False,
        )
    typer.echo(f"run_id={run_id}  state={state.value}  summary={summary}")
    if state.value == "FAILED":
        raise typer.Exit(code=1)


db_app = typer.Typer(help="Database management")
app.add_typer(db_app, name="db")


@db_app.command("init")
def db_init() -> None:
    """Create SQLite schema (idempotent)."""
    from tax_auto.storage.db import get_engine, init_db

    init_db()
    typer.echo(f"✓ schema initialized at {get_engine().url}")


# ── Customer management ──────────────────────────────────────────
customer_app = typer.Typer(help="Customer registry")
app.add_typer(customer_app, name="customer")


@customer_app.command("add")
def customer_add(
    tax_id: str = typer.Option(..., "--tax-id"),
    alias: str = typer.Option(..., "--alias"),
    contact: str | None = typer.Option(None, "--contact"),
    notification_level: str = typer.Option("INFO", "--notify-level"),
    use_china_llm: bool = typer.Option(False, "--china-llm"),
) -> None:
    """Add or update a customer in the registry."""
    from tax_auto.storage.customers import add_customer
    from tax_auto.storage.db import init_db

    init_db()
    add_customer(tax_id, alias, contact, notification_level, use_china_llm)
    typer.echo(f"✓ customer {tax_id} ({alias}) saved")


@customer_app.command("disable")
def customer_disable(tax_id: str = typer.Argument(...)) -> None:
    """Mark a customer as inactive (no future runs)."""
    from tax_auto.storage.customers import disable_customer
    from tax_auto.storage.db import init_db

    init_db()
    disable_customer(tax_id)
    typer.echo(f"✓ {tax_id} disabled")


@customer_app.command("list")
def customer_list() -> None:
    """Show all active customers."""
    from tax_auto.storage.customers import list_active_customers
    from tax_auto.storage.db import init_db

    init_db()
    customers = list_active_customers()
    if not customers:
        typer.echo("(no customers — `tax-auto customer add` to register)")
        return
    for c in customers:
        notify_china = " [china-llm]" if c.use_china_llm else ""
        typer.echo(f"{c.tax_id}  {c.alias:<24}  {c.notification_level}{notify_china}")


# ── Scheduled run entry point ────────────────────────────────────
@app.command("run")
def run_cmd(
    month: str = typer.Option(..., "--month", help="YYYY-MM"),
    tax_id: list[str] = typer.Option(  # noqa: B008
        [],
        "--tax-id",
        help="restrict to specific customer(s); default = all active",
    ),
) -> None:
    """Run fetch for a month across customers (or a subset)."""
    from tax_auto.scheduler.scheduler import run_for_month
    from tax_auto.storage.db import init_db

    init_db()
    results = run_for_month(month, tax_ids=list(tax_id) or None)
    for r in results:
        typer.echo(
            f"{r['tax_id']:<24} {r['state']:<10} "
            f"invoices={r.get('actual_count', 0)} "
            f"err={r.get('error_class', '-')}"
        )
    if any(r["state"] in ("FAILED",) for r in results):
        raise typer.Exit(code=1)


# ── Metrics ──────────────────────────────────────────────────────
@app.command("metrics")
def metrics_cmd() -> None:
    """Compute today's aggregated metrics and write to runtime/metrics/daily.json."""
    from tax_auto.obs.metrics import compute_daily, write_daily_snapshot
    from tax_auto.storage.db import init_db

    init_db()
    today = compute_daily()
    path = write_daily_snapshot()
    typer.echo(f"{today}")
    typer.echo(f"snapshot → {path}")


# ── Replay (alias for scripts/replay.py) ─────────────────────────
@app.command("replay")
def replay_cmd(run_id: str = typer.Argument(...)) -> None:
    """Open Playwright trace viewer for a past run."""
    import subprocess

    from tax_auto.config import get_settings

    trace = get_settings().traces_dir / run_id / "trace.zip"
    if not trace.exists():
        typer.echo(f"✗ no trace at {trace}", err=True)
        raise typer.Exit(code=1)
    subprocess.run(["uv", "run", "playwright", "show-trace", str(trace)], check=False)


if __name__ == "__main__":
    app()
