# OpenCode + Ollama — Master Wiki (v3)

> **Goal:** Make OpenCode model switching reliable and safe by using **context-tuned Ollama variants**, a **role-based model strategy**, and an **adaptive installer** that selects safe context windows based on detected RAM.

This page consolidates everything into a single wiki-ready document:

1. **How and why** we create `-ctxNNNN` model variants in Ollama
2. **Adaptive installer** (`install-models-with-ctx.sh`) with RAM detection + tiering
3. **Paste-ready model entries** for `provider.ollama.models`
4. **Recommended default / fallback / heavy / vision** strategy
5. **Full recommended `opencode.json` example** (copy/paste)
6. **How to adjust / override context sizing** (no script edits required)

---

## Why we create “ctx variants”

OpenCode commonly accesses Ollama through the **OpenAI-compatible `/v1` endpoint**.citeturn20search194  Setting `num_ctx` dynamically per request is not consistently supported in that compatibility layer; a widely used, reliable approach is to **bake `num_ctx` into a saved Ollama model tag** such as `qwen2.5-coder:32b-ctx16384`.citeturn20search189turn20search191turn20search202

**Practical upshot:** Create variants once, then OpenCode can switch models safely and predictably.

---

## 1) Adaptive installer (RAM-aware) — what it does

The installer:
- Detects total system memory (macOS via `sysctl`, Linux via `/proc/meminfo`)
- Assigns a **memory tier**
- Selects safe `num_ctx` targets per tier
- Pulls base models
- Prints copy/paste blocks that create `-ctxNNNN` variants

### Memory tiers

These are conservative defaults intended to avoid swapping and keep OpenCode stable:

- **Tier A:** < 48 GB RAM → smaller contexts
- **Tier B:** 48–80 GB RAM → typical 64GB-safe contexts
- **Tier C:** 80–128 GB RAM → larger contexts
- **Tier D:** ≥ 128 GB RAM → largest contexts (still conservative)

---

## 2) How to run the installer

1) Download `install-models-with-ctx.sh` (see Downloads)

2) Run:

```bash
bash install-models-with-ctx.sh
```

3) The script will print blocks like:

```text
ollama run qwen2.5-coder:32b
>>> /set parameter num_ctx 16384
>>> /save qwen2.5-coder:32b-ctx16384
>>> /bye
```

Copy/paste each block into your terminal to generate variants.

---

## 3) Paste-ready OpenCode model entries (provider.ollama.models)

> These entries assume you created the `-ctxNNNN` variants using the installer output.

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

## 4) Recommended model roles (default / fallback / heavy / vision)

Use this strategy to keep things fast and avoid memory thrash:

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

---

## 5) Full recommended `opencode.json` example (copy/paste)

> **Where this file goes:** `~/.config/opencode/opencode.json` (global) or your project root as `opencode.json`. OpenCode merges configs across locations.citeturn20search195

> **If using LAN-shared Ollama:** replace `localhost` with your host IP (e.g. `http://192.168.1.42:11434/v1`).

```jsonc
{
  "$schema": "https://opencode.ai/config.json",

  // Set your default model here (provider_id/model_id)
  "model": "ollama/qwen2.5-coder:32b-ctx16384",

  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": {
        "baseURL": "http://localhost:11434/v1",

        // Some OpenAI-compatible clients expect an apiKey field; Ollama ignores it locally.
        "apiKey": "ollama"
      },

      "models": {
        // Paste the entire models block from Section 3 here.

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
    }
  }
}
```

---

## 6) How to adjust context sizing (without editing the script)

The adaptive installer supports **environment variable overrides** so you can tune it per machine.

### Override examples

#### Make large models use bigger ctx on Tier B machines

```bash
CTX_LARGE_B=32768 bash install-models-with-ctx.sh
```

#### Make small models use huge ctx on Tier C machines

```bash
CTX_SMALL_C=131072 bash install-models-with-ctx.sh
```

#### Override multiple values at once

```bash
CTX_LARGE_B=32768 CTX_MED_B=32768 CTX_SMALL_B=65536 bash install-models-with-ctx.sh
```

### What you can override

- `CTX_LARGE_[A|B|C|D]` — 30B+ models
- `CTX_MED_[A|B|C|D]` — 13B–27B models
- `CTX_SMALL_[A|B|C|D]` — ≤8B models
- `CTX_VISION_[A|B|C|D]` — vision models (kept more conservative)

---

## 7) Troubleshooting / sanity checks

### Verify variants exist

```bash
ollama list
```

### If OpenCode doesn’t show new models

OpenCode merges config from multiple locations and does not hot-reload; restart OpenCode after edits.citeturn20search195

---

## Downloads

- `opencode_ollama_master_wiki_v3_full_example.md` (this page)
- `install-models-with-ctx.sh` (adaptive installer)
- `install-models-with-ctx-adaptive.sh` (same script, alternate name)
