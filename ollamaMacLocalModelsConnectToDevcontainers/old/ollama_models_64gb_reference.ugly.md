# Ollama Models Usable on a 64 GB Unified Memory System

**Scope**: This document lists Ollama models that can realistically be run on a system with **64 GB shared RAM / VRAM** (e.g., Apple Silicon M‑series) using standard quantized (`Q4_K_M`) variants.

**Sources**: Ollama official library, VRAM/RAM sizing guides, and 2025–2026 benchmarking analyses. Models exceeding practical 64 GB requirements (≥120B dense or very large MoE) are excluded.

---

## How to Read This Table

- **Max Size** = largest practical variant on 64 GB (Q4_K_M)
- **Fits 64 GB?** assumes room for OS + KV cache (8–16k context)
- **Primary Use** is the area where the model family excels

---

## General / Instruction Models

| Model Family | Variants ≤64 GB | Max Params | Fits 64 GB | Notes |
|-------------|----------------|------------|-----------|-------|
| Llama 3 / 3.1 / 3.3 | 8B, 70B | 70B | ✅ | Best general-purpose + long-context; 70B runs well at Q4 |
| Llama 3.2 | 1B, 3B | 3B | ✅ | Ultra‑light, fast inference |
| Mistral | 7B | 7B | ✅ | Fast, stable general assistant |
| Gemma 2 / 3 | 2B, 9B, 12B, 27B | 27B | ✅ | Efficient, instruction-strong |
| Phi‑3 / Phi‑4 | 3.8B, 14B | 14B | ✅ | Excellent reasoning per parameter |
| Vicuna | 7B, 13B | 13B | ✅ | Conversational fine‑tunes |
| Neural Chat | 7B | 7B | ✅ | Clear, instructional output |
| Starling | 7B | 7B | ✅ | Strong instruction compliance |
| Orca Mini | 3B, 7B, 13B | 13B | ✅ | Small‑model efficiency |

---

## Coding / Developer Models

| Model Family | Variants ≤64 GB | Max Params | Fits 64 GB | Notes |
|-------------|----------------|------------|-----------|-------|
| Qwen2.5‑Coder | 0.5B, 1.5B, 3B, 7B, 14B, 32B | 32B | ✅ | Top‑tier coding accuracy |
| Qwen3‑Coder | 4B, 8B, 14B, 30B | 30B | ✅ | Agent‑ready coding models |
| Code Llama | 7B, 13B, 34B | 34B | ✅ | Multi‑language coding |
| StarCoder | 7B, 15B | 15B | ✅ | Code generation / completion |
| WizardLM‑2 | 7B | 7B | ✅ | Instruction + code balance |

---

## Reasoning / Math‑Focused Models

| Model Family | Variants ≤64 GB | Max Params | Fits 64 GB | Notes |
|-------------|----------------|------------|-----------|-------|
| DeepSeek‑R1 | 7B, 8B, 14B, 32B | 32B | ✅ | Chain‑of‑thought reasoning |
| Qwen3 | 4B, 8B, 14B, 30B, 32B | 32B | ✅ | Reasoning + tools |
| Phi‑4 | 14B | 14B | ✅ | Math / STEM strength |

---

## Multimodal (Vision / Tools)

| Model Family | Variants ≤64 GB | Max Params | Fits 64 GB | Notes |
|-------------|----------------|------------|-----------|-------|
| LLaVA | 7B, 13B, 34B | 34B | ✅ | Vision + language |
| Gemma Vision | 12B, 27B | 27B | ✅ | Multimodal + tools |
| Qwen3.5 Vision | 4B, 9B, 27B, 35B | 35B | ✅ | Vision, function calling |

---

## Embedding Models (RAG)

| Model Family | Size | Fits 64 GB | Notes |
|-------------|------|-----------|-------|
| nomic‑embed‑text | ~300 MB | ✅ | Standard RAG embeddings |
| mxbai‑embed‑large | ~335 MB | ✅ | High‑quality embeddings |

---

## Models Intentionally Excluded

| Model | Reason |
|------|--------|
| Llama 3.1 405B | >140 GB even quantized |
| Qwen3 235B | Exceeds 64 GB |
| Qwen3.5 122B | Requires ≥96 GB |
| GPT‑OSS 120B | Not stable within 64 GB |
| Mixtral 8×22B | ~80 GB minimum |

---

## Practical Guidance

- **Daily driver**: Llama 3.3 70B or Qwen3 32B
- **Coding**: Qwen2.5‑Coder 32B
- **Agents**: Qwen3‑Coder 30B
- **RAG**: Llama 3 70B + nomic‑embed‑text

---

*As of May 2026. Model availability and quantization defaults are defined by Ollama library releases.*
