#!/usr/bin/env bash
# =============================================================================
# start_ollama.sh — Idempotently start Ollama server
# =============================================================================

set -e

echo "Checking Ollama server status..."

# Check if Ollama is already running by testing the API
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "✅ Ollama server is already running"
    ollama list
else
    echo "🚀 Starting Ollama server..."
    nohup ollama serve > /tmp/ollama.log 2>&1 &
    
    # Wait for server to be ready (up to 30 seconds)
    echo "   Waiting for server to start..."
    for i in {1..30}; do
        if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
            echo "✅ Ollama server started successfully"
            echo ""
            echo "Available models:"
            ollama list
            exit 0
        fi
        sleep 1
    done
    
    echo "⚠️  Ollama server may not have started properly. Check /tmp/ollama.log"
fi
