# Quick Decision Matrix — Which Tool Should I Use?

This is a **one‑page routing guide** for choosing the right interface for local models hosted by **Ollama** (especially on Apple Silicon).

**Principle:** If you care about *latency*, stay close to Ollama. If you care about *automation*, use OpenCode.

---

## 0) 10‑second health check (before blaming tools)

1) **GPU in use?**

```bash
ollama ps
```

Look for `100% GPU` in the Processor column.

2) **Model warm?** (macOS)

```bash
launchctl setenv OLLAMA_KEEP_ALIVE -1
```

---

## 1) Pick by goal (fastest path)

| Your goal right now | Best tool | Why | Typical experience |
|---|---|---|---|
| “Just answer this quickly” (chat / sanity check) | `ollama run` | Lowest overhead | Fastest first token |
| “Interactive chat, switch models quickly” | `ollama` (built‑in TUI) | Zero install, minimal overhead | Very fast |
| “Terminal UI like OpenCode, but fast” | `oterm` | Persistent sessions, markdown, low overhead | Fast streaming |
| “I want a ChatGPT‑style UI + history + uploads” | Open WebUI | Rich UI + uploads | Moderate overhead |
| “Work on a repo: read files, run commands, apply changes” | OpenCode | Tool/skill orchestration | Higher overhead |
| “Use vision models for OCR / screenshots” | Open WebUI or `oterm` | Easier attachments (WebUI), fast TUI otherwise | Depends on image size |
| “I need scripted automation / repeatable workflows” | OpenCode | Built for agents + tools | Slower but powerful |

---

## 2) Pick by workload size (model guidance)

| Workload | Recommended model class | Notes |
|---|---|---|
| Fast iteration, short prompts | 8B models | Great UX, low latency |
| Medium tasks, longer conversations | 14B models | Still responsive on 64GB |
| Heavy coding reasoning | 30–34B models | Use smaller context variants to keep speed |
| Vision / OCR | 27B / 32B VLM | Keep ctx conservative; images inflate memory |

Rule of thumb: **increase context on smaller models first**, not on 30B+.

---

## 3) Devcontainer support (what works)

| Where you are | What to use | Base URL / host |
|---|---|---|
| Devcontainer on same Mac as Ollama | Any tool inside container | `http://host.docker.internal:11434` |
| Devcontainer on another LAN machine (Mac Ollama server) | Any tool inside container | `http://MAC_IP:11434` |
| Windows WSL2 Docker → Mac Ollama server | Any tool inside container | `http://MAC_IP:11434` (NOT `host.docker.internal`) |

---

## 4) When OpenCode is the right answer (and when it isn’t)

### Use OpenCode when you want:
- file reading / writing and repo context
- tool execution (shell, tests, linters)
- multi‑step planning and automation

### Avoid OpenCode when you want:
- fastest interactive chat
- fastest first‑token latency
- quick model benchmarking

---

## 5) Quick performance knobs to try

### Keep models warm (macOS)

```bash
launchctl setenv OLLAMA_KEEP_ALIVE -1
```

### Reduce context before changing anything else

If a 30B+ model feels “stuck”, try a smaller ctx variant first (e.g. `…-ctx8192` or `…-ctx16384`).

### Confirm you’re using the right binary (Apple Silicon)

```bash
file "$(which ollama)"
```

You want `arm64`.

---

## Suggested default setup (most people)

- **Daily / default:** 8B model with 32k ctx
- **Heavy coding:** 30–32B model with 8–16k ctx
- **Vision:** VLM with 8k ctx
- **Automation:** OpenCode with the heavy model *selectively*
