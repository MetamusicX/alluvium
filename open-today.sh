#!/bin/bash
# Opens today's journal entry in Obsidian. Creates the file if it doesn't exist.
# Update JOURNAL_DIR and vault name to match your setup.

JOURNAL_DIR="$(dirname "$0")/Journal"
TODAY=$(date +%Y-%m-%d)
FILEPATH="$JOURNAL_DIR/$TODAY.md"

# Create today's file if it doesn't exist
if [ ! -f "$FILEPATH" ]; then
    echo "# $(date +'%B %d, %Y')" > "$FILEPATH"
    echo "" >> "$FILEPATH"
fi

# Open in Obsidian using the obsidian:// URI
# Change "Alluvium" to your vault name if different
open "obsidian://open?vault=Alluvium&file=Journal/$TODAY"
