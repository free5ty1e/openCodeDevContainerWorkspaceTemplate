# Ollama 64GB Reference Pack

This repository contains **practical, production‑grade documentation** for running **Ollama on 64GB unified‑memory systems**, with a strong focus on:

- Deterministic context sizing (`-ctxNNNN` variants)
- OpenCode‑friendly workflows
- Devcontainer safety
- Explicitly supported networking scenarios
- Audit‑friendly / air‑gapped environments

Where possible, recommendations favor **predictability and supportability** over peak benchmarks.

---

## 🧱 Quantization Baseline

All recommendations assume:

- **Q4_K_M**
- Apple Silicon (M‑series)
- Unified memory systems (64 GB class unless otherwise stated)

---

## 📚 Core 64GB Ollama Reference Docs

These documents focus on **model selection, sizing, and safe defaults** for 64 GB systems.

| Document | Purpose |
|--------|--------|
| [ollama_models_64gb_reference_pretty.md](ollama_models_64gb_reference_pretty.md) | Main human‑readable model catalog |
| [ollama_64gb_safe_defaults.md](ollama_64gb_safe_defaults.md) | Known‑good, conservative defaults |
| [ollama_models_64gb_csv.md](ollama_models_64gb_csv.md) | Script‑friendly model matrix |
| [ollama_64gb_ctx_cheatsheet.md](ollama_64gb_ctx_cheatsheet.md) | Per‑model context tuning guidance |
| [ollama_64gb_devcontainer_safe.md](ollama_64gb_devcontainer_safe.md) | Safe subset for Docker / devcontainers |
| [ollama_model_licenses.md](ollama_model_licenses.md) | High‑level license notes |
| [opencode_ollama_64gb_recommendations.md](opencode_ollama_64gb_recommendations.md) | OpenCode‑specific 64 GB guidance |

---

## 🧠 OpenCode + Ollama (Primary Workflow)

| Document | Purpose |
|--------|--------|
| [opencode_ollama_master_wiki_v4.md](opencode_ollama_master_wiki_v4.md) | **Primary reference**: quick start, adaptive installer, full `opencode.json`, role‑based model strategy |
| [opencode_ollama_faq_v1.md](opencode_ollama_faq_v1.md) | FAQ covering common failure modes and design decisions |
| [ollama_opencode_alternatives.md](ollama_opencode_alternatives.md) | Step‑by‑step alternatives to bypass OpenCode (low latency) |
| [quick-decision-matrix.md](quick-decision-matrix.md) | One‑page routing guide: pick the right tool fast |

---

## 🌐 Networking & Platform Scenarios

Start here if you are unsure which setup applies to your machine.

| Document | Use when |
|--------|----------|
| [Networking-Scenarios.md](Networking-Scenarios.md) | Choose the correct supported networking model |
| [opencode_ollama_devcontainer_note.md](opencode_ollama_devcontainer_note.md) | macOS devcontainers using `host.docker.internal` |
| [opencode_ollama_windows_wsl_devcontainer.md](opencode_ollama_windows_wsl_devcontainer.md) | **Windows + WSL Docker → Mac‑hosted Ollama** |

---

## 🛠️ Scripts

| Script | Purpose |
|------|---------|
| [install-models-with-ctx-v2.sh](install-models-with-ctx-v2.sh) | **Recommended**: pulls models, creates `-ctxNNNN` variants automatically, generates `opencode.json.generated` |
| [install-models-with-ctx.sh](install-models-with-ctx.sh) | Legacy v1 script (kept for reference) |

---

## 🛠️ install-models-with-ctx-v2.sh (Recommended)

This repository includes an **automated v2 installer** that creates **context‑tuned Ollama model variants** and generates an OpenCode config example.

### What the v2 script does

- ✅ Detects total system RAM and selects conservative defaults
- ✅ Pulls all required base models
- ✅ Creates persistent `-ctxNNNN` variants using `ollama create`
- ✅ Safe to re‑run (idempotent)
- ✅ Generates `opencode.json.generated` if none exists

### Basic usage

```bash
chmod +x install-models-with-ctx-v2.sh
./install-models-with-ctx-v2.sh
```

After running:
- `ollama list` will show `-ctxNNNN` variants
- `opencode.json.generated` will be created for reference

---

## 🔧 Context tuning (how & why)

Ollama defaults most models to very small context windows (~2–4k tokens). Modern open‑weight models support much larger windows, but **Ollama will silently truncate input unless `num_ctx` is increased**.

The v2 script creates **saved model variants** with explicit context sizes, for example:

```text
FROM qwen2.5-coder:32b
PARAMETER num_ctx 16384
```

This ensures:
- No silent truncation of long prompts or codebases
- Stable, predictable memory usage
- Seamless model switching in OpenCode

---

### Default memory tiers

| Tier | Total RAM | Large (30B+) | Medium (13–27B) | Small (≤8B) | Vision |
|----:|---------:|-------------:|---------------:|------------:|-------:|
| A | <48 GB | 8k | 8k | 16k | 8k |
| B | 48–80 GB | 16k | 16k | 32k | 8k |
| C | 80–128 GB | 32k | 32k | 64k | 16k |
| D | ≥128 GB | 48k | 64k | 128k | 32k |

Vision models are intentionally capped lower because image tokens consume more KV cache.

---

## 🔧 Tuning for common scenarios (no script edits)

Override defaults by exporting environment variables **before** running the script.

### Large codebases / monorepos (64 GB machines)

```bash
CTX_LARGE_B=32768 \
CTX_SMALL_B=65536 \
./install-models-with-ctx-v2.sh
```

### Long documents / RAG experiments

```bash
CTX_LARGE_B=32768 \
CTX_MED_B=32768 \
./install-models-with-ctx-v2.sh
```

### Keep DeepSeek fast and stable

```bash
DEEPSEEK_CTX_OVERRIDE=32768 ./install-models-with-ctx-v2.sh
```

### Very long context with small models

```bash
CTX_SMALL_B=131072 ./install-models-with-ctx-v2.sh
```

---

## Intended audience

- Apple Silicon power users
- OpenCode users
- Devcontainer workflows
- PCI or air‑gapped environments

---

## Non‑goals (by design)

This repository intentionally does **not** attempt to support:

- Windows‑hosted Ollama
- Internet‑exposed Ollama servers
- Dynamic per‑request `num_ctx`
- Multi‑host Ollama routing
- Undocumented Docker networking tricks

If a setup does not match the documented scenarios, the correct solution is **redesign**, not troubleshooting.
