#!/bin/bash
# Wrapper script to set necessary environment variables and resource limits
# before starting the Ollama server daemon.

# Set file descriptor limit for the service
ulimit -n 65536

# Export environment variable for Ollama to ensure it binds to the LAN IP
export OLLAMA_HOST=0.0.0.0:11434

# Ollama keep alive indefinitely to avoid models randomly shutting down in the middle of work
export OLLAMA_KEEP_ALIVE=-1

# ── Locate the ollama binary ──────────────────────────────────────────────
# Search order: Homebrew paths, official install path, then PATH
OLLAMA_BIN=""
for candidate in \
    /opt/homebrew/bin/ollama \
    /usr/local/bin/ollama \
    /usr/bin/ollama; do
    if [ -x "${candidate}" ]; then
        OLLAMA_BIN="${candidate}"
        break
    fi
done

# Fall back to PATH if no known location matched
if [ -z "${OLLAMA_BIN}" ]; then
    OLLAMA_BIN="$(command -v ollama 2>/dev/null)"
fi

if [ -z "${OLLAMA_BIN}" ]; then
    printf '[ollama_wrapper] ERROR: ollama binary not found.\n' >&2
    printf '[ollama_wrapper] Tried: /opt/homebrew/bin/ollama /usr/local/bin/ollama /usr/bin/ollama $PATH\n' >&2
    exit 1
fi

printf '[ollama_wrapper] Using ollama: %s\n' "${OLLAMA_BIN}"

# ── Kill any existing ollama server process ───────────────────────────────
# Prevents EADDRINUSE / stale daemon conflicts before starting fresh.
EXISTING_PIDS="$(pgrep -x ollama 2>/dev/null)" || true
if [ -n "${EXISTING_PIDS}" ]; then
    printf '[ollama_wrapper] Stopping existing ollama process(es): %s\n' "${EXISTING_PIDS}"
    # Send SIGTERM first, give them a moment to exit cleanly
    kill ${EXISTING_PIDS} 2>/dev/null || true
    sleep 1
    # Force-kill any that are still alive
    REMAINING="$(pgrep -x ollama 2>/dev/null)" || true
    if [ -n "${REMAINING}" ]; then
        printf '[ollama_wrapper] Force-killing stalled ollama process(es): %s\n' "${REMAINING}"
        kill -9 ${REMAINING} 2>/dev/null || true
        sleep 1
    fi
fi

# ── Start the server ──────────────────────────────────────────────────────
exec "${OLLAMA_BIN}" serve
