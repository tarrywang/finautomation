"""Parse docs/selectors.md → write tax_auto/flow/selectors.py.

Workflow:
  1. You fill docs/selectors.md with real selectors discovered during W1 recon.
  2. Run `uv run python scripts/sync_selectors.py` — this script:
     - Parses every `step_<n>_<name>` YAML block under a `## ` heading
     - Replaces the SELECTORS dict in selectors.py while preserving the module
       docstring + imports
     - Runs `uv run pytest tests/unit/test_selectors.py` to validate structure

You can re-run safely; it's idempotent.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELECTORS_MD = PROJECT_ROOT / "docs" / "selectors.md"
SELECTORS_PY = PROJECT_ROOT / "tax_auto" / "flow" / "selectors.py"


_STEP_HEADING = re.compile(r"^##\s+(step_\d+_[a-z_]+)\s*$", re.MULTILINE)
_FENCE = re.compile(r"```(?:yaml|yml)?\n(.*?)\n```", re.DOTALL)


def parse_md(text: str) -> dict[str, dict[str, list[str]]]:
    """Walk the markdown, collect each step's first ```yaml block."""
    out: dict[str, dict[str, list[str]]] = {}
    matches = list(_STEP_HEADING.finditer(text))
    if not matches:
        raise SystemExit(f"no `## step_*_*` headings found in {SELECTORS_MD}")

    for i, m in enumerate(matches):
        step = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end]
        fence = _FENCE.search(chunk)
        if not fence:
            print(f"[warn] {step}: no yaml fence, skipping")
            continue
        body = fence.group(1)
        try:
            data = yaml.safe_load(body) or {}
        except yaml.YAMLError as e:
            raise SystemExit(f"YAML parse failure in {step}: {e}") from e
        if not isinstance(data, dict):
            raise SystemExit(f"{step}: top-level YAML must be a mapping, got {type(data)}")

        keyed: dict[str, list[str]] = {}
        for key, value in data.items():
            if not isinstance(value, list) or not value:
                raise SystemExit(f"{step}/{key}: must be a non-empty list of selectors")
            keyed[key] = [str(v) for v in value]
        out[step] = keyed
    return out


_HEADER = '''"""Selector catalog — single source of truth for all DOM queries.

Generated from docs/selectors.md by scripts/sync_selectors.py.
DO NOT EDIT BY HAND — change docs/selectors.md and re-run sync.
"""

from __future__ import annotations

# fmt: off
SELECTORS: dict[str, dict[str, list[str]]] = '''

_FOOTER = '''  # noqa: E501
# fmt: on


def get_candidates(step: str, key: str) -> list[str]:
    """Return ordered candidate selectors. Raises KeyError if unknown."""
    return SELECTORS[step][key]
'''


def render_py(selectors: dict[str, dict[str, list[str]]]) -> str:
    """Pretty-print the dict as Python source."""
    import json

    body = json.dumps(selectors, ensure_ascii=False, indent=4)
    # JSON uses double-quoted strings — safe Python literal
    return _HEADER + body + "\n" + _FOOTER


def main() -> int:
    if not SELECTORS_MD.exists():
        print(f"missing {SELECTORS_MD}", file=sys.stderr)
        return 1
    text = SELECTORS_MD.read_text(encoding="utf-8")
    selectors = parse_md(text)

    if not selectors:
        print("parsed 0 steps; nothing to sync", file=sys.stderr)
        return 1

    py_src = render_py(selectors)
    SELECTORS_PY.write_text(py_src, encoding="utf-8")
    print(f"✓ wrote {SELECTORS_PY}")
    print("\nParsed structure:")
    for step, keys in selectors.items():
        print(f"  {step}: {len(keys)} elements")
        for k, cands in keys.items():
            print(f"    {k}: {len(cands)} candidate(s)")

    print("\nRunning tests/unit/test_selectors.py to validate structure...")
    import subprocess

    result = subprocess.run(
        ["uv", "run", "pytest", "tests/unit/test_selectors.py", "-q"],
        cwd=PROJECT_ROOT,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
