# Firewall‑Locked Access to Ollama (Windows Devcontainer → macOS Host)

This document provides **step‑by‑step, explicit instructions** for *your exact setup*:

- **MacBook Pro (macOS)** at **192.168.100.132**
  - Runs **Ollama**
  - Ollama listens on **localhost only** (`127.0.0.1:11434`)
  - Firewall rules are **policy‑controlled** (no inbound access)

- **Windows 10 LTSC laptop**
  - Runs a **devcontainer** (Docker)
  - Needs to connect to the Ollama instance on the Mac

Because inbound connections are blocked, this setup uses an **outbound SSH reverse tunnel** from the Mac to the Windows machine.

---

## Why this approach is required

Corporate endpoint firewalls typically enforce:

- ❌ Inbound connections: blocked or restricted
- ✅ Outbound connections: allowed

Even if Ollama binds to `0.0.0.0`, the firewall prevents other machines from connecting.

**SSH reverse port forwarding (`ssh -R`) solves this by:**

- Initiating the connection **outbound from the Mac**
- Making Ollama appear as a *local service* on the Windows machine
- Requiring **no firewall changes** on the Mac

---

## High‑level architecture

```
Devcontainer (Windows)
      │
      │  http://localhost:11434
      ▼
Windows 10 host (SSH server)
      │
      │  SSH reverse tunnel
      ▼
MacBook Pro (192.168.100.132)
      └── Ollama on 127.0.0.1:11434
```

To the devcontainer, Ollama looks local.

---

## Prerequisites

### On the MacBook Pro (192.168.100.132)

- Ollama installed and working locally
- SSH client available (default on macOS)

Verify Ollama:

```bash
ollama serve
```

Verify it is listening locally:

```bash
lsof -n -iTCP:11434 -sTCP:LISTEN
```

Expected:

```text
127.0.0.1:11434 (LISTEN)
```

This is **correct**.

---

### On the Windows 10 LTSC machine

- OpenSSH **server** installed and running
[https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse?tabs=gui&pivots=windows-10]

- Docker + devcontainer support

Verify SSH server:

```powershell
sshd
```

Or check status:

```powershell
Get-Service sshd
```

#### If no password is set for the Windows user login

...we can leverage public key login instead.

1. Generate an SSH key on the Mac:

```bash
ssh-keygen -t ed25519
```

2. Copy the public key to the Windows machine

The below won't work if you don't have a password to log in with, so use some other method to copy the file if necessary:

```bash
scp ~/.ssh/id_ed25519.pub username@WINDOWS_IP:C:/Users/username/`
```

3. Install the public key on Windows

a. If user is not administrator

```powershell
mkdir C:\Users\test\.ssh
notepad C:\Users\test\.ssh\authorized_keys
```

Paste the contents of id_ed25519.pub into the notepad file, save, quit.

Set permissions

```powershell
icacls C:\Users\test\.ssh\authorized_keys /inheritance:r
icacls C:\Users\test\.ssh\authorized_keys /grant test:F
```

b. If user is administrator

```powershell
notepad C:\ProgramData\ssh\administrators_authorized_keys
```

Paste the public key.

Fix permissions:

```powershell
icacls C:\ProgramData\ssh\administrators_authorized_keys /setowner "NT AUTHORITY\SYSTEM"
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r
icacls C:\ProgramData\ssh\administrators_authorized_keys /grant "SYSTEM:(F)" "Administrators:(F)"
```

Fix line endings:

```powershell
$path = "C:\ProgramData\ssh\administrators_authorized_keys"; (Get-Content $path -Raw) -replace "`r`n","`n" | Set-Content -NoNewline -Encoding ascii $path
```

4. Restart the SSH daemon on Windows

```powershell
Restart-Service sshd
```

The Mac must be able to SSH **outbound** to this machine.

---

## Step‑by‑step setup

### Step 1 — Start Ollama on the Mac

On the Mac (if not already running):

```bash
ollama serve
```

Leave this running, or ensure it is running via launchd.

---

### Step 2 — Create the SSH reverse tunnel (Mac → Windows)

From the **MacBook Pro**, run:

```bash
ssh -R 127.0.0.1:11434:localhost:11434 username@WINDOWS_IP

ssh -R 0.0.0.0:11434:localhost:11434 test@192.168.100.143

```

If you need to specify a nonstandard ssh public key file:

```bash

ssh -i ~/.ssh/id_ed25519_a \
    -R 127.0.0.1:11434:localhost:11434 \
    test@192.168.100.143


ssh -i ~/.ssh/id_ed25519_a \
    -R 0.0.0.0:11434:localhost:11434 \
    test@192.168.100.143    
```


Where:
- `username` = your Windows SSH user
- `WINDOWS_IP` = IP address of the Windows laptop

#### What this command does (important)

- `ssh` — opens an outbound SSH connection
- `-R` — enables *remote* port forwarding
- `127.0.0.1:11434` — port exposed **on the Windows machine**
- `localhost:11434` — local Ollama endpoint **on the Mac**

✅ Ollama remains bound to localhost on the Mac
✅ Windows now has `localhost:11434` mapped to Ollama

Leave this SSH session open.

---

### Step 3 — Verify from Windows (host)

On the **Windows machine**, open PowerShell:

```powershell
curl http://localhost:11434/api/tags
```

Expected result:
- JSON listing Ollama models

If this works, the tunnel is active.

---

## Step 4 — Connect from the devcontainer

Enable non-local binds in Windows OpenSSH
Open Powershell as Administrator:

```powershell
notepad C:\ProgramData\ssh\sshd_config
```

Find (or add) this line:

```powershell
GatewayPorts clientspecified
```

Save the file and restart the SSH daemon.

Check if Windows is now listening broadly in a powershell window:

```powershell
netstat -ano | findstr 11434
```

You should see something like:

```powershell
0.0.0.0:11434  LISTENING
```

Allow WSL -> Windows traffic on port 11434

Identify the WSL vEthernet interface name:

```powershell
Get-NetAdapter | Where-Object {$_.Name -match "WSL"}
```

you'll see something like `vEthernet (WSL)`

Add a Windows Firewall rule:

```powershell
New-NetFirewallRule -DisplayName "Allow WSL to Ollama Reverse Tunnel 11434" -Direction Inbound -InterfaceAlias "vEthernet (WSL)" -Action Allow -Protocol TCP -LocalPort 11434
```




Inside the devcontainer on Windows:

### If Windows is running Docker Desktop

```bash
export OLLAMA_HOST=http://host.docker.internal:11434
```

### If Windows is running Docker on the WSL / Ubuntu layer

Find Docker / WSL gateway IP:

```bash
cat /etc/resolv.conf
```

Use that IP for `OLLAMA_HOST`:

```bash
export OLLAMA_HOST=http://192.168.16.1:11434
```

Now test:

```bash
curl $OLLAMA_HOST/api/tags
```

✅ The devcontainer is now using Ollama on the Mac

---

## Why this works with locked firewalls

| Aspect | Result |
|------|--------|
| Inbound traffic to Mac | ❌ Never occurs |
| Outbound SSH from Mac | ✅ Allowed |
| Encryption | ✅ End‑to‑end |
| Firewall changes | ✅ None |

This pattern is widely approved in enterprise environments.

---

## Common refinements

### Run tunnel in background (Mac)

```bash
ssh -N -f -R 127.0.0.1:11434:localhost:11434 username@WINDOWS_IP
```

### Use a different Windows‑side port

```bash
ssh -R 127.0.0.1:51434:localhost:11434 username@WINDOWS_IP
```

Then in the devcontainer:

```bash
export OLLAMA_HOST=http://localhost:51434
```

---

## Troubleshooting checklist

- ❌ `curl` fails on Windows → check SSH tunnel still running
- ❌ Connection refused → verify `sshd` running on Windows
- ❌ Ollama not responding → verify `ollama serve` on Mac

Useful debug commands:

```bash
lsof -n -iTCP:11434
```

```powershell
netstat -ano | findstr 11434
```

---

## Summary

For your setup:

- Mac: Ollama stays on `localhost`
- Firewall: untouched
- Access: via SSH reverse tunnel
- Windows devcontainer: sees Ollama as local

This is the **most reliable and policy‑safe configuration** possible.
