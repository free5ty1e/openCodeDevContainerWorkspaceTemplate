# Devcontainer Setup — Windows (WSL + Docker Engine) connecting to a **Mac‑hosted Ollama** server

> **Scope / intent**
> - Ollama runs on a **Mac** (not inside Docker)
> - Windows machine uses **WSL2 + Docker Engine** (not Docker Desktop)
> - Devcontainers should talk to the **Mac’s Ollama instance only**
> - This document intentionally **does not** cover running Ollama on Windows

This is a compatibility-focused setup for older Windows machines where Docker Desktop is not available or not permitted.

---

## Mental model (important)

You have *three* separate network contexts:

1. **Mac host** – runs Ollama (`ollama serve`, TCP 11434)
2. **Windows host** – entry point for VS Code
3. **WSL2 Linux VM** – runs Docker Engine and devcontainers

Your devcontainers must:
- **Exit WSL**
- **Cross the LAN**
- **Reach the Mac directly by IP or DNS**

`host.docker.internal` **does NOT apply here**, because:
- That DNS name only maps to *the local host of the Docker engine*
- Your Docker engine is inside WSL, not on the Mac

So we must explicitly target the Mac.

---

## ✔️ Prerequisites (Mac side)

### 1) Ollama listening on the LAN

On the Mac, ensure Ollama is reachable beyond loopback.

Check listening address:

```bash
lsof -nP -iTCP:11434 | grep LISTEN
```

✅ You want to see **0.0.0.0:11434** or the Mac’s LAN IP

If Ollama only binds to `127.0.0.1`, set (once):

```bash
launchctl setenv OLLAMA_HOST 0.0.0.0
```

Then restart Ollama.

---

### 2) Determine Mac address (pick ONE)

Prefer **one stable identifier**:

- ✅ Static LAN IP (e.g. `192.168.1.50`)
- ✅ Hostname resolvable by Windows (e.g. `macstudio.local` via mDNS)

Test from Windows PowerShell:

```powershell
curl http://192.168.1.50:11434/api/tags
```

You should get JSON.

---

## ✔️ Windows / WSL side checks

### 1) Verify WSL can reach the Mac

From inside WSL:

```bash
curl http://192.168.1.50:11434/api/tags
```

If this fails:
- Check Windows firewall outbound rules
- Check Mac firewall allows inbound 11434

---

### 2) Docker Engine in WSL

This setup assumes:

```text
Windows
└─ WSL2 Linux distro
   └─ Docker Engine (dockerd)
      └─ Devcontainers
```

That means:
- Containers share the **WSL VM network**, not Windows’ loopback
- Any external service must be reachable by **real IP / DNS**

---

## ✅ Devcontainer configuration

### OpenCode provider config (inside container)

Use the **Mac address directly**:

```jsonc
{
  "provider": {
    "ollama": {
      "options": {
        "baseURL": "http://192.168.1.50:11434/v1"
      }
    }
  }
}
```

✅ This bypasses all `host.docker.internal` assumptions
✅ Works the same for devcontainers, Docker CLI, and CI containers

---

### Optional: make the address configurable

Recommended pattern:

```jsonc
{
  "provider": {
    "ollama": {
      "options": {
        "baseURL": "${OLLAMA_BASE_URL}"
      }
    }
  }
}
```

Then in devcontainer settings:

```json
"containerEnv": {
  "OLLAMA_BASE_URL": "http://192.168.1.50:11434/v1"
}
```

This lets you:
- Repoint easily
- Share config between Mac + Windows users

---

## ✅ Verify from inside the devcontainer

```bash
curl http://192.168.1.50:11434/api/tags
```

If this works, OpenCode will work.

---

## ❌ Common pitfalls

### ❌ Using `host.docker.internal`

Fails because:
- It resolves to the **WSL VM**, not the Mac

### ❌ Binding Ollama to 127.0.0.1

Containers cannot reach loopback of another machine.

### ❌ Assuming Windows and WSL share loopback

They do not. WSL has its own virtual NIC.

---

## ✅ Supported / unsupported summary

| Scenario | Supported | Notes |
|--------|-----------|------|
| Mac → Mac devcontainer | ✅ | Use `host.docker.internal` |
| Windows + Docker Desktop → Mac Ollama | ⚠️ | Use Mac IP |
| Windows + WSL Docker → Mac Ollama | ✅ | **This document** |
| Windows + WSL Docker → localhost Ollama | ❌ | Impossible by design |

---

## Operational safety notes

- Expose Ollama **only to trusted LANs**
- Prefer firewall allow‑listing over public exposure
- Keep context sizing conservative on the Mac host

---

✅ This document is intentionally narrow and conservative. It covers *only* the supported Mac‑host → Windows‑WSL‑container path.
