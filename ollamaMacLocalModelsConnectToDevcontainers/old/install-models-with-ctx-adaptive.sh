#!/usr/bin/env bash
set -euo pipefail

# install-models-with-ctx.sh
# Purpose: Pull a set of Ollama models and generate SAFE context-window variants for OpenCode.
#
# Why variants?
# OpenCode commonly talks to Ollama via the OpenAI-compatible endpoint (/v1). Setting num_ctx per request
# is not consistently supported in that compatibility layer, so the reliable workflow is to bake num_ctx
# into a saved model/tag:
#   ollama run <model>
#   >>> /set parameter num_ctx <N>
#   >>> /save <model-tag>
#   >>> /bye
#
# This script:
#   1) Pulls base models
#   2) Detects total system memory
#   3) Chooses a memory tier
#   4) Prints copy/paste blocks to create context-tuned variants

# ------------------------------
# Memory detection
# ------------------------------
get_total_mem_gb() {
  # macOS
  if command -v sysctl >/dev/null 2>&1; then
    local bytes
    bytes=$(sysctl -n hw.memsize 2>/dev/null || true)
    if [[ -n "${bytes}" ]]; then
      python3 - <<'PY' "$bytes"
import sys
b=int(sys.argv[1])
print(int(round(b/1024/1024/1024)))
PY
      return
    fi
  fi

  # Linux
  if [[ -r /proc/meminfo ]]; then
    local kb
    kb=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
    python3 - <<'PY' "$kb"
import sys
kb=int(sys.argv[1])
print(int(round(kb/1024/1024)))
PY
    return
  fi

  echo 0
}

TOTAL_GB=$(get_total_mem_gb)

# ------------------------------
# Tiering (safe defaults)
# ------------------------------
# Tier A: <48GB
# Tier B: 48-80GB (typical 64GB machines)
# Tier C: 80-128GB
# Tier D: >=128GB

tier_for_mem() {
  local gb=$1
  if (( gb < 48 )); then
    echo A
  elif (( gb < 80 )); then
    echo B
  elif (( gb < 128 )); then
    echo C
  else
    echo D
  fi
}

TIER=$(tier_for_mem "$TOTAL_GB")

echo "Detected total memory: ${TOTAL_GB} GB"
echo "Selected tier: ${TIER}"
echo

# ------------------------------
# Context sizing by model group
# ------------------------------
# Conservative, stability-first defaults.
# Override any value by exporting CTX_* vars before running.

# Large coding/reasoning models (30B+)
CTX_LARGE_A=${CTX_LARGE_A:-8192}
CTX_LARGE_B=${CTX_LARGE_B:-16384}
CTX_LARGE_C=${CTX_LARGE_C:-32768}
CTX_LARGE_D=${CTX_LARGE_D:-49152}

# Medium models (13B-27B)
CTX_MED_A=${CTX_MED_A:-8192}
CTX_MED_B=${CTX_MED_B:-16384}
CTX_MED_C=${CTX_MED_C:-32768}
CTX_MED_D=${CTX_MED_D:-65536}

# Small models (<=8B)
CTX_SMALL_A=${CTX_SMALL_A:-16384}
CTX_SMALL_B=${CTX_SMALL_B:-32768}
CTX_SMALL_C=${CTX_SMALL_C:-65536}
CTX_SMALL_D=${CTX_SMALL_D:-131072}

# Vision models (more conservative)
CTX_VISION_A=${CTX_VISION_A:-8192}
CTX_VISION_B=${CTX_VISION_B:-8192}
CTX_VISION_C=${CTX_VISION_C:-16384}
CTX_VISION_D=${CTX_VISION_D:-32768}

pick_ctx() {
  local group=$1
  local tier=$2
  local var="CTX_${group}_${tier}"
  echo "${!var}"
}

# ------------------------------
# Model list
# ------------------------------
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
echo "Copy/paste the following blocks into your terminal. Each block creates a -ctxNNNN variant."
echo

print_block() {
  local base=$1
  local ctx=$2
  local tag="${base}-ctx${ctx}"
  cat <<EOF
ollama run ${base}
>>> /set parameter num_ctx ${ctx}
>>> /save ${tag}
>>> /bye

EOF
}

ctx_large=$(pick_ctx LARGE "$TIER")
ctx_med=$(pick_ctx MED "$TIER")
ctx_small=$(pick_ctx SMALL "$TIER")
ctx_vision=$(pick_ctx VISION "$TIER")

# Coding (30B+)
print_block "qwen2.5-coder:32b" "$ctx_large"
print_block "qwen3-coder:30b" "$ctx_large"
print_block "codellama:34b" "$ctx_large"

# StarCoder (15B)
print_block "starcoder:15b" "$ctx_med"

# Reasoning (32B)
# DeepSeek R1 often benefits from being slightly more conservative than other 32B models.
ctx_deepseek=$ctx_large
if [[ "$TIER" == "A" ]]; then ctx_deepseek=8192; fi
if [[ "$TIER" == "B" ]]; then ctx_deepseek=16384; fi
if [[ "$TIER" == "C" ]]; then ctx_deepseek=16384; fi
if [[ "$TIER" == "D" ]]; then ctx_deepseek=32768; fi
print_block "deepseek-r1:32b" "$ctx_deepseek"
print_block "qwen3:32b" "$ctx_large"

# Vision / multimodal
print_block "gemma3:27b" "$ctx_vision"
print_block "qwen2.5vl:32b" "$ctx_vision"

# General
print_block "qwen3:8b" "$ctx_small"
print_block "qwen3:14b" "$ctx_med"
print_block "llama3:8b" "$ctx_small"
print_block "llama3.2:3b" "$ctx_small"

echo "==> Done. Verify variants with: ollama list"
