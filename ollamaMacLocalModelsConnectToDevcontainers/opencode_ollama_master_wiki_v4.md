# OpenCode + Ollama — Model Pack, Safe Context Presets, Full Config Example, Adaptive Installer (Wiki v4)

> **This v4 adds:** (1) a TL;DR Quick Start, (2) a FAQ page, and (3) a Devcontainer note for using host Ollama from containers.

---

## TL;DR — Quick Start (Copy/Paste)

### 1) Pull models + generate safe context variants

```bash
bash install-models-with-ctx.sh
```

This script detects your total RAM (macOS/Linux), selects a memory tier, and prints the copy/paste blocks to create `-ctxNNNN` model variants.

### 2) Put this in `~/.config/opencode/opencode.json` (or project `opencode.json`)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen2.5-coder:32b-ctx16384",
  "provider": {
    "ollama": {
      "name": "Ollama (local)",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://localhost:11434/v1",
        "apiKey": "ollama"
      },
      "models": {
        "qwen2.5-coder:32b-ctx16384": { "tools": true },
        "llama3:8b-ctx32768": {},
        "deepseek-r1:32b-ctx16384": {},
        "qwen2.5vl:32b-ctx8192": { "tools": true }
      }
    }
  }
}
```

### 3) Restart OpenCode (it doesn’t hot‑reload config)

OpenCode merges configs from multiple sources and reads them at startup; project config can override global config, and later sources override earlier ones only for conflicting keys. citeturn23search219

### 4) Performance Tips

Mac users benefit a lot from keeping models resident:

```bash
launchctl setenv OLLAMA_KEEP_ALIVE -1
```

Isolate specific model slowdowns to ollama (eliminate opencode wrapper) by testing thusly:

```bash
ollama run qwen2.5-coder:32b-ctx16384 "are you there?"
```

Instead of OpenCode, which does introduce a bottleneck

---

## Why we create “ctx variants”

OpenCode commonly accesses Ollama through the **OpenAI‑compatible `/v1` endpoint**. The reliable way to ensure a stable, large context window is to bake `num_ctx` into a saved Ollama model tag (e.g., `…-ctx16384`) via:

```text
ollama run <model>
>>> /set parameter num_ctx <N>
>>> /save <model-tag>
>>> /bye
```

This makes context sizing deterministic for OpenCode across sessions.

---

## 1) Install base models + generate safe ctx variants

### Run the adaptive installer

```bash
bash install-models-with-ctx.sh
```

### Verify

```bash
ollama list
```

Look for `-ctxNNNN` tags.

---

## 2) Paste‑ready OpenCode model entries (`provider.ollama.models`)

```jsonc
{
  "qwen2.5-coder:32b-ctx16384": { "name": "Qwen 2.5 Coder 32B — Coding — ctx 16k (safe default)", "tools": true },
  "qwen3-coder:30b-ctx16384":   { "name": "Qwen 3 Coder 30B — Agentic coding — ctx 16k", "tools": true },
  "codellama:34b-ctx16384":     { "name": "CodeLlama 34B — Multi-language coding — ctx 16k" },
  "starcoder:15b-ctx32768":     { "name": "StarCoder 15B — Code completion — ctx 32k" },

  "deepseek-r1:32b-ctx16384":   { "name": "DeepSeek-R1 32B — Heavy reasoning — ctx 16k" },
  "qwen3:32b-ctx16384":         { "name": "Qwen 3 32B — Reasoning + tools — ctx 16k", "tools": true },

  "qwen2.5vl:32b-ctx8192":      { "name": "Qwen 2.5 VL 32B — Vision / OCR — ctx 8k", "tools": true },
  "gemma3:27b-ctx8192":         { "name": "Gemma 3 27B — Multimodal — ctx 8k" },

  "llama3:8b-ctx32768":         { "name": "Llama 3 8B — Fast fallback — ctx 32k" },
  "qwen3:8b-ctx65536":          { "name": "Qwen 3 8B — Long-context utility — ctx 64k" }
}
```

---

## 3) Role‑based model strategy (recommended)

- **Default (daily coding):** `ollama/qwen2.5-coder:32b-ctx16384`
- **Fast fallback:** `ollama/llama3:8b-ctx32768`
- **Heavy reasoning / planning:** `ollama/deepseek-r1:32b-ctx16384`
- **Vision / structured extraction:** `ollama/qwen2.5vl:32b-ctx8192`

---

## 4) Full `opencode.json` example (copy‑paste)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "ollama/qwen2.5-coder:32b-ctx16384",
  "provider": {
    "ollama": {
      "name": "Ollama (local)",
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://localhost:11434/v1",
        "apiKey": "ollama"
      },
      "models": {
        "qwen2.5-coder:32b-ctx16384": { "tools": true },
        "qwen3-coder:30b-ctx16384": { "tools": true },
        "codellama:34b-ctx16384": {},
        "starcoder:15b-ctx32768": {},
        "deepseek-r1:32b-ctx16384": {},
        "qwen3:32b-ctx16384": { "tools": true },
        "qwen2.5vl:32b-ctx8192": { "tools": true },
        "gemma3:27b-ctx8192": {},
        "llama3:8b-ctx32768": {},
        "qwen3:8b-ctx65536": {}
      }
    }
  }
}
```

---

## 5) Adaptive installer — memory tiers & overrides

### Memory tiers

| Tier | Detected RAM | LARGE (30B+) | MED (13–27B) | SMALL (≤8B) | VISION |
|-----:|-------------:|-------------:|-------------:|------------:|-------:|
| A | < 48 GB | 8k | 8k | 16k | 8k |
| B | 48–80 GB | 16k | 16k | 32k | 8k |
| C | 80–128 GB | 32k | 32k | 64k | 16k |
| D | ≥128 GB | 48k | 64k | 128k | 32k |

### Override without editing the script

You can override defaults via env vars:

```bash
CTX_LARGE_B=32768 \
CTX_SMALL_B=65536 \
bash install-models-with-ctx.sh
```

Supported overrides:
- `CTX_LARGE_[A–D]`
- `CTX_MED_[A–D]`
- `CTX_SMALL_[A–D]`
- `CTX_VISION_[A–D]`

---

## 6) Devcontainer note — use host Ollama from inside containers

### The problem

Inside a container, `localhost` refers to the container, not your host. Docker Desktop provides a special DNS name `host.docker.internal` to reach services on the host. citeturn23search207

### Recommended approach

Install opencode in your devcontainer: 

```bash
curl -fsSL https://opencode.ai/install | bash

opencode --version

#Open the opencode config for editing:
nano .config/opencode/opencode.json
```


In your devcontainer OpenCode config (inside the container), point the Ollama provider to:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://host.docker.internal:11434/v1" },
      "models": {
        "qwen2.5-coder:32b-ctx16384": { "name": "Qwen 2.5 Coder 32B — ctx 16384 (default)", "tools": true },
        "qwen3-coder:30b-ctx16384": { "name": "Qwen 3 Coder 30B — ctx 16384", "tools": true },
        "codellama:34b-ctx16384": { "name": "CodeLlama 34B — ctx 16384" },
        "starcoder:15b-ctx32768": { "name": "StarCoder 15B — ctx 32768" },

        "deepseek-r1:32b-ctx16384": { "name": "DeepSeek-R1 32B — ctx 16384" },
        "qwen3:32b-ctx16384": { "name": "Qwen 3 32B — ctx 16384", "tools": true },

        "gemma3:27b-ctx8192": { "name": "Gemma 3 27B — ctx 8192" },
        "qwen2.5vl:32b-ctx8192": { "name": "Qwen 2.5 VL 32B — ctx 8192", "tools": true },

        "qwen3:8b-ctx32768": { "name": "Qwen 3 8B — ctx 32768" },
        "qwen3:14b-ctx32768": { "name": "Qwen 3 14B — ctx 32768" },
        "llama3:8b-ctx32768": { "name": "Llama 3 8B — ctx 32768 (fallback)" },
        "llama3.2:3b-ctx65536": { "name": "Llama 3.2 3B — ctx 65536" }
        // Add models you've pulled via `ollama pull <model>`
      }
    }
  }
}
```

Docker Desktop documents `host.docker.internal` for connecting from a container to a service on the host. citeturn23search207

### Sanity test inside the container

```bash
curl http://host.docker.internal:11434/api/tags
```

If it returns model JSON, your container can reach host Ollama.

---

## 7) Troubleshooting (quick)

- **Models not in OpenCode:** restart OpenCode; it reads config at startup and merges config sources by precedence. citeturn23search219
- **Pulled model but can’t select:** ensure the exact model id (including `-ctxNNNN`) exists in `ollama list`.
- **Devcontainer can’t connect:** ensure Docker Desktop is in use and `host.docker.internal` resolves. citeturn23search207

---

## 8) FAQ

See: `opencode_ollama_faq_v1.md` (generated alongside this wiki v4).

---

## Downloads

- `opencode_ollama_master_wiki_v4.md` (this page)
- `opencode_ollama_faq_v1.md` (FAQ)
- `opencode_ollama_devcontainer_note.md` (standalone devcontainer note)
- `install-models-with-ctx.sh` (adaptive installer)
