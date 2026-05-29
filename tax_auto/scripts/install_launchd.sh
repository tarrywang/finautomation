#!/usr/bin/env bash
# Install tax_auto monthly job into launchd.
# Idempotent: re-running replaces the existing plist.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/tax_auto/scheduler/launchd.plist.template"
LABEL="ai.tarry.tax_auto.monthly"
PLIST_OUT="$HOME/Library/LaunchAgents/$LABEL.plist"
UV_BIN="$(command -v uv)"

if [ -z "$UV_BIN" ]; then
    echo "error: uv not found in PATH" >&2
    exit 1
fi

# Default to "previous calendar month" placeholder so the plist always picks
# the month *before* the run date. We re-render the plist via cron-like nudge:
# easier alternative — recompute via a wrapper. For now, hardcode YYYY-MM and
# document the re-render requirement in RUNBOOK.md.
MONTH="${1:-$(date -v-1m +%Y-%m 2>/dev/null || date -d 'last month' +%Y-%m)}"

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$PROJECT_DIR/runtime/logs"

sed \
    -e "s|{{PROJECT_DIR}}|$PROJECT_DIR|g" \
    -e "s|{{UV_BIN}}|$UV_BIN|g" \
    -e "s|{{HOME}}|$HOME|g" \
    -e "s|{{MONTH_PLACEHOLDER_PASSED_AT_INSTALL}}|$MONTH|g" \
    "$TEMPLATE" > "$PLIST_OUT"

# Unload first to avoid "already loaded" error on re-install
launchctl unload "$PLIST_OUT" 2>/dev/null || true
launchctl load "$PLIST_OUT"

echo "✓ installed: $PLIST_OUT"
echo "  fires on day 5 of each month at 18:00 for month=$MONTH"
echo ""
echo "Useful commands:"
echo "  launchctl list | grep tax_auto         # verify"
echo "  launchctl unload $PLIST_OUT            # uninstall"
echo "  launchctl start $LABEL                 # trigger immediately"
echo "  tail -F $PROJECT_DIR/runtime/logs/launchd.{out,err}.log"
