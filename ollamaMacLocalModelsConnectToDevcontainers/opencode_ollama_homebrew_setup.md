# OpenCode + Ollama (Homebrew-first) Setup Guide

This document describes a **Homebrew-centric setup** for running **OpenCode** against **local Ollama models** on macOS (Apple Silicon), and for reusing those same models from **OpenCode inside a devcontainer**.

---

## Assumptions

- macOS on Apple Silicon (M-series)
- Homebrew installed (ARM64, `/opt/homebrew`)
- Docker Desktop / OrbStack / Colima providing `host.docker.internal`
- Goal: **Host runs Ollama + models once**, containers reuse them

---

## 1. Install Ollama via Homebrew

### Verify Homebrew architecture

```bash
which brew
brew --version
```

Ensure Homebrew lives under `/opt/homebrew` (native Apple Silicon).

### Install Ollama

```bash
brew install ollama

brew services start ollama

```

Verify:

```bash
ollama --version
```

### Start Ollama

```bash
ollama serve
```

Ollama listens on `127.0.0.1:11434` by default.

Verify:

```bash
curl http://localhost:11434/api/tags
```

---

## 2. Download Local Models

https://haimaker.ai/blog/ollama-opencode-setup/
https://ollama.com/library

Examples that work well on 64GB unified memory:

```bash
ollama pull qwen2.5-coder:32b
ollama pull codellama:34b
ollama pull starcoder:15b

ollama pull deepseek-r1:32b
ollama pull gemma3:27b
ollama pull qwen3.5-vision:35b

ollama pull qwen3:8b
ollama pull qwen3:14b
ollama pull qwen3:32b
```

List installed models:

```bash
ollama list
```

Models are stored under `~/.ollama`.

---

## 3. Install OpenCode (Host)

```bash
brew install opencode

#OR:
curl -fsSL https://opencode.ai/install | bash
```

Verify:

```bash
opencode --version
```

---

## 4. Configure OpenCode → Ollama (Host)

Create config directory:

```bash
mkdir -p ~/.config/opencode
```

Create `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": {
        "baseURL": "http://localhost:11434/v1"
      },
      "models": {
        "qwen3:8b":  { "name": "qwen3:8b" },
        "qwen3:14b": { "name": "qwen3:14b" },
        "qwen3:32b": { "name": "qwen3:32b" }
      }
    }
  }
}
```

Test:

```bash
opencode
```

---

## 5. Use Host Ollama from a Devcontainer

### Key rule

- `localhost` ❌ (container only)
- `host.docker.internal` ✅ (host macOS)

---

## 6. Install OpenCode in the Devcontainer

In `.devcontainer/devcontainer.json`:

```json
{
  "postCreateCommand": "curl -fsSL https://opencode.ai/install | bash"
}
```

---

## 7. Devcontainer OpenCode Config

Create `.opencode/opencode.container.json` in your repo:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (host)",
      "options": {
        "baseURL": "http://host.docker.internal:11434/v1"
      }
    }
  }
}
```

Install it during container creation:

```json
{
  "postCreateCommand": "mkdir -p ~/.config/opencode && cp .opencode/opencode.container.json ~/.config/opencode/opencode.json"
}
```

Test inside container:

```bash
curl http://host.docker.internal:11434/api/tags
opencode
```

---

## 8. Optional: Expose Ollama Beyond localhost

Only if needed (usually not):

```bash
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
```

Restart Ollama and verify:

```bash
lsof -i :11434
```

⚠️ Ollama has no built-in authentication. Use only on trusted networks.

---

## Outcome

✅ Native Apple Silicon inference
✅ One shared model cache
✅ Works on host + devcontainers
✅ No Docker GPU penalties
✅ Fully Homebrew-friendly setup
