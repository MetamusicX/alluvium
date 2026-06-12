#!/bin/bash
# Alluvium Setup Script
# Installs dependencies and configures daily auto-processing.

set -e

ALLUVIUM_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.alluvium.process.plist"
PLIST_SRC="$ALLUVIUM_DIR/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "=== Alluvium Setup ==="
echo

# 1. Install Python dependencies
echo "Installing Python dependencies..."
pip3 install pyyaml
echo

# 2. API key (skip if you set provider: claude-code in config.yaml,
#    or if you prefer to export the key in your shell profile)
echo "Enter your API key for the provider set in config.yaml"
echo "(default provider: anthropic — press Enter to skip):"
read -r API_KEY
API_KEY="${API_KEY:-}"

# 3. Choose processing time
echo
echo "What time should Alluvium process your journal? (24h format)"
echo "Alluvium processes YESTERDAY's entry, so a morning hour works best."
echo "Default: 6"
read -r HOUR
HOUR="${HOUR:-6}"

echo
echo "Minute? Default: 30"
read -r MINUTE
MINUTE="${MINUTE:-30}"

# 4. Create logs directory
mkdir -p "$ALLUVIUM_DIR/logs"

# 5. Generate personalised plist
sed -e "s|ALLUVIUM_PATH|$ALLUVIUM_DIR|g" \
    -e "s|YOUR_API_KEY_HERE|$API_KEY|g" \
    -e "s|<integer>6</integer>|<integer>$HOUR</integer>|" \
    -e "s|<integer>30</integer>|<integer>$MINUTE</integer>|" \
    "$PLIST_SRC" > "$PLIST_DEST"

# 6. Load the agent
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

# 7. Verify the installation
echo
python3 "$ALLUVIUM_DIR/healthcheck.py" || true

echo
echo "=== Setup complete ==="
echo
echo "Alluvium will process yesterday's journal every day at $HOUR:$(printf '%02d' "$MINUTE")."
echo
echo "To write your journal:  Open this folder as an Obsidian vault, write in 00 Journal/YYYY-MM-DD.md"
echo "To process manually:    python3 \"$ALLUVIUM_DIR/process_journal.py\" [YYYY-MM-DD]"
echo "To change the time:     Re-run this setup script"
echo "Logs:                   $ALLUVIUM_DIR/logs/"
