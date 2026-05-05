# Bypassing OpenCode for Low‑Latency Local LLM Workflows (Ollama‑First Guide)

This document explains **practical, step‑by‑step alternatives to OpenCode** when you want **maximum performance and lowest latency** from local Ollama models — especially on Apple Silicon.

It covers:
- Direct Ollama workflows
- Terminal (ANSI/TUI) UIs
- Browser UIs
- Devcontainer usage (same host and LAN)
- Performance tuning and optimization
- A balanced comparison with OpenCode

The goal is not to replace OpenCode entirely, but to **use the right tool for the right job**.

---

## Mental model (important)

Think of the stack in layers:

```
UI / Tooling
├─ Ollama CLI / TUI / WebUI   ← low overhead, fast
├─ OpenCode                  ← orchestration, tools, automation
└─ Ollama server (Metal GPU)
```

When latency matters, **stay as close to Ollama as possible**.

---

## Option 1 — Direct `ollama run` (baseline, fastest)

### When to use
- Quick questions
- Sanity checks ("are you there?")
- Performance verification
- Debugging GPU / context behavior

### How to use

```bash
ollama run qwen2.5-coder:32b-ctx16384 "are you there?"
```

### Pros
- ✅ Lowest possible overhead
- ✅ Fastest time‑to‑first‑token
- ✅ Exact model behavior

### Cons
- ❌ No persistent chat history
- ❌ No UI controls beyond the terminal

### Devcontainer usage

From **inside a devcontainer** (same host):

```bash
ollama run MODEL_NAME "prompt"
```

From a **devcontainer on another machine on the LAN**:

```bash
OLLAMA_HOST=http://MAC_IP:11434 ollama run MODEL_NAME "prompt"
```

---

## Option 2 — Built‑in Ollama TUI (`ollama`)

### When to use
- Interactive chatting
- Model switching
- Zero‑install UI

### How to use

```bash
ollama
```

Navigate with arrow keys, press Enter to chat.

### Pros
- ✅ Still very low overhead
- ✅ Interactive experience
- ✅ Minimal learning curve

### Cons
- ❌ No saved sessions
- ❌ No file uploads

### Devcontainer usage
Same as `ollama run` — requires network access to the Ollama host.

---

## Option 3 — `oterm` (recommended ANSI / OpenCode‑style alternative)

`oterm` is the **closest equivalent to OpenCode’s feel**, but without the orchestration overhead.

### When to use
- Daily interactive work
- Long conversations
- Markdown & code‑heavy responses

### Install

```bash
brew install uv
uvx oterm
```

### How to use

1. Launch with `oterm`
2. Select **Provider: Ollama**
3. Choose your `-ctxNNNN` variant
4. Chat normally

### Pros
- ✅ Persistent sessions
- ✅ Streaming tokens (fast)
- ✅ Markdown, code blocks, themes
- ✅ Very low latency vs OpenCode

### Cons
- ❌ No automation / skills
- ❌ No build‑mode orchestration

### Devcontainer usage

**Same host devcontainer**:

- Ollama running on host
- Works automatically via `localhost:11434`

**Different machine on LAN**:

```bash
export OLLAMA_HOST=http://MAC_IP:11434
oterm
```

---

## Option 4 — Lightweight TUIs (LazyLlama, OWLEN)

### When to use
- Maximum responsiveness
- Keyboard‑driven workflows
- No interest in tooling / planning

### LazyLlama

- Extremely fast Rust TUI
- Excellent streaming performance

Typical use:

```bash
lazyllama
```

### OWLEN

- Pane‑based interface
- Good for reasoning‑heavy models

### Pros
- ✅ Lowest UI overhead
- ✅ Excellent performance

### Cons
- ❌ Fewer features than oterm
- ❌ Smaller communities

### Devcontainer usage
Same networking rules apply as above.

---

## Option 5 — Open WebUI (browser‑based)

### When to use
- ChatGPT‑style experience
- File uploads / PDFs
- Multimodal workflows

### Install (Docker, same host)

```bash
docker run -d \
  -p 3000:8080 \
  --add-host=host.docker.internal:host-gateway \
  -v open-webui:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```

Open: http://localhost:3000

### Pros
- ✅ Rich UI
- ✅ History, uploads, RAG
- ✅ Still faster than OpenCode

### Cons
- ❌ Browser overhead
- ❌ Heavier than terminal UIs

### Devcontainer usage

Devcontainers connect via browser → host → Ollama.

---

## Where OpenCode *does* make sense

### Best use cases
- Multi‑step automation
- Skills / tools / MCP servers
- Build‑mode workflows
- Repeatable project scaffolding

### Known trade‑offs
- ❌ High first‑token latency
- ❌ Slower streaming
- ❌ Extra prompt planning overhead

**Recommendation**: treat OpenCode as an *automation layer*, not a chat UI.

---

## Performance optimization checklist (all options)

### 1. Verify GPU usage

```bash
ollama ps
```

Look for `100% GPU`.

### 2. Keep models warm

```bash
launchctl setenv OLLAMA_KEEP_ALIVE -1
```

Prevents reload between prompts.

### 3. Match model size to task

| Task | Recommended model |
|----|------------------|
| Fast chat | Qwen 3 8B ctx 32k |
| Coding | Qwen 2.5 Coder 32B ctx 8–16k |
| Long docs | 8–14B with larger ctx |

### 4. Avoid oversized context on large models

Large ctx × large models → partial GPU spill → severe slowdown.

### 5. Prefer direct Ollama paths

Fewer layers between UI and Ollama = better performance.

---

## Recommended combined workflow

- **Fast thinking / chat** → `oterm`
- **Heavy coding (selectively)** → `ollama run` or oterm
- **Automation / tools** → OpenCode

This is the most effective pattern on Apple Silicon today.

---

✅ This separation is intentional — no single tool does everything *and* stays fast.
