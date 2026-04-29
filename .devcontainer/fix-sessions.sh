#!/usr/bin/env bash
# =============================================================================
# fix-sessions.sh — Manually migrate opencode sessions after container rebuild
# Run this when you notice your sessions are missing after a rebuild
# =============================================================================

DB_PATH="/home/vscode/.local/share/opencode/opencode.db"

if [ ! -f "$DB_PATH" ]; then
    echo "No opencode database found at $DB_PATH"
    exit 1
fi

echo "Migrating sessions to current project ID..."
python3 "$(dirname "$0")/migrate_sessions.py"
echo "Done! Listing sessions..."
opencode session list --format table 2>&1 || echo "Run 'opencode session list' to verify your sessions."
