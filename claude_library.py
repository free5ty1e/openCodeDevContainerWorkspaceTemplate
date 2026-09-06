#!/usr/bin/env python3
"""
Claude Bridge Library - Shared functionality for all provider scripts.

This module contains common functions used across the Claude Code bridge scripts
for OpenCode Zen, Google, NVIDIA, and anyAPI. It provides:

- Prerequisite checking and installation (litellm, claude CLI)
- API key management
- Model fetching, categorizing, and selection
- Proxy management
- Model access testing
- Claude persistence and statusline setup

Usage:
    from claude_library import ensure_litellm, get_api_key, fetch_models, etc.
"""

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
import re

# ─── Configuration ─────────────────────────────────────────────────────────────

# Default paths
DEFAULT_VENV_PYTHON = "/workspace/.venv/bin/python3"


# ─── Utility Functions ───────────────────────────────────────────────────────

def get_python_executable():
    """Return the python executable to use for pip installs.
    Prefers the workspace virtualenv if it exists."""
    venv_python = DEFAULT_VENV_PYTHON
    if os.path.isfile(venv_python) and os.access(venv_python, os.X_OK):
        return venv_python
    return sys.executable


def run_command(cmd, label="", timeout=300):
    """Run a command, return (rc, output). Labels help with verbose logging."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = p.stdout + p.stderr
        if label:
            print(f"  [{label}] rc={p.returncode}, output={output[:200] if output else 'empty'}...")
        return p.returncode, output
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


def check_claude_cli_version():
    """Check if the claude CLI is on PATH and has a working native binary.
    Returns tuple of (is_ok, version_string)."""
    if shutil.which("claude") is None:
        return False, None
    rc, out = run_command(["claude", "--version"])
    if rc != 0 or not out:
        return False, None
    has_valid_version = bool(re.match(r".*\d+\.\d+", out.strip()))
    no_error_messages = "error" not in out.lower() and "not installed" not in out.lower()
    return has_valid_version and no_error_messages, out.strip() if has_valid_version and no_error_messages else None


def install_package(pkg_name, pip_name=None):
    """Check if a package is importable; install via pip if not.
    Returns True if package was available or successfully installed."""
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
            [get_python_executable(), "-m", "pip", "install", "-q", pip_name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        print(f"  ❌ Failed to install {pip_name}.")
        return False


# ─── Cache Management ──────────────────────────────────────────────────────

def read_cache(cache_file, default=None, as_int=False, as_set=False):
    """Read a value from a cache file."""
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip()
            if as_set:
                if val:
                    return set(val.split("\n"))
                return set()
            if as_int:
                if val.isdigit():
                    return int(val)
                return default
            return val if val else default
        except OSError:
            pass
    return default


def write_cache(cache_file, value):
    """Write a value to a cache file."""
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            if isinstance(value, set):
                f.write("\n".join(sorted(value)))
            else:
                f.write(str(value))
    except OSError:
        pass


# ─── API Key Management ─────────────────────────────────────────────────────

def get_and_cache_api_key(cache_file, env_var_name, clear=False):
    """Get an API key from environment or prompt user, then cache it."""
    # Handle clear flag
    if clear and os.path.exists(cache_file):
        try:
            os.remove(cache_file)
            print(f"🗑️  Cleared cached API key at {cache_file}.")
        except OSError:
            pass

    api_key = os.environ.get(env_var_name)
    if api_key:
        print(f"✅ {env_var_name} found in environment.")
        return api_key

    cached = read_cache(cache_file)
    if cached:
        print(f"✅ Found cached API key in {cache_file}.")
        os.environ[env_var_name] = cached
        return cached

    return None  # Caller should prompt


def cache_api_key(cache_file, api_key):
    """Cache an API key to a file."""
    if not api_key:
        return
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            f.write(api_key)
        print(f"💾 API key cached to {cache_file}.")
    except OSError as e:
        print(f"⚠️  Could not cache API key to {cache_file}: {e}")


# ─── Model Utilities ────────────────────────────────────────────────────────

def filter_chat_models(models, non_chat_keywords=None, free_keywords=None):
    """Filter models to only include chat models, separating free from standard."""
    if non_chat_keywords is None:
        non_chat_keywords = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"]
    if free_keywords is None:
        free_keywords = ["community", "instruct", "chat", "free"]

    standard_chat_models = []
    free_tier_chat_models = []

    for model_obj in models:
        model_id = model_obj.get("id", "")
        owned_by = model_obj.get("owned_by", "").lower()
        model_id_lower = model_id.lower()

        if any(keyword in model_id_lower for keyword in non_chat_keywords):
            continue

        # Check if this is a free model
        is_free = (
            "community" in owned_by
            or any(keyword in model_id_lower for keyword in free_keywords)
            or "free" in model_id_lower
        )
        if is_free:
            if not any(m.get("id") == model_id for m in free_tier_chat_models):
                free_tier_chat_models.append(model_obj)
        else:
            if not any(m.get("id") == model_id for m in standard_chat_models):
                standard_chat_models.append(model_obj)

    standard_chat_models.sort(key=lambda m: m.get("id", ""))
    free_tier_chat_models.sort(key=lambda m: m.get("id", ""))

    return standard_chat_models, free_tier_chat_models, standard_chat_models + free_tier_chat_models


def format_token_count(tokens):
    """Format token count for display (e.g., 15000 -> 15k)."""
    if not tokens:
        return ""
    try:
        tokens = int(tokens)
    except (ValueError, TypeError):
        return str(tokens)
    if tokens >= 10000:
        return f"{tokens // 1000}k"
    elif tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


# ─── HTTP Utilities ───────────────────────────────────────────────────────

def http_get_json(url, api_key=None, use_query_param=False, timeout=30):
    """GET JSON from an API endpoint."""
    headers = {"Accept": "application/json", "User-Agent": "claude-bridge/1.0"}

    if api_key:
        if use_query_param:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}key={api_key}"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ─── Proxy Management ─────────────────────────────────────────────────────

def get_litellm_binary():
    """Find the litellm binary, preferring venv, then system install."""
    # Check venv first
    venv_litellm = "/workspace/.venv/bin/litellm"
    if os.path.isfile(venv_litellm) and os.access(venv_litellm, os.X_OK):
        return venv_litellm

    # Fall back to shutil.which
    bin_path = shutil.which("litellm")
    if bin_path:
        return bin_path

    return None


def find_claude_pkg_dir(claude_prefix="@anthropic-ai/claude-code"):
    """Find the installed claude CLI package directory."""
    candidates = [
        "/usr/lib/node_modules",
        "/usr/local/lib/node_modules",
        os.path.expanduser("~/.nvm/versions/node"),
        os.path.expanduser("~/.npm-global/lib"),
    ]

    for base in candidates:
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                if claude_prefix.split("/")[1] in dirs or f"{claude_prefix}" in files:
                    return os.path.join(root, claude_prefix)

    # Try npm root
    rc, out = run_command(["npm", "root", "-g"])
    if rc == 0:
        return os.path.join(out.strip(), claude_prefix)

    return None


def start_litellm_proxy(config_file, port, master_key, log_file, pid_file):
    """Start the litellm proxy in the background."""
    # Clean up any existing proxy
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass

    os.makedirs(os.path.dirname(config_file), exist_ok=True)

    litellm_bin = get_litellm_binary()

    # If not found, try to install
    if litellm_bin is None:
        print("   📦 Installing litellm[proxy] system-wide...")
        subprocess.run(
            ["pip", "install", "--break-system-packages", "-q", "litellm[proxy]"],
            capture_output=True, timeout=180,
        )
        litellm_bin = get_litellm_binary()

    if litellm_bin is None:
        print("   ❌ Could not find or install litellm CLI binary")
        return None

    with open(log_file, "w") as logf:
        proc = subprocess.Popen(
            [litellm_bin, "--config", config_file, "--port", str(port)],
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    with open(pid_file, "w") as f:
        f.write(str(proc.pid))

    print(f"🚀 Starting litellm proxy on port {port} (PID {proc.pid})...")
    return proc


def test_proxy_health(port, master_key, timeout=2):
    """Test if the proxy is healthy and responding."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/health",
            headers={"Authorization": f"Bearer {master_key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ─── Claude CLI Management ────────────────────────────────────────────────

def get_claude_version():
    """Get the installed claude CLI version, or None if not installed."""
    if shutil.which("claude") is None:
        return None
    rc, out = run_command(["claude", "--version"])
    if rc != 0 or not out:
        return None
    if "error" in out.lower() or "not installed" in out.lower():
        return None
    return out.strip()


def install_claude_cli(args=None):
    """Install claude CLI via npm, handling devcontainer quirks."""
    print("   Current claude CLI version: not installed")

    # Check node availability
    print("   🔍 Checking node.js availability...")
    node_result = subprocess.run(["node", "-v"], capture_output=True, text=True, timeout=10)
    if node_result.returncode != 0:
        print("   ❌ Node.js not found. Cannot install claude CLI.")
        return False
    print(f"   ✅ Node.js available: {node_result.stdout.strip()}")

    if check_claude_cli_version()[0]:
        print("  ✅ claude CLI found (native binary OK).")
        return True

    print("   📦 Installing claude CLI via npm (@anthropic-ai/claude-code)...")

    npm_cmd = ["npm", "install", "-g", "--allow-scripts=@anthropic-ai/claude-code", "@anthropic-ai/claude-code", "--legacy-peer-deps", "--no-audit"]
    rc, out = run_command(npm_cmd, label="npm-install")
    print(f"   npm install rc={rc}")

    # Handle ENOTEMPTY/EPIPE
    if rc != 0 and ("ENOTEMPTY" in out or "EPIPE" in out or "npm error syscall rename" in out):
        print("   ENOTEMPTY/EPIPE detected - cleaning target directory...")
        run_command(["npm", "bin", "cache", "clean", "--force"])
        for cand in [
            "/home/vscode/.npm-global/lib/node_modules/@anthropic-ai/claude-code",
            "/usr/lib/node_modules/@anthropic-ai/claude-code",
            "/usr/local/lib/node_modules/@anthropic-ai/claude-code",
        ]:
            if os.path.isdir(cand):
                print(f"   Removing partial install at {cand}")
                shutil.rmtree(cand, ignore_errors=True)
        rc, out = run_command(npm_cmd, label="npm-install-retry")

    # Handle permission errors
    if rc != 0 and ("permission" in out.lower() or "EACCES" in out):
        print("   Install failed with permission error, retrying with sudo...")
        rc, out = run_command(["sudo"] + npm_cmd, label="npm-install-sudo")

    # Try to fix binary if needed
    pkg_dir = find_claude_pkg_dir()
    if pkg_dir:
        install_cjs = os.path.join(pkg_dir, "install.cjs")
        if os.path.exists(install_cjs):
            print("  🛠️  Fixing claude native binary...")
            for exe in ["bin/claude.exe", "bin/claude"]:
                stale = os.path.join(pkg_dir, exe)
                if os.path.isfile(stale):
                    try:
                        os.remove(stale)
                    except PermissionError:
                        run_command(["sudo", "rm", "-f", stale])
            run_command(["node", install_cjs], label="node-install")

    if check_claude_cli_version()[0]:
        print("  ✅ claude CLI installed and working.")
        return True

    print("  ⚠️  claude CLI installed but the native binary is not working.")
    return False


def ensure_claude_cli(args=None):
    """Ensure claude CLI is available, installing if needed.

    This function:
    1. Checks if claude CLI is already installed and working
    2. If installed, checks for available upgrade and prompts (unless --accept-all-defaults)
    3. If not installed, auto-installs without prompting
    4. Returns True if claude CLI is available, False otherwise
    """
    # First check if claude is already installed and working
    is_ok, version = check_claude_cli_version()

    if is_ok:
        print(f"   Current claude CLI version: {version}")

        # If --accept-all-defaults, skip upgrade check
        if args and args.accept_all_defaults:
            return True

        # Check for available upgrade
        latest_version = ""
        try:
            rc3, out3 = run_command(["npm", "view", "@anthropic-ai/claude-code", "version"])
            if rc3 == 0 and out3.strip():
                latest_version = out3.strip()
        except Exception:
            pass

        if latest_version and latest_version != version:
            print(f"   Latest claude CLI version: {latest_version}")
            try:
                resp = input(f"   Upgrade from {version} to {latest_version}? (y/N): ").strip().lower()
            except EOFError:
                resp = "n"

            if resp == "y":
                print("   Upgrading claude CLI via npm...")
                npm_cmd = ["npm", "install", "-g", "--legacy-peer-deps", "@anthropic-ai/claude-code"]
                rc, out = run_command(npm_cmd, label="cli-upgrade")

                # Handle ENOTEMPTY/EPIPE in devcontainers
                if rc != 0 and ("ENOTEMPTY" in out or "EPIPE" in out):
                    print("   Upgrade encountered issues - attempting cleanup...")
                    run_command(["npm", "bin", "cache", "clean", "--force"])
                    rc, out = run_command(npm_cmd, label="cli-upgrade-retry")

                # Re-verify version after upgrade
                rc2, out2 = run_command(["claude", "--version"], label="cli-version-after")
                if rc2 == 0:
                    new_version = out2.strip()
                    print(f"   New claude CLI version: {new_version}")
        return True

    # Claude not installed - auto-install it (no prompt needed)
    return install_claude_cli(args)


def ensure_prerequisites(args=None):
    """Ensure litellm (with the proxy extras) and the claude CLI are available."""
    print("🔍 Checking prerequisites...")
    ok = True
    ok &= install_package("litellm", "litellm[proxy]")
    ok &= ensure_claude_cli(args)
    return ok
# End of shared functions