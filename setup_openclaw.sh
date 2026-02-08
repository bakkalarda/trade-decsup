#!/usr/bin/env bash
# Setup script for OpenClaw integration with Trade DSS
# Run this after installing OpenClaw and the Python DSS
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$SCRIPT_DIR/openclaw"

echo "=== Trade DSS — OpenClaw Setup ==="
echo ""

# Step 1: Check prerequisites
echo "[1/6] Checking prerequisites..."

if ! command -v openclaw &> /dev/null; then
    echo "ERROR: openclaw not found. Install with: npm install -g openclaw@latest"
    exit 1
fi

if ! command -v dss &> /dev/null; then
    echo "WARNING: 'dss' CLI not found in PATH."
    echo "  Install with: cd $SCRIPT_DIR && pip install -e ."
    echo "  Or run: pip install -e $SCRIPT_DIR"
fi

if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found."
    exit 1
fi

echo "  OK"

# Step 2: Initialize database
echo "[2/6] Initializing SQLite database..."
cd "$SCRIPT_DIR"
python3 -c "from dss.storage.database import init_db; init_db(); print('  Database initialized')"

# Step 3: Copy workspace files
echo "[3/6] Workspace files are at: $WORKSPACE_DIR"
echo "  Set agents.defaults.workspace to this path in ~/.openclaw/openclaw.json"
echo "  Or symlink: ln -sf $WORKSPACE_DIR ~/.openclaw/workspace"

# Step 4: Set up cron jobs
echo "[4/6] Setting up cron jobs..."
echo ""
echo "Run the following openclaw commands to install cron jobs:"
echo ""

cat << 'CRON_COMMANDS'
# 4-Hour scan (every 4 hours, offset by 5 min for data settlement)
openclaw cron add \
  --name "4H Scan Cycle" \
  --cron "5 */4 * * *" \
  --tz "UTC" \
  --session isolated \
  --message "Run the 4H scan cycle. First search for macro headlines and event calendar. Then run: dss scan --asset all --timeframe 4h. Then run: dss position check. Report any alerts, position updates, or risk warnings to the user. If nothing notable, send a brief all-clear." \
  --announce \
  --channel telegram

# Daily update (after UTC daily close at 00:05)
openclaw cron add \
  --name "Daily Update" \
  --cron "5 0 * * *" \
  --tz "UTC" \
  --session isolated \
  --message "Run the daily update cycle. Run dss scan --asset all --timeframe 4h for all assets. Run dss structure --asset BTC --timeframe 1d, then ETH, XRP, SOL, XAU, XAG. Note any stage transitions. Check positions. Write a daily summary to memory. Report to user." \
  --announce \
  --channel telegram

# Weekly update (Monday after weekly close)
openclaw cron add \
  --name "Weekly Update" \
  --cron "5 0 * * 1" \
  --tz "UTC" \
  --session isolated \
  --model "opus" \
  --thinking high \
  --message "Run the weekly update. Run dss macro-state for regime assessment. Run dss structure --asset <X> --timeframe 1w for all 6 assets. Run dss zones --asset <X> --timeframe 1w for all assets. Reassess macro regime with web_search for weekly economic outlook. Write a comprehensive weekly summary to memory and send to user." \
  --announce \
  --channel telegram

# Position check (every 30 minutes)
openclaw cron add \
  --name "Position Check" \
  --cron "*/30 * * * *" \
  --tz "UTC" \
  --session isolated \
  --message "Run dss position check. If any stops hit, targets reached, or positions need attention, alert the user immediately. If all positions are fine, do not send a message (reply HEARTBEAT_OK)." \
  --announce \
  --channel telegram
CRON_COMMANDS

echo ""

# Step 5: Telegram setup reminder
echo "[5/6] Telegram channel setup"
echo "  Make sure Telegram is configured in OpenClaw:"
echo "    openclaw channels login telegram"
echo "  Then set delivery target in cron jobs:"
echo "    --to '<your-telegram-chat-id>'"

# Step 6: Test
echo "[6/6] Test the setup:"
echo "  dss status               # Check DSS health"
echo "  dss data refresh --asset BTC --timeframe 4h  # Test data fetch"
echo "  dss scan --asset BTC --timeframe 4h          # Test full scan"
echo ""
echo "=== Setup complete ==="
