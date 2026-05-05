#!/usr/bin/env bash
set -euo pipefail

# install-models.sh
# Purpose: Pull a set of Ollama models and create SAFE context-window variants for OpenCode.
#
# Why variants? Ollama's OpenAI-compatible endpoint does not reliably support changing num_ctx per request.
# The reliable workflow is:
#   ollama run <model>
#   >>> /set parameter num_ctx <N>
#   >>> /save <model-tag>
#   >>> /bye
# This workflow is documented in Ollama+OpenCode guides.

MODELS=(
  # Coding
  "qwen2.5-coder:32b"
  "qwen3-coder:30b"
  "codellama:34b"
  "starcoder:15b"

  # Reasoning
  "deepseek-r1:32b"
  "qwen3:32b"

  # Multimodal
  "gemma3:27b"
  "qwen2.5vl:32b"

  # General
  "qwen3:8b"
  "qwen3:14b"
  "llama3:8b"
  "llama3.2:3b"
)

echo "==> Pulling base models"
for m in "${MODELS[@]}"; do
  echo "--- ollama pull ${m}"
  ollama pull "${m}"
done

echo
echo "==> Creating SAFE context variants"
echo "NOTE: Ollama creates context variants interactively. For each model below, copy/paste the block into the ollama prompt."
echo

cat <<'EOF'

# ─────────────────────────────
# qwen2.5-coder:32b (safe ctx: 16384, optional 32768)
ollama run qwen2.5-coder:32b
>>> /set parameter num_ctx 16384
>>> /save qwen2.5-coder:32b-ctx16384
>>> /set parameter num_ctx 32768
>>> /save qwen2.5-coder:32b-ctx32768
>>> /bye

# qwen3-coder:30b (safe ctx: 16384, optional 32768)
ollama run qwen3-coder:30b
>>> /set parameter num_ctx 16384
>>> /save qwen3-coder:30b-ctx16384
>>> /set parameter num_ctx 32768
>>> /save qwen3-coder:30b-ctx32768
>>> /bye

# codellama:34b (safe ctx: 16384)
ollama run codellama:34b
>>> /set parameter num_ctx 16384
>>> /save codellama:34b-ctx16384
>>> /bye

# starcoder:15b (safe ctx: 32768)
ollama run starcoder:15b
>>> /set parameter num_ctx 32768
>>> /save starcoder:15b-ctx32768
>>> /bye

# deepseek-r1:32b (safe ctx: 8192, optional 16384)
ollama run deepseek-r1:32b
>>> /set parameter num_ctx 8192
>>> /save deepseek-r1:32b-ctx8192
>>> /set parameter num_ctx 16384
>>> /save deepseek-r1:32b-ctx16384
>>> /bye

# qwen3:32b (safe ctx: 16384, optional 32768)
ollama run qwen3:32b
>>> /set parameter num_ctx 16384
>>> /save qwen3:32b-ctx16384
>>> /set parameter num_ctx 32768
>>> /save qwen3:32b-ctx32768
>>> /bye

# gemma3:27b (safe ctx: 8192, optional 16384)
ollama run gemma3:27b
>>> /set parameter num_ctx 8192
>>> /save gemma3:27b-ctx8192
>>> /set parameter num_ctx 16384
>>> /save gemma3:27b-ctx16384
>>> /bye

# qwen2.5vl:32b (safe ctx: 8192, optional 16384)
ollama run qwen2.5vl:32b
>>> /set parameter num_ctx 8192
>>> /save qwen2.5vl:32b-ctx8192
>>> /set parameter num_ctx 16384
>>> /save qwen2.5vl:32b-ctx16384
>>> /bye

# qwen3:8b (safe ctx: 32768, optional 65536)
ollama run qwen3:8b
>>> /set parameter num_ctx 32768
>>> /save qwen3:8b-ctx32768
>>> /set parameter num_ctx 65536
>>> /save qwen3:8b-ctx65536
>>> /bye

# qwen3:14b (safe ctx: 32768)
ollama run qwen3:14b
>>> /set parameter num_ctx 32768
>>> /save qwen3:14b-ctx32768
>>> /bye

# llama3:8b (safe ctx: 32768, optional 65536)
ollama run llama3:8b
>>> /set parameter num_ctx 32768
>>> /save llama3:8b-ctx32768
>>> /set parameter num_ctx 65536
>>> /save llama3:8b-ctx65536
>>> /bye

# llama3.2:3b (safe ctx: 65536)
ollama run llama3.2:3b
>>> /set parameter num_ctx 65536
>>> /save llama3.2:3b-ctx65536
>>> /bye

EOF

echo
echo "==> Done. Verify variants with: ollama list"
