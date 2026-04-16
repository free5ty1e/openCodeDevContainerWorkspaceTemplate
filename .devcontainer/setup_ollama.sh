#!/usr/bin/env bash
# =============================================================================
# setup_ollama.sh — Pull Ollama models after container creation
# =============================================================================

set -e

echo "=========================================="
echo "🤖 Ollama Model Setup"
echo "=========================================="

# Available models:
#   - qwen2.5-coder:7b  (~4.4GB) - Optimized for code generation
#   - codellama:13b      (~7.4GB) - Code Llama with larger context
#   - llama3.2:3b       (~2.0GB) - Fast, general-purpose for non-code tasks
OLLAMA_MODELS=${OLLAMA_MODELS:-"qwen2.5-coder:7b codellama:13b llama3.2:3b"}

echo ""
echo "Models to pull: $OLLAMA_MODELS"
echo ""
read -p "Pull Ollama models locally? This downloads ~14GB. (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Skipping model download. To install later, run:"
    echo "   bash .devcontainer/setup_ollama.sh"
    exit 0
fi

echo ""
echo "📥 Pulling Ollama models: $OLLAMA_MODELS"

for model in $OLLAMA_MODELS; do
    echo "   → Pulling $model..."
    ollama pull "$model" || echo "   ⚠️  Failed to pull $model (may already exist or network issue)"
done

echo ""
echo "✅ Ollama models ready!"
echo ""
echo "Available models:"
ollama list

echo ""
echo "To start Ollama server: ollama serve"
echo "To use with opencode:   opencode --model ollama/qwen2.5-coder:7b"
