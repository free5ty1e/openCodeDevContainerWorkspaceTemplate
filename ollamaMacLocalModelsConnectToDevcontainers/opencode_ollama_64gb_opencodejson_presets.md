# OpenCode Ollama 64GB: Per-Model Safe Context Presets (opencode.json snippet)

This snippet assumes you will create **context-tuned Ollama variants** (via `ollama run ...` then `/set parameter num_ctx ...` then `/save ...`).
That workflow is documented in community guides for Ollama + OpenCode. 

> Why variants? Ollama's OpenAI-compatible API does not reliably allow changing `num_ctx` per-request, so the reliable approach is saving a new tagged model with `num_ctx` baked in.

## Recommended defaults
- Default model: `ollama/qwen2.5-coder:32b-ctx16384`
- Fallback model: `ollama/llama3:8b-ctx32768`
- Heavy/plan model: `ollama/deepseek-r1:32b-ctx16384`

## Paste into your `provider.ollama.models` section

```jsonc
{
  // --- Coding models (safe ctx presets) ---
  "qwen2.5-coder:32b-ctx16384": {
    "name": "Qwen 2.5 Coder 32B — Coding (≈26GB) — ctx 16k (safe default)"
  },
  "qwen2.5-coder:32b-ctx32768": {
    "name": "Qwen 2.5 Coder 32B — Coding (≈26GB) — ctx 32k (heavier)"
  },
  "qwen3-coder:30b-ctx16384": {
    "name": "Qwen 3 Coder 30B — Agentic coding/tools (≈22GB) — ctx 16k"
  },
  "qwen3-coder:30b-ctx32768": {
    "name": "Qwen 3 Coder 30B — Agentic coding/tools (≈22GB) — ctx 32k"
  },
  "codellama:34b-ctx16384": {
    "name": "CodeLlama 34B — Multi-language coding (≈28GB) — ctx 16k"
  },
  "starcoder:15b-ctx32768": {
    "name": "StarCoder 15B — Code completion (≈10GB) — ctx 32k"
  },

  // --- Math / reasoning ---
  "deepseek-r1:32b-ctx8192": {
    "name": "DeepSeek-R1 32B — Reasoning (≈24GB) — ctx 8k (safe)"
  },
  "deepseek-r1:32b-ctx16384": {
    "name": "DeepSeek-R1 32B — Reasoning (≈24GB) — ctx 16k (heavier)"
  },
  "qwen3:32b-ctx16384": {
    "name": "Qwen 3 32B — Reasoning/tools (≈26GB) — ctx 16k"
  },
  "qwen3:32b-ctx32768": {
    "name": "Qwen 3 32B — Reasoning/tools (≈26GB) — ctx 32k"
  },

  // --- Vision / multimodal ---
  "gemma3:27b-ctx8192": {
    "name": "Gemma 3 27B — Multimodal (≈20GB) — ctx 8k (safe)"
  },
  "gemma3:27b-ctx16384": {
    "name": "Gemma 3 27B — Multimodal (≈20GB) — ctx 16k"
  },
  "qwen2.5vl:32b-ctx8192": {
    "name": "Qwen 2.5 VL 32B — Vision/OCR/structured JSON — ctx 8k (safe)"
  },
  "qwen2.5vl:32b-ctx16384": {
    "name": "Qwen 2.5 VL 32B — Vision/OCR/structured JSON — ctx 16k"
  },

  // --- General / lightweight ---
  "qwen3:8b-ctx32768": {
    "name": "Qwen 3 8B — General (≈6GB) — ctx 32k"
  },
  "qwen3:8b-ctx65536": {
    "name": "Qwen 3 8B — General (≈6GB) — ctx 64k"
  },
  "qwen3:14b-ctx32768": {
    "name": "Qwen 3 14B — General (≈10GB) — ctx 32k"
  },
  "llama3:8b-ctx32768": {
    "name": "Llama 3 8B — Daily driver (≈6GB) — ctx 32k"
  },
  "llama3:8b-ctx65536": {
    "name": "Llama 3 8B — Daily driver (≈6GB) — ctx 64k"
  },
  "llama3.2:3b-ctx65536": {
    "name": "Llama 3.2 3B — Utility (≈3GB) — ctx 64k"
  }
}
```
