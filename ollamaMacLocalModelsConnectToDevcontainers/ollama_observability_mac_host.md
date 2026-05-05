# Observability for Ollama on macOS

This document explains how to observe **who is using your Ollama server and when**, with a focus on **GPU / inference usage** when Ollama is running on a macOS host (Apple Silicon) and shared across a local network (LAN).

The goal is **practical observability**, not perfect accounting. Ollama does not provide per-user auth or metrics, so we rely on system- and network-level signals.

---

## What Ollama Does (and Does Not) Expose

Ollama currently:
- ✅ Exposes a local HTTP API on port 11434
- ✅ Logs basic lifecycle events
- ✅ Tracks loaded models and running sessions

Ollama does **not**:
- ❌ Authenticate users
- ❌ Identify clients by name
- ❌ Provide per-user GPU or token accounting

Because of this, observability is **correlational**.

---

## 1. See Active Models and Inference Sessions

```bash
ollama ps
```

This shows:
- Which models are currently loaded
- How long they have been active

If you see a large model listed here, your GPU / UMA memory is in use.

---

## 2. Identify Which Machines Are Connected (Key Signal)

```bash
lsof -n -iTCP:11434
```

This reveals **all client IP addresses** currently connected to Ollama.

Example:

```
ollama   12345 user   TCP 192.168.1.42:11434->192.168.1.88:51234 (ESTABLISHED)
```

From this you can determine:
- The *source IP* (who is using it)
- Whether multiple clients are connected

Map IPs to machines via your router, DHCP table, or DNS.

---

## 3. Correlate Time and Load

When performance drops:

```bash
ollama ps
lsof -n -iTCP:11434
top
```

Together, these answer:
- Is a model running?
- Who is connected?
- Is CPU or memory pressure high?

On Apple Silicon, GPU load appears primarily as **memory pressure**, not a simple GPU %.

---

## 4. Monitor Logs for Usage Events

Ollama logs live in:

```text
~/.ollama/logs/
```

To watch activity:

```bash
tail -f ~/.ollama/logs/server.log
```

You’ll see:
- Model load/unload
- Server restarts

Logs do not record client identity, but they provide **timing** for correlation.

---

## 5. Optional: Enable Access Logging via a Local Reverse Proxy

For *true request-level observability*, place a local reverse proxy (Nginx or Caddy) in front of Ollama **on the LAN interface only**.

Benefits:
- Request timestamps
- Source IP logging
- Optional rate limiting

Architecture:

```
[Clients] → [Proxy :11435] → [Ollama :11434]
```

This does **not** require internet exposure.

---

## 6. Practical Attribution Strategy

In practice, combine:

| Signal | What it Tells You |
|------|-------------------|
| ollama ps | Which model is active |
| lsof :11434 | Which machines are connected |
| logs | When usage occurred |
| router/DNS | Who owns that IP |

This is sufficient for home labs and small teams.

---

## 7. Known Limitations

- No per-user quotas
- No identity without a proxy or VPN
- No per-request GPU metrics

If you need per-user attribution, combine Ollama with:
- Tailscale (identity via VPN)
- Reverse proxy auth

---

## Summary

Ollama observability today answers:
- ✅ "Is the GPU in use?"
- ✅ "Which model is running?"
- ✅ "Which machine is connected?"

But not:
- ❌ "Which user account is this?"

This document gives you **all currently available signals** without modifying Ollama itself.

---

*Document generated May 2026 for macOS-hosted, LAN-shared Ollama instances.*
