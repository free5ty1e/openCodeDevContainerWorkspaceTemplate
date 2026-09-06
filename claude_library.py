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
    claude_path = shutil.which("claude")
    if claude_path is None:
        return False, None
    try:
        rc, out = run_command(["claude", "--version"])
    except OSError as e:
        # Exec format error or other OS-level error
        print(f"   ⚠️  claude binary exists but failed to execute: {e}")
        return False, None
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
        result = subprocess.run(
            [get_python_executable(), "-m", "pip", "install", pip_name],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  ✅ Successfully installed {pip_name}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Failed to install {pip_name}")
        print(f"     Error: {e.stderr.strip() if e.stderr else 'Unknown error'}")
        print(f"     Command: {e.cmd}")
        print(f"     Return code: {e.returncode}")
        return False
    except Exception as e:
        print(f"  ❌ Unexpected error installing {pip_name}: {e}")
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


def categorize_models(models, non_chat_keywords=None, free_keywords=None):
    """Wrapper to categorize models using filter_chat_models with provider-specific keywords.

    This is a convenience function that calls filter_chat_models with the provided keywords.
    Providers can pass their own non_chat_keywords and free_keywords for customization.
    """
    return filter_chat_models(models, non_chat_keywords, free_keywords)


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
    """Start the litellm proxy in the background and wait until ready."""
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

    # Wait for proxy to be ready with retries
    for i in range(60):  # Wait up to 60 seconds
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": f"Bearer {master_key}"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    print("   ✅ Proxy is ready.")
                    return proc
        except Exception:
            time.sleep(1)

    print("   ⚠️  Proxy did not become ready. Check the log below:")
    try:
        with open(log_file) as f:
            print(f.read()[-3000:])
    except OSError:
        pass
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


def _get_latest_npm_version(package_name):
    """Get the latest version of an npm package."""
    try:
        rc, out = run_command(["npm", "view", package_name, "version"])
        if rc == 0 and out.strip():
            return out.strip()
    except Exception:
        pass
    return ""


def _check_and_prompt_upgrade(package_name, display_name, current_version, args):
    """Check for available upgrade and prompt user if available.
    Returns True if upgrade was performed or not needed, False if failed."""
    # If --accept-all-defaults, skip upgrade check
    if args and args.accept_all_defaults:
        return True

    latest_version = _get_latest_npm_version(package_name)
    if not latest_version:
        print(f"   Latest {display_name} version: (unable to check)")
        return True

    # Extract just the version number (e.g., "2.1.261" from "2.1.261 (Claude Code)")
    current_version = current_version.split()[0] if current_version else current_version

    if latest_version and latest_version != current_version:
        print(f"   Latest {display_name} version: {latest_version}")
        try:
            resp = input(f"   Upgrade {display_name} from {current_version} to {latest_version}? (y/N): ").strip().lower()
        except EOFError:
            resp = "n"

        if resp == "y":
            print(f"   Upgrading {display_name} via npm...")
            npm_cmd = ["npm", "install", "-g", "--legacy-peer-deps", package_name]
            rc, out = run_command(npm_cmd, label=f"{package_name}-upgrade")

            # Handle ENOTEMPTY/EPIPE in devcontainers
            if rc != 0 and ("ENOTEMPTY" in out or "EPIPE" in out):
                print("   Upgrade encountered issues - attempting cleanup...")
                run_command(["npm", "bin", "cache", "clean", "--force"])
                rc, out = run_command(npm_cmd, label=f"{package_name}-upgrade-retry")

            # Handle permission errors
            if rc != 0 and ("permission" in out.lower() or "EACCES" in out):
                print("   Upgrade failed with permission error, retrying with sudo...")
                rc, out = run_command(["sudo"] + npm_cmd, label=f"{package_name}-upgrade-sudo")

            # Re-verify version after upgrade
            if "claude" in package_name:
                rc2, out2 = run_command(["claude", "--version"], label="version-after")
            else:
                # For other packages, try common command names
                bin_name = package_name.split("/")[-1].replace("-", "_")
                rc2, out2 = run_command([bin_name, "--version"], label="version-after")
            if rc2 == 0:
                new_version = out2.strip()
                print(f"   New {display_name} version: {new_version}")
            else:
                print(f"   ⚠️  Upgrade may have failed - version check returned non-zero")
        return True
    return True


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

        # Check for available upgrade and prompt if needed
        return _check_and_prompt_upgrade("@anthropic-ai/claude-code", "claude CLI", version, args)

    # Claude not installed - auto-install it (no prompt needed)
    return install_claude_cli(args)


def ensure_litellm(args=None):
    """Ensure litellm is installed and check for upgrades."""
    print("   Checking litellm...")
    # First check if litellm is available
    if install_package("litellm", "litellm[proxy]"):
        # Check for available upgrade
        return _check_and_prompt_upgrade("litellm[proxy]", "litellm", "installed", args)
    return False


def ensure_prompt_toolkit(args=None):
    """Ensure prompt_toolkit is installed."""
    print("   Checking prompt_toolkit...")
    if install_package("prompt_toolkit", "prompt_toolkit"):
        return _check_and_prompt_upgrade("prompt_toolkit", "prompt_toolkit", "installed", args)
    return False


def ensure_prerequisites(args=None):
    """Ensure litellm (with the proxy extras), prompt_toolkit, and the claude CLI are available."""
    print("🔍 Checking prerequisites...")
    ok = True
    ok &= ensure_litellm(args)
    ok &= ensure_prompt_toolkit(args)
    ok &= ensure_claude_cli(args)
    return ok


# ─── Cache Management (Extended) ────────────────────────────────────────────

def load_model_context(model_id):
    """Load the last context window for a specific model from cache."""
    cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
    cache_file = os.path.join(cache_dir, f"{model_id}.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip()
                return int(val) if val.isdigit() else None
        except (OSError, ValueError):
            pass
    return None


def save_model_context(model_id, context_window):
    """Save the context window for a specific model to cache."""
    try:
        cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{model_id}.txt")
        with open(cache_file, "w") as f:
            f.write(str(context_window))
    except OSError:
        pass


def load_model_compaction(model_id):
    """Load the last auto-compaction threshold (%) for a specific model."""
    cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
    cache_file = os.path.join(cache_dir, f"{model_id}.compaction.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip()
                return int(val) if val.isdigit() else None
        except (OSError, ValueError):
            pass
    return None


def save_model_compaction(model_id, threshold):
    """Save the auto-compaction threshold (%) for a specific model."""
    try:
        cache_dir = os.path.expanduser("~/.claude_opencode_context_windows")
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f"{model_id}.compaction.txt")
        with open(cache_file, "w") as f:
            f.write(str(threshold))
    except OSError:
        pass


def load_statusline_mode(cache_file=None):
    """Load the last used statusline mode ('full' or 'compact') from cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_statusline_mode")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip().lower()
                if val in ("full", "compact"):
                    return val
        except OSError:
            pass
    return None


def save_statusline_mode(mode, cache_file=None):
    """Save the last used statusline mode ('full' or 'compact') to cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_statusline_mode")
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            f.write(mode)
    except OSError:
        pass


# ─── Favorites Management ───────────────────────────────────────────────────

def load_favorites(cache_file=None):
    """Load the set of favorite model IDs from cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_favorites")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                content = f.read().strip()
                if content:
                    return set(content.split("\n"))
        except OSError:
            pass
    return set()


def save_favorites(favorites_set, cache_file=None):
    """Save the set of favorite model IDs to cache."""
    if cache_file is None:
        cache_file = os.path.expanduser("~/.claude_opencode_favorites")
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as f:
            f.write("\n".join(sorted(favorites_set)))
    except OSError:
        pass


# ─── Terminal Utilities ─────────────────────────────────────────────────────

def get_terminal_height():
    """Return usable terminal height, clamped to a sane minimum."""
    try:
        import shutil
        rows = shutil.get_terminal_size().lines
        if rows and rows > 4:
            return rows
    except Exception:
        pass
    return 24


# ─── Proxy Utilities ────────────────────────────────────────────────────────

def port_open(port):
    """Check if a port is open."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_running_proxy(pid_file=None, timeout=5):
    """Stop any previously-started proxy for this bridge."""
    if pid_file is None:
        pid_file = os.path.expanduser("~/.claude_opencode_proxy.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(timeout)
        except (OSError, ValueError):
            pass
        try:
            os.remove(pid_file)
        except OSError:
            pass


def test_proxy_connection(port, master_key, selected_model):
    """Quick validation via the proxy to verify the model responds."""
    print(f"\n🧪 Testing selected model through proxy ({selected_model})...")
    payload = {
        "model": selected_model,
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "Hello! Please respond with a simple greeting."}],
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {master_key}",
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            content = data.get("content", [])
            text = "".join(
                b.get("text", "")
                for b in content
                if b.get("type") == "text"
            )
            if not text.strip():
                text = "".join(
                    b.get("thinking", "")
                    for b in content
                    if b.get("type") == "thinking"
                )
            print(f"✅ Proxy test successful!")
            print(f"   Response: {text.strip()[:200]}")
            print(f"   Usage: {data.get('usage', {})}")
            return True
    except urllib.error.HTTPError as e:
        print(f"⚠️  Proxy test returned status {e.code}")
        print(f"   Error: {e.read().decode()[:500]}")
        return False
    except Exception as e:
        print(f"⚠️  Proxy test failed: {type(e).__name__}: {str(e)[:200]}")
        return False


# ─── Claude Code Persistence ─────────────────────────────────────────────

def setup_claude_persistence():
    """Set up Claude Code persistence using .claude_persist in workspace."""
    import shutil

    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.abspath(script_dir)
    claude_persist_dir = os.path.join(workspace_root, ".claude_persist")
    claude_config_dir = os.path.expanduser("~/.claude")

    os.makedirs(claude_persist_dir, exist_ok=True)

    if os.path.islink(claude_config_dir):
        try:
            current_target = os.readlink(claude_config_dir)
        except OSError:
            current_target = ""
        if current_target == claude_persist_dir:
            print(f"✅ ~/.claude already symlinked to {claude_persist_dir}")
            return

    elif os.path.isdir(claude_config_dir):
        if not os.listdir(claude_config_dir):
            shutil.rmtree(claude_config_dir)
            os.symlink(claude_persist_dir, claude_config_dir)
            print(f"  ✅ Symlinked empty ~/.claude → {claude_persist_dir}")
        else:
            print(f"  Copying existing ~/.claude to {claude_persist_dir}")
            if os.path.exists(claude_persist_dir):
                shutil.rmtree(claude_persist_dir)
            shutil.copytree(claude_config_dir, claude_persist_dir, dirs_exist_ok=True)
            shutil.rmtree(claude_config_dir)
            os.symlink(claude_persist_dir, claude_config_dir)
            print(f"  ✅ Migrated ~/.claude → {claude_persist_dir}")
    else:
        os.symlink(claude_persist_dir, claude_config_dir)
        print(f"  ✅ Created ~/.claude → {claude_persist_dir} (populated on first launch)")


# ─── Statusline Setup ─────────────────────────────────────────────────────

def setup_statusline_symlink(workspace_file="claude_statusline.sh", provider_indicator="opencode"):
    """Set up statusline file symlink in the Claude config directory."""
    claude_config_dir = os.path.expanduser("~/.claude")
    workspace_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_statusline = os.path.join(workspace_dir, workspace_file)

    if not os.path.exists(workspace_statusline):
        print(f"⚠️  workspace statusline not found at {workspace_statusline}")
        return None

    statusline_dst = os.path.join(claude_config_dir, workspace_file)

    try:
        os.makedirs(os.path.dirname(statusline_dst), exist_ok=True)
        os.chmod(workspace_statusline, 0o755)
        if os.path.islink(statusline_dst) or os.path.exists(statusline_dst):
            os.unlink(statusline_dst)
        os.symlink(workspace_statusline, statusline_dst)
        print(f"📐 Symlinked workspace statusline → {statusline_dst}")

        settings_file = os.path.join(claude_config_dir, "settings.json")
        resolved_statusline = os.path.realpath(statusline_dst)
        # Don't hardcode CLAUDE_CODE_STATUSLINE_MODE - let the env var from launch_claude_with_model pass through
        statusline_command = f"bash {resolved_statusline}"

        if os.path.exists(settings_file):
            with open(settings_file, "r") as f:
                settings = json.load(f)
        else:
            settings = {}

        settings["statusLine"] = {
            "type": "command",
            "command": statusline_command
        }

        with open(settings_file, "w") as f:
            json.dump(settings, f, indent=2)

        print(f"⚙️  Configured statusLine in {settings_file}")
        return True
    except OSError as e:
        print(f"⚠️  Could not create symlink: {e}")
        return None
# End of claude_library.py