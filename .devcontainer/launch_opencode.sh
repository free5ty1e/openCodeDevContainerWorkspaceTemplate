#!/usr/bin/env bash
# =============================================================================
# launch_opencode.sh — Launch opencode with the most recent session
# =============================================================================

# Optionally start Ollama server (idempotent - safe to run every time)
if command -v ollama &> /dev/null; then
    bash "$(dirname "$0")/start_ollama.sh"
fi

# Find the most recent session
SESSION_ID=$(opencode session list 2>/dev/null | tail -n +3 | head -n 1 | awk '{print $1}' || true)

if [ -z "$SESSION_ID" ]; then
    echo "No sessions found. Starting fresh..."
    exec opencode /workspace
else
    echo "Resuming session: $SESSION_ID"
    exec opencode /workspace -c -s "$SESSION_ID"
fi
