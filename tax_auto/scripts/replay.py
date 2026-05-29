"""Open a past run's trace in Playwright trace viewer.

Usage:
    uv run python scripts/replay.py 01HXXXXXXXXXXX
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tax_auto.config import get_settings


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/replay.py <run_id>", file=sys.stderr)
        return 2
    run_id = sys.argv[1]
    trace = get_settings().traces_dir / run_id / "trace.zip"
    if not trace.exists():
        print(f"no trace at {trace}", file=sys.stderr)
        return 1
    # `playwright show-trace` opens an interactive viewer
    subprocess.run(["uv", "run", "playwright", "show-trace", str(trace)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
