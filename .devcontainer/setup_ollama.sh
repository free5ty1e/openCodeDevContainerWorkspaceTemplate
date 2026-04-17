#!/usr/bin/env bash
# =============================================================================
# setup_ollama.sh — Pull Ollama models after container creation
# =============================================================================

set +e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/ollama_models.conf"

echo "=========================================="
echo "🤖 Ollama Model Setup"
echo "=========================================="

echo ""
echo "Available models:"
for model in "${!MODELS[@]}"; do
    IFS='|' read -r desc size <<< "${MODELS[$model]}"
    echo "  • $model ($size) - $desc"
done

# Check if running interactively (has a terminal)
is_interactive() {
    [ -t 0 ] && [ -t 1 ]
}

# Collect models to pull
TO_PULL=""

if is_interactive; then
    echo ""
    echo "Select models to install:"
    echo "  [A] All models"
    echo "  [N] None (skip)"
    echo ""
    read -p "Install all models? (A/n): " -n 1 -r
    echo ""

    if [[ $REPLY =~ ^[Nn]$ ]]; then
        echo ""
        echo "Individual model selection:"
        echo ""
        for model in "${!MODELS[@]}"; do
            IFS='|' read -r desc size <<< "${MODELS[$model]}"
            read -p "Install $model ($size) - $desc? (y/N): " -n 1 -r
            echo ""
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                TO_PULL="$TO_PULL $model"
            fi
        done
        
        if [ -z "$TO_PULL" ]; then
            echo "No models selected. Skipping."
            echo "To install later, run: bash .devcontainer/setup_ollama.sh"
            exit 0
        fi
    else
        # Select all models
        for model in "${!MODELS[@]}"; do
            TO_PULL="$TO_PULL $model"
        done
    fi
else
    echo ""
    echo "ERROR: Cannot prompt for input (not a terminal)."
    echo ""
    echo "Usage:"
    echo "  bash .devcontainer/setup_ollama.sh              # Interactive mode"
    echo "  OLLAMA_MODELS='model1 model2' bash setup_ollama.sh  # Non-interactive"
    exit 1
fi

echo ""
echo "📥 Pulling selected models..."

for model in $TO_PULL; do
    if [ -n "$model" ]; then
        echo "   → Pulling $model..."
        ollama pull "$model" || echo "   ⚠️  Failed to pull $model"
    fi
done

echo ""
echo "✅ Ollama models ready!"
echo ""
echo "Available models:"
ollama list

echo ""
echo "To start Ollama server: bash .devcontainer/start_ollama.sh"
echo "To use with opencode:   opencode --model ollama/qwen2.5-coder:7b"
