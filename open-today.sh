#!/bin/bash
# Alluvium — Create today's journal note and open it in Obsidian.
# Add this as a macOS Login Item to start every morning with a blank page ready.

ALLUVIUM_DIR="$(cd "$(dirname "$0")" && pwd)"
JOURNAL_DIR="$ALLUVIUM_DIR/00 Journal"
TODAY=$(date +%Y-%m-%d)
TITLE=$(date +"%B %-d, %Y")
FILE="$JOURNAL_DIR/$TODAY.md"

mkdir -p "$JOURNAL_DIR"
if [ ! -f "$FILE" ]; then
    printf '# %s\n\n' "$TITLE" > "$FILE"
fi

urlencode() { python3 -c 'import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))' "$1"; }

# Open in Obsidian; fall back to the default .md handler if Obsidian isn't installed.
VAULT_NAME="$(basename "$ALLUVIUM_DIR")"
open "obsidian://open?vault=$(urlencode "$VAULT_NAME")&file=$(urlencode "00 Journal/$TODAY")" 2>/dev/null || open "$FILE"
