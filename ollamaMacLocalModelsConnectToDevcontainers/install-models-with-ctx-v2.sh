#!/usr/bin/env bash
set -euo pipefail

# install-models-with-ctx-v2.sh
#
# v2 improvements:
# - Fully automatic: creates -ctxNNNN model variants WITHOUT manual copy/paste
# - RAM-aware: detects total system memory and selects conservative context sizes per tier
# - Idempotent: skips pulls/creates when already present
# - Generates an example OpenCode config if one doesn't exist
#
# Supported: macOS-hosted Ollama server. (Works on Linux too if ollama is installed.)

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
# Context sizing defaults by tier
# ------------------------------
# Conservative, stability-first defaults.
# Override any of these by exporting CTX_* variables before running.

# Large (30B+)
CTX_LARGE_A=${CTX_LARGE_A:-8192}
CTX_LARGE_B=${CTX_LARGE_B:-16384}
CTX_LARGE_C=${CTX_LARGE_C:-32768}
CTX_LARGE_D=${CTX_LARGE_D:-49152}

# Medium (13B–27B)
CTX_MED_A=${CTX_MED_A:-8192}
CTX_MED_B=${CTX_MED_B:-16384}
CTX_MED_C=${CTX_MED_C:-32768}
CTX_MED_D=${CTX_MED_D:-65536}

# Small (≤8B)
CTX_SMALL_A=${CTX_SMALL_A:-16384}
CTX_SMALL_B=${CTX_SMALL_B:-32768}
CTX_SMALL_C=${CTX_SMALL_C:-65536}
CTX_SMALL_D=${CTX_SMALL_D:-131072}

# Vision (kept more conservative than text-only)
CTX_VISION_A=${CTX_VISION_A:-8192}
CTX_VISION_B=${CTX_VISION_B:-8192}
CTX_VISION_C=${CTX_VISION_C:-16384}
CTX_VISION_D=${CTX_VISION_D:-32768}

pick_ctx() {
  local group=$1  # LARGE|MED|SMALL|VISION
  local tier=$2   # A|B|C|D
  local var="CTX_${group}_${tier}"
  echo "${!var}"
}

ctx_large=$(pick_ctx LARGE "$TIER")
ctx_med=$(pick_ctx MED "$TIER")
ctx_small=$(pick_ctx SMALL "$TIER")
ctx_vision=$(pick_ctx VISION "$TIER")

# Per-model overrides (policy): some models are safe/valuable with larger ctx even if "MED" on Tier B.
# You can override these too by exporting the *_OVERRIDE variables.

STAR_CODER_CTX_OVERRIDE=${STAR_CODER_CTX_OVERRIDE:-32768}
QWEN3_14B_CTX_OVERRIDE=${QWEN3_14B_CTX_OVERRIDE:-32768}
LLAMA32_3B_CTX_OVERRIDE=${LLAMA32_3B_CTX_OVERRIDE:-65536}

# Compute a tier-aware override: use the override only when it is larger than the tier default.
max() { python3 - <<'PY' "$1" "$2"
import sys
print(max(int(sys.argv[1]), int(sys.argv[2])))
PY
}

# ------------------------------
# Helpers
# ------------------------------
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

require_cmd ollama
require_cmd python3

# List local models (names only)
list_models() {
  ollama list 2>/dev/null | tail -n +2 | awk '{print $1}'
}

model_exists() {
  local name="$1"
  list_models | grep -Fxq "$name"
}

pull_if_missing() {
  local base="$1"
  if model_exists "$base"; then
    echo "==> Model already present: $base (skip pull)"
  else
    echo "==> Pulling: $base"
    ollama pull "$base"
  fi
}

create_variant_if_missing() {
  local base="$1"
  local ctx="$2"
  local tag="${base}-ctx${ctx}"

  if model_exists "$tag"; then
    echo "==> Variant already present: $tag (skip create)"
    return
  fi

  echo "==> Creating variant: $tag (num_ctx=${ctx})"

  local tmp
  tmp=$(mktemp)
  cat > "$tmp" <<EOF
FROM ${base}
PARAMETER num_ctx ${ctx}
EOF

  # Create the derived model tag
  ollama create "$tag" -f "$tmp"
  rm -f "$tmp"
}

# ------------------------------
# Model plan
# ------------------------------
# NOTE: qwen2.5vl spelling is correct for Ollama library models.

BASE_MODELS=(
  # Coding
  "qwen2.5-coder:32b"
  "qwen3-coder:30b"
  "codellama:34b"
  "starcoder:15b"
  "gemma4:e4b"
  "gemma4:e2b"

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

# ------------------------------
# Pull models
# ------------------------------
echo "==> Ensuring base models are present"
for m in "${BASE_MODELS[@]}"; do
  pull_if_missing "$m"
done

echo

# ------------------------------
# Create ctx variants
# ------------------------------
echo "==> Creating SAFE context variants automatically"

# 30B+ models
create_variant_if_missing "qwen2.5-coder:32b" "$ctx_large"
create_variant_if_missing "qwen3-coder:30b"   "$ctx_large"
create_variant_if_missing "codellama:34b"     "$ctx_large"

# StarCoder (prefer larger ctx)
starcoder_ctx=$(max "$ctx_med" "$STAR_CODER_CTX_OVERRIDE")
create_variant_if_missing "starcoder:15b"     "$starcoder_ctx"

# Reasoning
# DeepSeek can be KV-heavy; keep it a touch conservative by default.
# Tier policy: A=8192, B=16384, C=16384, D=32768 (overridable via DEEPSEEK_CTX_OVERRIDE)
DEEPSEEK_CTX_OVERRIDE=${DEEPSEEK_CTX_OVERRIDE:-0}
if (( DEEPSEEK_CTX_OVERRIDE > 0 )); then
  deepseek_ctx=$DEEPSEEK_CTX_OVERRIDE
else
  case "$TIER" in
    A) deepseek_ctx=8192;;
    B) deepseek_ctx=16384;;
    C) deepseek_ctx=16384;;
    D) deepseek_ctx=32768;;
  esac
fi
create_variant_if_missing "deepseek-r1:32b"   "$deepseek_ctx"
create_variant_if_missing "qwen3:32b"         "$ctx_large"

# Vision / multimodal
create_variant_if_missing "gemma3:27b"        "$ctx_vision"
create_variant_if_missing "qwen2.5vl:32b"     "$ctx_vision"

# General
create_variant_if_missing "qwen3:8b"          "$ctx_small"
qwen3_14b_ctx=$(max "$ctx_med" "$QWEN3_14B_CTX_OVERRIDE")
create_variant_if_missing "qwen3:14b"         "$qwen3_14b_ctx"
create_variant_if_missing "llama3:8b"         "$ctx_small"
llama32_3b_ctx=$(max "$ctx_small" "$LLAMA32_3B_CTX_OVERRIDE")
create_variant_if_missing "llama3.2:3b"       "$llama32_3b_ctx"

echo

# ------------------------------
# Generate an OpenCode config example (if none exists)
# ------------------------------
# We intentionally DO NOT overwrite an existing config.
# We create ./opencode.json.generated in the current directory.

GEN_FILE="opencode.json.generated"
if [[ -f "$GEN_FILE" ]]; then
  echo "==> ${GEN_FILE} already exists (skip generation)"
else
  echo "==> Generating ${GEN_FILE} (example config; safe to copy parts)"

  # Choose defaults that align with the role-based strategy
  DEFAULT_MODEL="ollama/qwen2.5-coder:32b-ctx${ctx_large}"
  FALLBACK_MODEL="ollama/llama3:8b-ctx${ctx_small}"
  VISION_MODEL="ollama/qwen2.5vl:32b-ctx${ctx_vision}"

  cat > "$GEN_FILE" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",

  "model": "${DEFAULT_MODEL}",

  "provider": {
    "ollama": {
      "name": "Ollama (local)",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://localhost:11434/v1",
        "apiKey": "ollama"
      },
      "models": {
        "gemma4:e4b": { "name": "Gemma4 Chat E4B VibeCoder (default)", "tools": true },
        "gemma4:e2b": { "name": "Gemma4 Chat E2B VibeCoder", "tools": true },

        "qwen2.5-coder:32b-ctx16384": { "name": "Qwen 2.5 Coder 32B — ctx 16384", "tools": true },
        "qwen3-coder:30b-ctx${ctx_large}": { "name": "Qwen 3 Coder 30B — ctx ${ctx_large}", "tools": true },
        "codellama:34b-ctx${ctx_large}": { "name": "CodeLlama 34B — ctx ${ctx_large}" },
        "starcoder:15b-ctx${starcoder_ctx}": { "name": "StarCoder 15B — ctx ${starcoder_ctx}" },

        "deepseek-r1:32b-ctx${deepseek_ctx}": { "name": "DeepSeek-R1 32B — ctx ${deepseek_ctx}" },
        "qwen3:32b-ctx${ctx_large}": { "name": "Qwen 3 32B — ctx ${ctx_large}", "tools": true },

        "gemma3:27b-ctx${ctx_vision}": { "name": "Gemma 3 27B — ctx ${ctx_vision}" },
        "qwen2.5vl:32b-ctx${ctx_vision}": { "name": "Qwen 2.5 VL 32B — ctx ${ctx_vision}", "tools": true },

        "qwen3:8b-ctx${ctx_small}": { "name": "Qwen 3 8B — ctx ${ctx_small}" },
        "qwen3:14b-ctx${qwen3_14b_ctx}": { "name": "Qwen 3 14B — ctx ${qwen3_14b_ctx}" },
        "llama3:8b-ctx${ctx_small}": { "name": "Llama 3 8B — ctx ${ctx_small} (fallback)" },
        "llama3.2:3b-ctx${llama32_3b_ctx}": { "name": "Llama 3.2 3B — ctx ${llama32_3b_ctx}" }
      }
    }
  }
}
EOF

  echo "==> Wrote ${GEN_FILE}"
  echo "    Default model:  ${DEFAULT_MODEL}"
  echo "    Fallback model: ${FALLBACK_MODEL}"
  echo "    Vision model:   ${VISION_MODEL}"
fi

echo

echo "==> Done. Useful checks:"
echo "    ollama list"
echo "    ollama ps"
