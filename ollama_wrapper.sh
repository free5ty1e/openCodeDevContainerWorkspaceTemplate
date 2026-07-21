#!/bin/bash
# ollama_wrapper.sh
# 
# Runs ollama serve with settings that are more suitable for long-running reasoning sessions.
# 
# Usage: ./ollama_wrapper.sh
# Runs in the foreground, exposes logs to console.  Open a second terminal for ollama commands.
# It will kill any running Ollama serve instances, be sure to also manually close your Ollama app before running to avoid conflicts
# 

# Raise file-descriptor limit (same as v1)
ulimit -n 65536

# Bind to all interfaces so devcontainers and LAN machines can reach the server
export OLLAMA_HOST=0.0.0.0:11434

# Keep models loaded indefinitely — no idle unload
export OLLAMA_KEEP_ALIVE=-1

# *** Core fix: 30-minute timeout instead of the default 5 minutes ***
export OLLAMA_LOAD_TIMEOUT=30m

# Expand and Force the Context Length
# Agentic workflows fill up standard 2K or 4K context windows within two or three turns, causing the model to freeze or output gibberish.
# Careful about this length, longer conversations exponentially take more and more memory and CPU, and can cause the model to crash or freeze.
# At 22K, the model can handle 10-15 turns of conversation before it starts to struggle.  At 30K, it can handle 20-30 turns, but will use a lot more memory and CPU.
export OLLAMA_CONTEXT_LENGTH=22000

# Kill any existing Ollama processes (same as v1)
killall -9 ollama 2>/dev/null || true

# Run Ollama in the foreground (same as v1)
exec /usr/local/bin/ollama serve
