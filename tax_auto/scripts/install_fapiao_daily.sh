#!/usr/bin/env bash
# Install the 发票通 daily sync job into launchd.
# Fires at 03:00 local time every day. Idempotent: re-run replaces the existing plist.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/tax_auto/scheduler/fapiao_daily.plist.template"
LABEL="ai.tarry.fapiao.daily"
PLIST_OUT="$HOME/Library/LaunchAgents/$LABEL.plist"
UV_BIN="$(command -v uv)"

if [ -z "$UV_BIN" ]; then
    echo "error: uv not found in PATH" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$PROJECT_DIR/runtime/logs"

sed \
    -e "s|{{PROJECT_DIR}}|$PROJECT_DIR|g" \
    -e "s|{{UV_BIN}}|$UV_BIN|g" \
    -e "s|{{HOME}}|$HOME|g" \
    "$TEMPLATE" > "$PLIST_OUT"

# Unload first to avoid "already loaded" error on re-install
launchctl unload "$PLIST_OUT" 2>/dev/null || true
launchctl load "$PLIST_OUT"

echo "✓ installed: $PLIST_OUT"
echo "  fires daily at 03:00 local time"
echo ""
echo "Useful commands:"
echo "  launchctl list | grep fapiao            # verify"
echo "  launchctl start $LABEL                  # trigger now (test)"
echo "  launchctl unload $PLIST_OUT             # uninstall"
echo "  tail -F $PROJECT_DIR/runtime/logs/sync_daily.{out,err}.log"
