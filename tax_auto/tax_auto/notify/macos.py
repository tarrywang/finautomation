"""macOS native notifications via osascript. CRITICAL-only by convention."""

from __future__ import annotations

import subprocess

from tax_auto.obs.logging import bind


def notify(title: str, message: str) -> bool:
    """Show a macOS notification banner. Safe no-op on non-macOS."""
    log = bind()
    safe_msg = message.replace('"', '\\"')[:200]
    safe_title = title.replace('"', '\\"')[:80]
    script = f'display notification "{safe_msg}" with title "{safe_title}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=5,
            capture_output=True,
        )
        return True
    except FileNotFoundError:
        # not on macOS, ignore
        return False
    except subprocess.CalledProcessError as e:
        log.warning(f"[macos.notify] osascript failed: {e.stderr.decode(errors='replace')!r}")
        return False
    except subprocess.TimeoutExpired:
        log.warning("[macos.notify] timeout")
        return False
