#!/usr/bin/env python3
import argparse
import os
import sys
import subprocess
import json
import shutil
import time
import socket
import signal
import urllib.request
import urllib.error

# ─── Configuration ───────────────────────────────────────────────────────
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
CACHE_FILE = os.path.expanduser("~/.nvidia_api_key_cache")
PROXY_PORT = 4499
PROXY_MASTER_KEY = "sk-claude-bridge"
CONFIG_DIR = os.path.expanduser("~/.claude_nvidia")
CONFIG_FILE = os.path.join(CONFIG_DIR, "litellm_proxy.yaml")
LOG_FILE = os.path.join(CONFIG_DIR, "proxy.log")
PID_FILE = os.path.join(CONFIG_DIR, "proxy.pid")

# ─── Step 1: Check & install prerequisites ──────────────────────────────
def _get_python():
    """Return the python executable to use for pip installs.
    Prefers the workspace virtualenv if it exists."""
    venv_python = "/workspace/.venv/bin/python3"
    if os.path.isfile(venv_python) and os.access(venv_python, os.X_OK):
        return venv_python
    return sys.executable


def install_if_missing(pkg_name, pip_name=None):
    """Check if a package is importable; install via pip if not."""
    pip_name = pip_name or pkg_name
    try:
        __import__(pkg_name)
        print(f"  ✅ {pkg_name} already available")
        return True
    except ImportError:
        pass
    print(f"  📦 Installing {pip_name}...")
    try:
        subprocess.run(
            [_get_python(), "-m", "pip", "install", "-q", pip_name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        print(f"  ❌ Failed to install {pip_name}.")
        return False


def _run(cmd, label=""):
    """Run a command, return (rc, output).  Labels help with verbose logging."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = p.stdout + p.stderr
        if label:
            print(f"  [{label}] rc={p.returncode}, output={output[:200] if output else 'empty'}...")
        return p.returncode, output
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def _claude_version_ok():
    """Return True if the claude CLI is on PATH and has a working native binary."""
    if shutil.which("claude") is None:
        return False
    rc, out = _run(["claude", "--version"])
    # Accept if version output is non-empty and doesn't contain error messages
    if rc != 0 or not out:
        return False
    # Check that it looks like a valid version (has digits and dots)
    import re
    has_valid_version = bool(re.match(r".*\d+\.\d+", out.strip()))
    no_error_messages = "error" not in out.lower() and "not installed" not in out.lower()
    return has_valid_version and no_error_messages


def ensure_claude_cli():
    """Install the claude CLI via npm if not already present, fixing native binary."""
    # First check node availability with verbose logging
    print("  🔍 Checking node.js availability...")
    node_result = subprocess.run(["node", "-v"], capture_output=True, text=True, timeout=10)
    if node_result.returncode != 0:
        print("  ❌ Node.js not found. Cannot install claude CLI.")
        return False
    print(f"  ✅ Node.js available: {node_result.stdout.strip()}")

    if _claude_version_ok():
        print("  ✅ claude CLI found (native binary OK).")
        return True

    print("  📦 Installing claude CLI via npm (@anthropic-ai/claude-code)...")
    # npm global installs often need root inside a devcontainer.
    # Use --legacy-peer-deps to avoid peer dependency issues, and --no-audit
    # to speed up install. rc=217 (EPIPE) can happen in devcontainers and is
    # usually non-fatal - retry once.
    npm_cmd = ["npm", "install", "-g", "--allow-scripts=@anthropic-ai/claude-code", "@anthropic-ai/claude-code", "--legacy-peer-deps", "--no-audit"]
    rc, out = _run(npm_cmd, label="npm-install")
    print(f"   npm install rc={rc}")

# Retry once if rc=217 (EPIPE/ENOTEMPTY) or other non-permission errors (but not if permission-related)
    if rc != 0 and "permission" not in out.lower():
        print("   Retrying npm install (cleaning global dir first)...\n")        # Clean the npm global dir to fix ENOTEMPTY
        try:
            target_dir = os.path.join(os.path.expanduser("~"), ".npm-global", "lib", "node_modules", "@anthropic-ai", "claude-code")
            if os.path.isdir(target_dir):
                _run(["rm", "-rf", target_dir], label="clean-node-modules")
        except:
            pass
        # Also clean npm cache
        _run(["npm", "cache", "clean", "--force"], label="npm-cache-clean")
        # Retry the install
        rc, out = _run(npm_cmd, label="npm-install-retry")
