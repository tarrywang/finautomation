"""Daily aggregated metrics. Written to runtime/metrics/daily.json after each run."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median

from sqlmodel import select

from tax_auto.config import get_settings
from tax_auto.storage.db import session_scope
from tax_auto.storage.models import Run


def _metrics_path() -> Path:
    s = get_settings()
    d = s.runtime_dir / "metrics"
    d.mkdir(parents=True, exist_ok=True)
    return d / "daily.json"


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(len(s) * p), len(s) - 1)
    return s[idx]


def compute_daily(target_date: date | None = None) -> dict:  # type: ignore[type-arg]
    """Aggregate runs that started on the target day (UTC)."""
    target_date = target_date or datetime.now(timezone.utc).date()
    day_str = target_date.isoformat()

    # Materialize tuples inside the session — avoid DetachedInstanceError after close
    with session_scope() as sess:
        rows = sess.exec(select(Run)).all()
        snapshots = [
            (r.started_at, r.ended_at, r.state, r.llm_calls, r.llm_cost_cents)
            for r in rows
        ]

    todays = [s for s in snapshots if s[0].date() == target_date]
    durations: list[float] = [
        (ended - started).total_seconds()
        for started, ended, _, _, _ in todays
        if ended is not None
    ]
    llm_total = sum(s[3] for s in todays)
    cost_total_cents = sum(s[4] for s in todays)
    done = sum(1 for s in todays if s[2] == "DONE")
    failed = sum(1 for s in todays if s[2] == "FAILED")

    metrics = {
        "date": day_str,
        "runs_total": len(todays),
        "runs_done": done,
        "runs_failed": failed,
        "llm_calls_total": llm_total,
        "llm_cost_cents": cost_total_cents,
        "p50_duration_s": median(durations) if durations else 0,
        "p95_duration_s": _percentile(durations, 0.95),
    }
    return metrics


def write_daily_snapshot() -> Path:
    metrics = compute_daily()
    path = _metrics_path()
    # Read existing, replace today's row
    history: list[dict] = []  # type: ignore[type-arg]
    if path.exists():
        try:
            existing = json.loads(path.read_text())
            history = [x for x in existing if x["date"] != metrics["date"]]
        except Exception:  # noqa: BLE001
            history = []
    history.append(metrics)
    history.sort(key=lambda x: x["date"])
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    return path
