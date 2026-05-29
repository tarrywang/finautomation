"""Daily metrics aggregation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tax_auto.config import get_settings
from tax_auto.obs.metrics import compute_daily, write_daily_snapshot
from tax_auto.storage.customers import add_customer
from tax_auto.storage.db import init_db, session_scope
from tax_auto.storage.models import Run


def test_empty_db_returns_zero_metrics() -> None:
    init_db()
    m = compute_daily()
    assert m["runs_total"] == 0
    assert m["runs_done"] == 0
    assert m["runs_failed"] == 0
    assert m["llm_calls_total"] == 0


def test_metrics_count_done_and_failed_separately() -> None:
    init_db()
    add_customer("91310000ZZZZZZZZ1", alias="x")
    now = datetime.now(timezone.utc)
    with session_scope() as sess:
        sess.add(Run(
            id="01R1", tax_id="91310000ZZZZZZZZ1",
            started_at=now, ended_at=now + timedelta(seconds=60),
            state="DONE", llm_calls=2, llm_cost_cents=5,
        ))
        sess.add(Run(
            id="01R2", tax_id="91310000ZZZZZZZZ1",
            started_at=now, ended_at=now + timedelta(seconds=120),
            state="FAILED",
        ))
    m = compute_daily()
    assert m["runs_total"] == 2
    assert m["runs_done"] == 1
    assert m["runs_failed"] == 1
    assert m["llm_calls_total"] == 2
    assert m["llm_cost_cents"] == 5
    assert m["p50_duration_s"] > 0


def test_write_snapshot_creates_json_file() -> None:
    init_db()
    path = write_daily_snapshot()
    assert path.exists()
    data = json.loads(path.read_text())
    assert isinstance(data, list)
    assert any("date" in row for row in data)
