# Accessing Ollama When Firewall Settings Are Policy‑Locked

This document captures the **recommended, policy‑safe procedure** for accessing **Ollama running on a corporate‑managed laptop** when:

- You **cannot** open inbound firewall ports
- LAN exposure (`0.0.0.0:11434`) is blocked or discouraged
- You still need to use Ollama from:
  - devcontainers
  - other machines
  - remote tools (OpenCode, oterm, scripts, etc.)

The solution is to use **outbound tunnels**, not inbound firewall changes.

---

## Core principle (read first)

> Corporate endpoint firewalls almost always block **inbound** connections but allow **outbound** ones.

Therefore:
- ❌ Do NOT try to open port `11434` on the laptop
- ✅ Do route traffic **outbound** from the laptop using a tunnel

The most reliable option is **SSH reverse port forwarding**.

---

## Recommended solution: SSH reverse tunnel (`ssh -R`)

This is the **cleanest and most policy‑friendly** way to reach Ollama.

### What you need

- Ollama running on your laptop (localhost only)
- SSH access **from the laptop** to another machine you control
  - dev server
  - VM
  - jump host
  - another workstation

No inbound firewall access is required.

---

## Architecture overview

```
Client / Devcontainer
        │
        │  http://localhost:11434
        ▼
Remote host (SSH server)
        │
        │  SSH reverse tunnel
        ▼
Laptop (Ollama on localhost)
```

- Ollama stays bound to `127.0.0.1`
- The laptop initiates the connection
- All traffic flows through encrypted SSH

---

## Step‑by‑step instructions

### Step 1 — Run Ollama locally (default, no LAN bind)

On the laptop:

```bash
ollama serve
```

Confirm it is listening locally:

```bash
lsof -n -iTCP:11434 -sTCP:LISTEN
```

Expected output:

```text
127.0.0.1:11434 (LISTEN)
```

This is **correct** for this scenario.

---

### Step 2 — Create the SSH reverse tunnel

From the **same laptop**, initiate an SSH session **outbound**:

```bash
ssh -R 127.0.0.1:11434:localhost:11434 user@REMOTE_HOST
```

Explanation:
- `-R` = remote port forwarding
- `127.0.0.1:11434` (remote side) ← exposed
- `localhost:11434` (local side) ← Ollama

Leave this SSH session open.

---

### Step 3 — Verify from the remote host

On `REMOTE_HOST`:

```bash
curl http://localhost:11434/api/tags
```

If this succeeds, the tunnel is active.

---

## Using this with devcontainers

### Devcontainer runs on the remote host

Inside the devcontainer:

```bash
export OLLAMA_HOST=http://localhost:11434
```

All tools now see Ollama as a local service.

---

### Devcontainer runs elsewhere

Have the devcontainer connect to `REMOTE_HOST:11434`.

The firewall is no longer involved with the laptop.

---

## Why this works (and LAN exposure fails)

| Approach | Result |
|--------|-------|
| Bind to `0.0.0.0` | Blocked by firewall policy |
| Open inbound port | Denied / audited |
| SSH reverse tunnel | ✅ Works (outbound only) |

This pattern is widely used for databases, internal web apps, and developer tools.

---

## Security & compliance benefits

- ✅ No inbound firewall changes
- ✅ Encrypted end‑to‑end (SSH)
- ✅ Access scoped to the SSH user
- ✅ Easy to audit and disable
- ✅ No permanent network exposure

This is typically **approved by security teams** where LAN binding is not.

---

## Common variations

### Keep tunnel running in background

```bash
ssh -N -f -R 127.0.0.1:11434:localhost:11434 user@REMOTE_HOST
```

### Use a non‑default remote port

```bash
ssh -R 127.0.0.1:51434:localhost:11434 user@REMOTE_HOST
```

Then:

```bash
curl http://localhost:51434/api/tags
```

---

## When NOT to use this approach

- You need multi‑user access without SSH
- You need browser‑based public URLs
- SSH outbound connections are blocked

In those cases, consider:
- Microsoft Dev Tunnels
- Cloudflare Tunnel (if allowed)

---

## Summary

If firewall rules are locked:

- ✅ Keep Ollama on `localhost`
- ✅ Use SSH reverse tunnels
- ✅ Route all tooling through the tunnel

This is the **recommended and supported pattern** for enterprise laptops.
