# Ollama 64GB Reference Pack

This repository contains **practical, production‑grade documentation** for running **Ollama on 64GB unified‑memory systems** in real enterprise environments.

It is opinionated by design and focuses on:

- Deterministic context sizing (`-ctxNNNN` variants)
- Clear separation between *chat*, *coding*, and *automation* workflows
- Devcontainer‑safe and enterprise‑compliant setups
- Locked‑down firewall / endpoint policy realities
- Repeatable, supportable configurations

Where possible, recommendations favor **predictability, debuggability, and policy compliance** over maximum raw benchmarks.

---

## 🧱 Quantization Baseline

Unless otherwise stated, all guidance assumes:

- **Q4_K_M** quantization
- Apple Silicon (M‑series)
- Unified memory systems (64 GB class unless otherwise noted)

---

## 📚 Core 64GB Ollama Reference Docs

These documents define the **model selection and tuning baseline** for this repo.

| Document | Purpose |
|--------|--------|
| [ollama_models_64gb_reference_pretty.md](ollama_models_64gb_reference_pretty.md) | Human‑readable model catalog |
| [ollama_models_64gb_csv.md](ollama_models_64gb_csv.md) | Script‑friendly model matrix |
| [ollama_64gb_safe_defaults.md](ollama_64gb_safe_defaults.md) | Known‑good conservative defaults |
| [ollama_64gb_ctx_cheatsheet.md](ollama_64gb_ctx_cheatsheet.md) | Context sizing guidance by model |
| [ollama_64gb_devcontainer_safe.md](ollama_64gb_devcontainer_safe.md) | Models safe for Docker / devcontainers |
| [ollama_model_licenses.md](ollama_model_licenses.md) | High‑level license notes |

---

## 🧠 OpenCode + Ollama (Automation Layer)

OpenCode is treated as an **automation and orchestration layer**, *not* as the default low‑latency chat UI.

| Document | Purpose |
|--------|--------|
| [opencode_ollama_master_wiki_v4.md](opencode_ollama_master_wiki_v4.md) | Primary OpenCode reference (install, config, strategy) |
| [opencode_ollama_faq_v1.md](opencode_ollama_faq_v1.md) | FAQ and design decisions |
| [opencode_ollama_64gb_recommendations.md](opencode_ollama_64gb_recommendations.md) | OpenCode‑specific model guidance |

---

## ⚡ Bypassing OpenCode for Low‑Latency Work

These documents cover **direct‑to‑Ollama workflows** where latency matters.

| Document | Purpose |
|--------|--------|
| [ollama_opencode_alternatives.md](ollama_opencode_alternatives.md) | Step‑by‑step alternatives to OpenCode (CLI, TUI, WebUI) |
| [quick-decision-matrix.md](quick-decision-matrix.md) | One‑page guide: which tool to use *right now* |

---

## 🌐 Networking, Exposure & Firewall Scenarios

These docs address **real enterprise networking constraints**, including policy‑locked firewalls.

| Document | Use when |
|--------|----------|
| [Networking-Scenarios.md](Networking-Scenarios.md) | Choose the correct supported network model |
| [lan_exposure.md](lan_exposure.md) | LAN exposure when inbound access *is allowed* |
| [ollama_firewall_locked_access.md](ollama_firewall_locked_access.md) | Policy‑safe access when inbound firewall rules are blocked (general) |
| [ollama_firewall_locked_windows_devcontainer.md](ollama_firewall_locked_windows_devcontainer.md) | Policy‑safe runbook: Windows devcontainer → macOS Ollama via SSH reverse tunnel (specific) |

Both firewall‑locked documents are intentionally kept:
- one is **general / reusable**
- one is **specific and operational**

---

## 🛠️ Installation & Helper Scripts

| Script | Purpose |
|------|---------|
| [install-models-with-ctx-v2.sh](install-models-with-ctx-v2.sh) | **Recommended**: pull models, create `-ctxNNNN` variants automatically, generate `opencode.json.generated` |
| [install-models-with-ctx.sh](install-models-with-ctx.sh) | Legacy v1 script (kept for reference) |

---

## 🔧 Context Tuning (Why & How)

Ollama defaults most models to small context windows (~2–4k tokens). Modern open‑weight models support much larger contexts, **but Ollama will silently truncate unless `num_ctx` is explicitly set**.

The v2 install script creates **persistent model variants** such as:

```text
qwen2.5-coder:32b-ctx16384
```

This provides:

- No silent truncation
- Predictable memory usage
- Clean model switching (OpenCode, oterm, CLI)

---

### Default Memory Tiers (Reference)

| Tier | Total RAM | Large (30B+) | Medium (13–27B) | Small (≤8B) | Vision |
|----:|---------:|-------------:|---------------:|------------:|-------:|
| A | <48 GB | 8k | 8k | 16k | 8k |
| B | 48–80 GB | 16k | 16k | 32k | 8k |
| C | 80–128 GB | 32k | 32k | 64k | 16k |
| D | ≥128 GB | 48k | 64k | 128k | 32k |

Vision models are intentionally capped lower due to image token density.

---

## 🧪 Typical Working Pattern (Recommended)

- **Fast chat / thinking:** `oterm` or `ollama run`
- **Daily default model:** 8B class, larger ctx
- **Heavy coding:** 30–32B class, conservative ctx
- **Automation / tools:** OpenCode (intentionally, selectively)

This separation avoids unnecessary latency while keeping OpenCode available where it adds value.

---

## Intended Audience

- Apple Silicon power users
- Engineers using devcontainers
- Teams operating under endpoint / firewall policy
- Audit‑ or compliance‑sensitive environments

---

## Explicit Non‑Goals

This repository **does not attempt** to support:

- Windows‑hosted Ollama
- Internet‑exposed Ollama servers without auth
- Ad‑hoc firewall bypassing
- Undocumented Docker or networking hacks

If a setup does not match these documented patterns, the correct solution is **redesign**, not troubleshooting.
