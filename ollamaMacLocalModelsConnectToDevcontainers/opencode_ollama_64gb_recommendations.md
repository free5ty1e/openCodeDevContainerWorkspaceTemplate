# OpenCode + Ollama: Recommended Models (64GB)

This guide maps **OpenCode workflows** to Ollama models that fit comfortably on 64 GB systems.

---

## Primary OpenCode Model

```bash
ollama pull qwen2.5-coder:32b
```

- Best overall SWE-bench and HumanEval performance locally
- Strong tool-calling support
- Recommended for:
  - `Plan` mode
  - Multi-file refactors
  - Test generation

---

## Secondary / Fallback Models

### Fast Iteration

```bash
ollama pull llama3:8b
```

- Extremely fast
- Good for quick diffs, explanations

---

### Heavy Reasoning Tasks

```bash
ollama pull llama3.3:70b
```

- Use selectively
- Best for architectural planning and audits

---

## Recommended OpenCode Defaults

- Primary provider: `qwen2.5-coder:32b`
- Context: 16384
- Secondary fallback: `llama3:8b`

---

*Verified with OpenCode CLI and devcontainer workflows.*
