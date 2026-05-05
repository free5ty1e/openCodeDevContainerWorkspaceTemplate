# Exposing a Host macOS Ollama Instance to the Local Network for OpenCode Clients

This guide extends the local Ollama + OpenCode install and explains **how to safely expose a host Mac’s Ollama instance to your local network (LAN)** so that **other physical machines** (laptops, desktops) can use it as a shared inference backend.

This is a **LAN-only design** (no public internet exposure).

---

## Overview

### Default Behavior

- Ollama binds to `127.0.0.1:11434` by default
- Only the host machine can access the model API
- OpenCode on other machines **cannot connect** without reconfiguration citeturn11search128

### Goal State

- Host Mac runs Ollama
- Ollama listens on the LAN interface (e.g. `192.168.1.x:11434`)
- Other machines run OpenCode configured with the host Mac’s IP

---

## Step 1 — Identify Your Host Mac’s LAN IP

On the Mac running Ollama:

```bash
ipconfig getifaddr en0
```

Example:

```
192.168.1.42
```

This IP will be used by all OpenCode clients.

---

## Step 2 — Configure Ollama to Listen on the LAN

### Why this is required

Ollama runs as a GUI app on macOS and **does not inherit shell environment variables**. Setting `OLLAMA_HOST` in `.zshrc` will NOT work citeturn11search129.

---

### Option A — Temporary (Session-only)

```bash
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
```

Then **quit and restart the Ollama app**.

This change resets on logout or reboot.

---

### Option B — Persistent (Recommended)

Create a launch agent:

```bash
mkdir -p ~/Library/LaunchAgents
nano ~/Library/LaunchAgents/com.ollama.server.plist
```

Paste the following **minimal, safe** configuration:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.ollama.server</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/ollama</string>
    <string>serve</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_HOST</key>
    <string>0.0.0.0:11434</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
```

Load it:

```bash
launchctl load -w ~/Library/LaunchAgents/com.ollama.server.plist
```

Restart Ollama.

```bash
brew services restart ollama
```

---

## Step 3 — Verify Network Binding

On the host Mac:

```bash
lsof -n -iTCP:11434 -sTCP:LISTEN
```

Expected output:

```
ollama  ... TCP *:11434 (LISTEN)
```

This confirms Ollama is listening on all interfaces citeturn11search128.

---

## Step 4 — macOS Firewall Check

Ensure the macOS firewall allows inbound connections to Ollama:

- System Settings → Network → Firewall
- Allow incoming connections for **Ollama**

If blocked, other machines will silently fail.

### Step 4a - No access to firewall settings

See other document [ollama_firewall_locked_access.md](ollama_firewall_locked_access.md)

---

## Step 5 — Configure OpenCode on Remote Machines

On each OpenCode client machine:

### Update OpenCode config

Open the opencode config for editing

```bash
nano .config/opencode/opencode.json
```

In your devcontainer OpenCode config (inside the container), point the Ollama provider to:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://192.168.100.132:11434/v1" },
      "models": {
        "qwen2.5-coder:32b-ctx16384": { "name": "Qwen 2.5 Coder 32B — ctx 16384 (default)", "tools": true },
        "qwen3-coder:30b-ctx16384": { "name": "Qwen 3 Coder 30B — ctx 16384", "tools": true },
        "codellama:34b-ctx16384": { "name": "CodeLlama 34B — ctx 16384" },
        "starcoder:15b-ctx32768": { "name": "StarCoder 15B — ctx 32768" },

        "deepseek-r1:32b-ctx16384": { "name": "DeepSeek-R1 32B — ctx 16384" },
        "qwen3:32b-ctx16384": { "name": "Qwen 3 32B — ctx 16384", "tools": true },

        "gemma3:27b-ctx8192": { "name": "Gemma 3 27B — ctx 8192" },
        "qwen2.5vl:32b-ctx8192": { "name": "Qwen 2.5 VL 32B — ctx 8192", "tools": true },

        "qwen3:8b-ctx32768": { "name": "Qwen 3 8B — ctx 32768" },
        "qwen3:14b-ctx32768": { "name": "Qwen 3 14B — ctx 32768" },
        "llama3:8b-ctx32768": { "name": "Llama 3 8B — ctx 32768 (fallback)" },
        "llama3.2:3b-ctx65536": { "name": "Llama 3.2 3B — ctx 65536" }
        // Add models you've pulled via `ollama pull <model>`
      }
    }
  }
}
```

(Substitute your host IP in the baseURL)

---

## Step 6 — Validate from a Remote Machine

From another computer:

```bash
curl http://192.168.1.42:11434/api/tags
```

Successful response returns installed model metadata citeturn11search131.

Then launch OpenCode:

```bash
opencode
```

Models should appear instantly (no downloads).

---

## Security Considerations (LAN Scope)

- Ollama has **no built-in authentication** citeturn11search133
- Anyone on the LAN can consume compute
- Do **not** expose port 11434 directly to the internet

### Recommended safeguards

- Trusted LAN only
- Router firewall blocks WAN → 11434
- One active inference session at a time for ≥30B models

For remote access beyond LAN, use VPN (Tailscale or WireGuard) instead of opening ports citeturn11search137.

---

## Architecture Summary

```
[OpenCode Clients] ──LAN──▶ [Mac Host]
                          └─ Ollama :11434
```

---

*Document written May 2026. Designed for OpenCode + LAN-only Ollama sharing on macOS.*
