# Ollama Models Safe for Devcontainers (64GB Host)

These models are safe to use from **Docker / devcontainers** while Ollama runs on the host.

| Model | Why Safe |
|------|----------|
| llama3:8b | Fast, low latency |
| qwen2.5-coder:32b | Primary coding agent |
| qwen3-coder:30b | Tool-calling agents |
| nomic-embed-text | RAG only |

Avoid running **70B models** from multiple containers concurrently.
