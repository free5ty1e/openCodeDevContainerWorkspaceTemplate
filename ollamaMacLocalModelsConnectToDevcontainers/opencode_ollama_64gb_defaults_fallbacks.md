# OpenCode + Ollama 64GB: Default / Heavy / Fallback Recommendations

This guide gives you a **simple, repeatable** model strategy for OpenCode on a 64GB unified-memory system.

## Recommended model roles

### Default (daily coding)
- **Model:** `ollama/qwen2.5-coder:32b-ctx16384`
- **Use for:** most Plan/Build loops; refactors; tests

### Fast fallback (low latency)
- **Model:** `ollama/llama3:8b-ctx32768`
- **Use for:** quick edits, explanations, fast iteration

### Heavy reasoning / planning
- **Model:** `ollama/deepseek-r1:32b-ctx16384`
- **Use for:** hard debugging, multi-step reasoning, architecture planning

### Vision / structured extraction
- **Model:** `ollama/qwen2.5vl:32b-ctx8192`
- **Use for:** screenshots, OCR, diagram parsing, structured JSON output

## Suggested OpenCode global defaults

In your main OpenCode config, set:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen2.5-coder:32b-ctx16384"
}
```

Then keep the other models available for switching when needed.

## Safety rules (64GB UMA)
- Avoid running **two ≥30B models simultaneously**.
- Start with the **smaller ctx variant** (e.g. 16k) before trying 32k.
- If you see memory pressure / swapping, reduce ctx first.
