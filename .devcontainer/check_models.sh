#!/usr/bin/env bash
# =============================================================================
# check_models.sh — Verify Ollama models are consistent across configs
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."  # Go to workspace root

echo "=========================================="
echo "🔍 Ollama Model Config Validator"
echo "=========================================="
echo ""

# Source the central model list
source "${SCRIPT_DIR}/ollama_models.conf"

echo "Models in ollama_models.conf:"
CONFIGS_MODELS=()
for model in "${!MODELS[@]}"; do
    IFS='|' read -r desc size <<< "${MODELS[$model]}"
    echo "  ✓ $model ($size) - $desc"
done

echo ""
echo "--- Checking configs ---"
ERRORS=0

# Check tasks.json
echo ""
echo "tasks.json:"
TASKS_MODELS=$(grep -oP '"Opencode \+ Ollama: \K[^"]+' .vscode/tasks.json | sed 's/ (.*//' || true)
for model in "${!MODELS[@]}"; do
    if echo "$TASKS_MODELS" | grep -q "^${model}$"; then
        echo "  ✓ $model"
    else
        echo "  ✗ MISSING in tasks.json: $model"
        ERRORS=$((ERRORS+1))
    fi
done

# Check opencode.json
echo ""
echo "opencode.json:"
for model in "${!MODELS[@]}"; do
    if grep -q "\"$model\"" opencode.json; then
        echo "  ✓ $model"
    else
        echo "  ✗ MISSING in opencode.json: $model"
        ERRORS=$((ERRORS+1))
    fi
done

echo ""
echo "=========================================="
if [ $ERRORS -eq 0 ]; then
    echo "✅ All models in sync!"
    exit 0
else
    echo "❌ $ERRORS issue(s) found"
    exit 1
fi
