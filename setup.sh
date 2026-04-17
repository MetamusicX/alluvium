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
pip3 install anthropic pyyaml
echo

# 2. Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Enter your Anthropic API key:"
    read -r API_KEY
else
    API_KEY="$ANTHROPIC_API_KEY"
fi

# 3. Choose processing time
echo
echo "What time should Alluvium process your daily journal? (24h format)"
echo "Default: 21 (9 PM)"
read -r HOUR
HOUR="${HOUR:-21}"

echo
echo "Minute? Default: 0"
read -r MINUTE
MINUTE="${MINUTE:-0}"

# 4. Create logs directory
mkdir -p "$ALLUVIUM_DIR/logs"

# 5. Generate personalised plist
sed -e "s|ALLUVIUM_PATH|$ALLUVIUM_DIR|g" \
    -e "s|YOUR_API_KEY_HERE|$API_KEY|g" \
    -e "s|<integer>21</integer>|<integer>$HOUR</integer>|" \
    -e "s|<integer>0</integer>|<integer>$MINUTE</integer>|" \
    "$PLIST_SRC" > "$PLIST_DEST"

# 6. Load the agent
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo
echo "=== Setup complete ==="
echo
echo "Alluvium will automatically process your journal every day at $HOUR:$(printf '%02d' $MINUTE)."
echo
echo "To write your journal:  Open Alluvium/ as an Obsidian vault"
echo "To process manually:    python3 $ALLUVIUM_DIR/process_journal.py"
echo "To change the time:     Re-run this setup script"
echo "Logs:                   $ALLUVIUM_DIR/logs/"
