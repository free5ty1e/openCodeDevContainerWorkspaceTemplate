# Ollama Models for 64 GB Unified Memory Systems (Authoritative Reference)

**Purpose**: A practical, pull-ready catalog of Ollama models that are *actually usable* on a 64 GB shared RAM/VRAM system (Apple Silicon Macs, UMA workstations).

This document focuses on:
- Exact `ollama pull` identifiers
- Realistic context window sizes
- Practical memory consumption (Q4_K_M baseline)

All tables are formatted for **human readability** in GitHub, VS Code, Obsidian, and Azure DevOps wikis.

---

## General / Instruction Models

| ollama pull ID | Params | Default Context | Practical Context (64 GB) | Typical RAM @ Q4 | Notes |
|---------------|--------|-----------------|--------------------------|------------------|-------|
| llama3.3:70b | 70B | 128k | 16k–32k | 45–50 GB | Flagship general model, excellent instruction following |
| llama3:8b | 8B | 8k | 32k–64k | ~6 GB | Fast daily driver, very tolerant of large ctx |
| llama3.2:3b | 3B | 8k | 64k | ~3 GB | Ultra-light utility model |
| mistral:7b | 7B | 8k | 32k | ~5 GB | Fast, stable, low-latency |
| gemma2:27b | 27B | 8k | 16k | 18–20 GB | Very efficient mid-large model |
| phi4:14b | 14B | 16k | 32k | 9–10 GB | Excellent math & reasoning per param |

---

## Coding / Developer Models

| ollama pull ID | Params | Default Context | Practical Context (64 GB) | Typical RAM @ Q4 | Notes |
|---------------|--------|-----------------|--------------------------|------------------|-------|
| qwen2.5-coder:32b | 32B | 16k | 16k–32k | 22–26 GB | Best overall local coding model |
| qwen3-coder:30b | 30B | 256k | 16k–32k | 18–22 GB | Agentic coding, strong tool use |
| codellama:34b | 34B | 8k | 16k | 24–28 GB | Mature multi-language support |
| starcoder:15b | 15B | 8k | 32k | ~10 GB | Completion-heavy workflows |

---

## Reasoning / Math Models

| ollama pull ID | Params | Default Context | Practical Context (64 GB) | Typical RAM @ Q4 | Notes |
|---------------|--------|-----------------|--------------------------|------------------|-------|
| deepseek-r1:32b | 32B | 32k | 16k | 20–24 GB | Chain-of-thought reasoning |
| qwen3:32b | 32B | 32k | 16k–32k | 22–26 GB | Reasoning + tools |
| phi4:14b | 14B | 16k | 32k | 9–10 GB | STEM + logic strength |

---

## Multimodal (Vision)

| ollama pull ID | Params | Default Context | Practical Context (64 GB) | Typical RAM @ Q4 | Notes |
|---------------|--------|-----------------|--------------------------|------------------|-------|
| llava:34b | 34B | 8k | 16k | 24–28 GB | Vision + text understanding |
| gemma3:27b | 27B | 8k | 16k | 18–20 GB | Efficient multimodal assistant |
| qwen3.5-vision:35b | 35B | 8k | 16k | 28–32 GB | Vision + structured output |

---

## Embeddings / RAG

| ollama pull ID | Size | Context | Typical RAM | Notes |
|---------------|------|---------|-------------|-------|
| nomic-embed-text | ~300 MB | 8k | <1 GB | Default RAG embedding |
| mxbai-embed-large | ~335 MB | 8k | <1 GB | Higher-quality embeddings |

---

## Models Intentionally Excluded

| Model | Reason |
|------|--------|
| llama3.1:405b | >140 GB even quantized |
| qwen3:235b | Far exceeds 64 GB |
| qwen3.5:122b | Requires ≥96 GB |
| mixtral:8x22b | ~80 GB minimum |

---

*Freshly regenerated May 2026. Q4_K_M baseline assumed throughout.*
