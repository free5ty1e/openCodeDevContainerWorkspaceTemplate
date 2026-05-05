# Ollama 64GB Safe Defaults & Recommendations

This document provides **known-good defaults** for running Ollama models on a 64 GB unified-memory system (Apple Silicon Macs, shared VRAM/RAM).

---

## Global Defaults (Recommended)

- **Quantization:** `Q4_K_M`
- **Context (general):** 8192
- **Context (large models ≥30B):** 16384
- **Max concurrent sessions:** 1–2

---

## Recommended Daily Setup

### General / Analysis

```bash
ollama pull llama3.3:70b
```

- Context: 16384
- Typical RAM use: ~45–50 GB
- Best for: long documents, analysis, reasoning

---

### Coding / OpenCode

```bash
ollama pull qwen2.5-coder:32b
```

- Context: 16384
- Typical RAM use: ~22–26 GB
- Best for: IDE agents, refactors, test generation

---

### Agent / Tool-Calling

```bash
ollama pull qwen3-coder:30b
```

- Context: 16384
- Typical RAM use: ~18–22 GB
- Best for: autonomous agents, OpenCode planning/build loops

---

### RAG / Embeddings

```bash
ollama pull nomic-embed-text
```

- RAM use: <1 GB
- Recommended chunk size: 512–1024 tokens

---

## Stability Rules

- Avoid running **two ≥30B models simultaneously**
- Reduce context before changing quantization
- Keep ≥10 GB headroom for macOS

---

*Validated on Apple Silicon M2/M3 Max systems*.
