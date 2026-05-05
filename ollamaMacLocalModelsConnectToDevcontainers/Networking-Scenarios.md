# Networking Scenarios — OpenCode + Ollama

> **Purpose**: Choose the *correct* networking setup for OpenCode + Ollama based on your OS and Docker runtime. This page is intentionally explicit about what *is* and *is not* supported.

---

## Canonical Assumptions

All documented scenarios assume:

- ✅ Ollama runs on **macOS**
- ✅ Ollama is **not containerized**
- ✅ TCP port **11434** is used
- ✅ Context windows are baked into saved `-ctxNNNN` variants

These constraints are intentional to keep behavior deterministic and supportable.

---

## Quick Decision Table

| Your machine | Docker runtime | Ollama location | Supported | Documentation |
|--------------|---------------|-----------------|-----------|---------------|
| macOS | Docker Desktop | Same Mac | ✅ | `opencode_ollama_master_wiki_v4.md` |
| macOS | No Docker | Same Mac | ✅ | `opencode_ollama_master_wiki_v4.md` |
| Windows | Docker Desktop | Mac | ⚠️ | Use Mac IP/hostname |
| Windows | **WSL2 + Docker Engine** | **Mac** | ✅ | `opencode_ollama_windows_wsl_devcontainer.md` |
| Windows | WSL2 + Docker Engine | Windows | ❌ | Unsupported |
| Linux | Docker Engine | Mac | ⚠️ | Use Mac IP/hostname |
| Any | Any | Multiple Ollama hosts | ❌ | Out of scope |

---

## Scenario Details

### ✅ macOS → macOS devcontainer

- Use Docker Desktop
- Use `host.docker.internal`

```jsonc
"baseURL": "http://host.docker.internal:11434/v1"
```

**Docs**:
- `opencode_ollama_master_wiki_v4.md`
- `opencode_ollama_devcontainer_note.md`

---

### ⚠️ Windows + Docker Desktop → Mac Ollama

- Do **not** rely on `host.docker.internal` to point to the Mac
- Use the Mac’s LAN IP or mDNS name

```jsonc
"baseURL": "http://192.168.1.50:11434/v1"
```

---

### ✅ Windows (WSL2 + Docker Engine) → Mac Ollama

This is a **first‑class supported scenario** for older or restricted Windows machines.

Key rules:
- Ollama must bind to `0.0.0.0` on the Mac
- Containers must connect directly to the Mac by IP or hostname
- `host.docker.internal` must **not** be used

```jsonc
"baseURL": "http://192.168.1.50:11434/v1"
```

**Docs**:
- `opencode_ollama_windows_wsl_devcontainer.md`

---

## ❌ Explicitly Unsupported Scenarios

These are documented to prevent wasted troubleshooting effort:

- Windows‑hosted Ollama
- WSL containers targeting Windows `localhost`
- Cross‑machine use of `host.docker.internal`
- Multiple Ollama hosts behind a single OpenCode config
- Dynamic per‑request `num_ctx` switching

If your setup falls here, the correct solution is **redesign**, not debugging.

---

## Operational Rules (Apply Everywhere)

- Prefer **stable IPs or hostnames** over DNS tricks
- Verify connectivity with `/api/tags` before debugging OpenCode
- Restart OpenCode after config changes (no hot reload)
- Keep Ollama restricted to trusted LANs only

---

## Suggested Ordering

1. This page (`Networking-Scenarios.md`)
2. `opencode_ollama_master_wiki_v4.md`
3. Platform‑specific devcontainer document
4. `opencode_ollama_faq_v1.md`
