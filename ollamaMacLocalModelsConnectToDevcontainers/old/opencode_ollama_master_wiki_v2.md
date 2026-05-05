# OpenCode + Ollama — Model Pack, Safe Context Presets, Adaptive Installer (Wiki)

> **Goal:** Make OpenCode model switching reliable and safe by using **context-tuned Ollama variants** and a **role-based model strategy**.

---

## Why we create “ctx variants”

OpenCode frequently accesses Ollama through the **OpenAI-compatible `/v1` endpoint**.citeturn20search194  Setting `num_ctx` dynamically via that compatibility API is not consistently supported; a commonly used, reliable approach is to **bake `num_ctx` into a saved Ollama model tag** (e.g., `…-ctx16384`).citeturn20search189turn20search191turn20search202

---

## 1) Install base models + generate safe ctx variants

### Download and run the adaptive installer

1. Download `install-models-with-ctx.sh` (see Downloads section)
2. Run it:

```bash
bash install-models-with-ctx.sh
```

The script will:
- pull base models
- detect system RAM
- print copy/paste blocks to create `-ctxNNNN` variants

### Create variants (copy/paste blocks)

Ollama supports setting parameters in an interactive run session:citeturn20search202turn20search191

```text
ollama run <model>
>>> /set parameter num_ctx <N>
>>> /save <model-tag>
>>> /bye
```

---

## 2) Paste-ready OpenCode model entries (provider.ollama.models)

> **Note:** These entries assume you create Ollama variants that bake in `num_ctx`, named like `model:tag-ctx16384`.
> This is the most reliable approach when using Ollama via an OpenAI-compatible endpoint.

### Paste into your `provider.ollama.models` section

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


---

## 3) Default / Heavy / Fallback model strategy

Use this role-based strategy so you don’t constantly over-provision memory.

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

### Suggested OpenCode global default

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen2.5-coder:32b-ctx16384"
}
```


---

## 4) Troubleshooting / sanity checks

### Verify the variants exist

```bash
ollama list
```

You should see tags like `qwen2.5-coder:32b-ctx16384`.

### Ensure OpenCode loads the right config

OpenCode loads and merges config from multiple sources (global, project, env overrides).citeturn20search195

If models don’t appear:
- fully exit and restart OpenCode
- ensure you’re in the project root that contains the intended config

---

## Downloads

- `opencode_ollama_master_wiki_v2.md` (this wiki page)
- `install-models-with-ctx.sh` (adaptive installer)

