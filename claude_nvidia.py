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
    # to speed up install. rc=217 (EPIPE/ENOTEMPTY) can happen in devcontainers and is
    # usually non-fatal - retry with cleanup.
    npm_cmd = ["npm", "install", "-g", "--allow-scripts=@anthropic-ai/claude-code", "@anthropic-ai/claude-code", "--legacy-peer-deps", "--no-audit"]
    rc, out = _run(npm_cmd, label="npm-install")
    print(f"   npm install rc={rc}")

    # Handle ENOTEMPTY/EPIPE: clean the target dir and retry
    if rc != 0 and ("ENOTEMPTY" in out or "EPIPE" in out or "npm error syscall rename" in out):
        print("   ENOTEMPTY/EPIPE detected - cleaning target directory and retrying...")
        # Find the target dir and clean it
        cleanup_cmd = ["npm", "bin", "cache", "clean", "--force"]
        _run(cleanup_cmd, label="npm-cache-clean")
        # Also remove any partial install of the package
        for cand in [
            "/home/vscode/.npm-global/lib/node_modules/@anthropic-ai/claude-code",
            "/usr/lib/node_modules/@anthropic-ai/claude-code",
            "/usr/local/lib/node_modules/@anthropic-ai/claude-code",
        ]:
            if os.path.isdir(cand):
                print(f"   Removing partial install at {cand}")
                shutil.rmtree(cand, ignore_errors=True)
        # Retry the install
        rc, out = _run(npm_cmd, label="npm-install-retry")

    # Check if install failed for permission reasons - use sudo if needed
    if rc != 0 and ("permission" in out.lower() or "EACCES" in out):
        print("   Install failed with permission error, retrying with sudo...")
        rc, out = _run(["sudo"] + npm_cmd, label="npm-install-sudo")
    # Also handle ENOTEMPTY after the retry if we still have issues
    if rc != 0 and "ENOTEMPTY" in out:
        print("   ENOTEMPTY still present after retry, attempting force cleanup...")
        # Remove the global node_modules dir entirely
        global_node_dir = os.path.expanduser("~/.npm-global/lib/node_modules")
        if os.path.isdir(global_node_dir):
            shutil.rmtree(global_node_dir, ignore_errors=True)
        rc, out = _run(["sudo"] + npm_cmd if os.path.exists("/usr/bin/sudo") else npm_cmd, label="npm-install-sudo-force")

    # Find the installed package dir to fix the native binary if postinit was skipped.
    pkg_dir = None
    for cand in [
        "/usr/lib/node_modules/@anthropic-ai/claude-code",
        "/usr/local/lib/node_modules/@anthropic-ai/claude-code",
        os.path.expanduser("~/.nvm/versions/node/*/lib/node_modules/@anthropic-ai/claude-code"),
    ]:
        if os.path.isdir(cand):
            pkg_dir = cand
            break
    if pkg_dir is None:
        rc2, out2 = _run(["npm", "root", "-g"], label="npm-root")
        pkg_dir = os.path.join(out2.strip(), "@anthropic-ai", "claude-code")

    install_cjs = os.path.join(pkg_dir, "install.cjs")
    if os.path.exists(install_cjs) and not _claude_version_ok():
        print("  🛠️  Fixing claude native binary...")
        # Remove stale placeholder that blocks the native download.
        for exe in ["bin/claude.exe", "bin/claude"]:
            stale = os.path.join(pkg_dir, exe)
            if os.path.isfile(stale):
                try:
                    os.remove(stale)
                except PermissionError:
                    _run(["sudo", "rm", "-f", stale], label="rm-stale")
        rc3, out3 = _run(["node", install_cjs], label="node-install")
        print(f"   node install rc={rc3}")
        # After install.cjs, verify and potentially re-run
        if not _claude_version_ok():
            # Try one more time with sudo
            _run(["sudo", "node", install_cjs], label="node-install-sudo")

    if _claude_version_ok():
        print("  ✅ claude CLI installed and working.")
        return True

    print("  ⚠️  claude CLI installed but the native binary is not working.")
    print("      Fix manually: node <install_dir>/install.cjs")
    return False


def ensure_prerequisites():
    """Ensure litellm (with the proxy extras) and the claude CLI are available."""
    print("🔍 Checking prerequisites...")
    ok = True
    ok &= install_if_missing("litellm", "litellm[proxy]")
    ok &= ensure_claude_cli()
    return ok


# ─── Step 2: Prompt for API key if not cached ───────────────────────────
def get_api_key():
    """Return a valid NVIDIA API key, prompting if needed and caching it."""
    NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")

    if NVIDIA_API_KEY:
        print("✅ NVIDIA API key found in environment.")
        return NVIDIA_API_KEY

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cached = f.read().strip()
            if cached:
                print(f"✅ Found cached API key in {CACHE_FILE}.")
                os.environ["NVIDIA_API_KEY"] = cached
                return cached
        except OSError:
            print(f"⚠️  Could not read cached API key from {CACHE_FILE}.")

    print("🔑 NVIDIA API key not found.")
    try:
        api_key = input("   Please enter your NVIDIA API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n❌ No API key provided. Exiting.")
        sys.exit(1)

    if not api_key:
        print("❌ No API key provided. Exiting.")
        sys.exit(1)

    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            f.write(api_key)
        print(f"💾 API key cached to {CACHE_FILE}.")
    except OSError:
        print(f"⚠️  Could not cache API key to {CACHE_FILE}.")

    os.environ["NVIDIA_API_KEY"] = api_key
    return api_key


# ─── Step 3: Fetch models from NVIDIA ────────────────────────────────────
def http_get_json(url, api_key):
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_models(api_key):
    """Fetch the list of available chat models from NVIDIA API."""
    try:
        data = http_get_json(NVIDIA_API_URL, api_key)
        return data.get("data", [])
    except Exception as e:
        print(f"❌ Failed to retrieve models: {e}")
        sys.exit(1)


# ─── Step 4: Categorize, filter, and sort models ────────────────────────
def categorize_models(all_raw_models):
    """Categorize models into standard and free/tier, sorted with free at bottom."""
    NON_CHAT_KEYWORDS = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"]
    FREE_KEYWORDS = ["community", "instruct", "chat", "deepseek", "kimi", "glm", "llama", "gemma", "nemotron"]

    standard_chat_models = []
    free_tier_chat_models = []

    for model_obj in all_raw_models:
        model_id = model_obj.get("id", "")
        owned_by = model_obj.get("owned_by", "").lower()
        model_id_lower = model_id.lower()

        if any(keyword in model_id_lower for keyword in NON_CHAT_KEYWORDS):
            continue

        is_free = (
            "community" in owned_by
            or any(keyword in model_id_lower for keyword in ["deepseek", "kimi", "glm"])
        )
        if is_free:
            if model_id not in free_tier_chat_models:
                free_tier_chat_models.append(model_id)
        else:
            if model_id not in standard_chat_models:
                standard_chat_models.append(model_id)

    standard_chat_models.sort()
    free_tier_chat_models.sort()
    combined_models_list = standard_chat_models + free_tier_chat_models
    return standard_chat_models, free_tier_chat_models, combined_models_list


# ─── Step 5: Display model list and handle selection ─────────────────────
def get_selection_input(prompt, max_val):
    """Get user selection input, handling EOF gracefully."""
    while True:
        try:
            selection = input(prompt).strip()
            if not selection:
                continue
            selected_idx = int(selection) - 1
            if selected_idx < 0 or selected_idx >= max_val:
                print(f"❌ Invalid selection. Please enter a number between 1 and {max_val}.")
                continue
            return selected_idx
        except (ValueError, IndexError):
            print(f"❌ Invalid input. Please enter a number between 1 and {max_val}.")
        except EOFError:
            print(f"\n❌ No input received. Exiting.")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n👋 Cancelled by user.")
            sys.exit(0)


def display_and_select(standard, free, combined):
    """Display the model list with numbers and get user selection."""
    print("\n========================================")
    print("       AVAILABLE NVIDIA CHAT MODELS     ")
    print("========================================")

    current_number = 1
    if standard:
        print("\n--- Standard & Enterprise Chat Models ---")
        for model_id in standard:
            print(f"[{current_number}] {model_id}")
            current_number += 1
    if free:
        print("\n--- Free & Community Tier Chat Models ---")
        for model_id in free:
            print(f"[{current_number}] {model_id} (Free Tier)")
            current_number += 1

    print("========================================")
    print(f"\nTotal models: {len(combined)}")
    print(f"\nSelect a model number [1-{len(combined)}]:")
    selected_idx = get_selection_input("> ", len(combined))
    selected_model = combined[selected_idx]
    print(f"\n🚀 Selected Model: {selected_model}")
    return selected_model


def check_model_access(selected_model, api_key):
    """Verify the selected model is usable with the given API key.

    Some NVIDIA models are not accessible to every account (they return
    404 'Function not found for account'). This catches that early so the
    user can pick a different model instead of failing inside Claude Code.
    Returns True if the model responds, False otherwise.
    """
    print(f"   🔍 Checking access to {selected_model}...")
    payload = {
        "model": selected_model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
        "temperature": 0,
    }
    req = urllib.request.Request(
        NVIDIA_CHAT_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status == 200:
                print(f"   ✅ Model accessible and responding.")
                return True
            print(f"   ⚠️  Model returned status {resp.status}. May still work.")
            return False
    except urllib.error.HTTPError as e:
        print(f"   ❌ Model NOT accessible (status {e.code}).")
        print(f"      {e.read().decode()[:300]}")
        return False
    except Exception as e:
        print(f"   ⚠️  Access check error: {type(e).__name__}: {str(e)[:120]}")
        return False


# ─── Step 6: Generate litellm proxy config ───────────────────────────────
def generate_litellm_config(selected_model, api_key):
    """Write a litellm PROXY config mapping the model to NVIDIA's OpenAI API.

    Claude Code speaks the Anthropic Messages API (/v1/messages), but NVIDIA
    only exposes an OpenAI-compatible API (/v1/chat/completions). litellm's
    proxy translates between the two, so Claude Code's conversation and tool
    calls work against the NVIDIA model.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)
    config = {
        "model_list": [
            {
                "model_name": "nvidia",
                "litellm_params": {
                    "model": f"nvidia_nim/{selected_model}",
                    "api_key": api_key,
                    "api_base": "https://integrate.api.nvidia.com/v1",
                },
            }
        ],
        "general_settings": {"master_key": PROXY_MASTER_KEY},
        "litellm_settings": {"drop_params": True},
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"📝 litellm proxy config written to {CONFIG_FILE}")
    return CONFIG_FILE


# ─── Step 7: Proxy lifecycle ─────────────────────────────────────────────
def port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def stop_running_proxy():
    """Stop any previously-started proxy for this bridge."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
        except (OSError, ValueError):
            pass
        try:
            os.remove(PID_FILE)
        except OSError:
            pass


def _get_venv_python():
    """Return the workspace virtualenv python executable, if it exists."""
    venv_py = "/workspace/.venv/bin/python3"
    if os.path.isfile(venv_py) and os.access(venv_py, os.X_OK):
        return venv_py
    return None


def _get_python():
    """Return the python executable to use for pip installs.
    Prefers the workspace virtualenv if it exists."""
    venv_python = _get_venv_python()
    if venv_python:
        return venv_python
    return sys.executable


def _get_litellm_bin():
    """Return the path to the litellm binary, preferring the venv."""
    # Check venv first
    venv_litellm = "/workspace/.venv/bin/litellm"
    if os.path.isfile(venv_litellm) and os.access(venv_litellm, os.X_OK):
        return venv_litellm
    # Fall back to shutil.which
    bin_path = shutil.which("litellm")
    if bin_path:
        return bin_path
    # Last resort: use the python -m approach with venv python
    return None


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


def start_proxy():
    """Start the litellm proxy in the background and wait until ready."""
    stop_running_proxy()
    os.makedirs(CONFIG_DIR, exist_ok=True)
    # Try litellm binary sources in order of preference
    litellm_bin = None
    # 1. Try workspace venv
    venv_litellm = "/workspace/.venv/bin/litellm"
    if os.path.isfile(venv_litellm) and os.access(venv_litellm, os.X_OK):
        litellm_bin = venv_litellm
    # 2. Try shutil.which
    if litellm_bin is None:
        bin_path = shutil.which("litellm")
        if bin_path:
            litellm_bin = bin_path
    # 3. Last resort: use python -m litellm with venv python
    if litellm_bin is None:
        venv_python = "/workspace/.venv/bin/python3"
        if os.path.isfile(venv_python) and os.access(venv_python, os.X_OK):
            litellm_bin = [venv_python, "-m", "litellm"]
        else:
            litellm_bin = [sys.executable, "-m", "litellm"]
    with open(LOG_FILE, "w") as logf:
        # litellm_bin may be a string path or a list [python, "-m", "litellm"]
        if isinstance(litellm_bin, str):
            cmd = [litellm_bin, "--config", CONFIG_FILE, "--port", str(PROXY_PORT)]
        else:
            cmd = litellm_bin + ["--config", CONFIG_FILE, "--port", str(PROXY_PORT)]
        proc = subprocess.Popen(
            cmd,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))

    print(f"🚀 Starting litellm proxy on port {PROXY_PORT} (PID {proc.pid})...")
    for _ in range(45):
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/health" % PROXY_PORT,
                headers={"Authorization": f"Bearer {PROXY_MASTER_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    print("   ✅ Proxy is ready.")
                    return proc
        except Exception:
            time.sleep(1)
    print("   ⚠️  Proxy did not become ready. Check the log below:")
    try:
        with open(LOG_FILE) as f:
            print(f.read()[-3000:])
    except OSError:
        pass
    return proc


# ─── Step 8: CLI test/validation step ────────────────────────────────────
def test_proxy_connection(selected_model):
    """Quick validation via the proxy to verify the model responds."""
    print(f"\n🧪 Testing selected model through proxy ({selected_model})...")
    payload = {
        "model": "nvidia",
        "max_tokens": 50,
        "messages": [{"role": "user", "content": "Hello! Please respond with a simple greeting."}],
    }
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/messages" % PROXY_PORT,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {PROXY_MASTER_KEY}",
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
                # Reasoning models put the reply in the "thinking" block.
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


# ─── Step 9: Launch Claude Code ──────────────────────────────────────────
def setup_claude_persistence():
    """Set up Claude Code persistence using .claude_persist in workspace.

    Migrates ~/.claude to workspace/.claude_persist so sessions survive
    devcontainer rebuilds. This mirrors the pattern from setup_claude_zen_devcontainer.sh.
    """
    import shutil

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    WORKSPACE_ROOT = os.path.abspath(SCRIPT_DIR)
    CLAUDE_PERSIST_DIR = os.path.join(WORKSPACE_ROOT, ".claude_persist")
    CLAUDE_CONFIG_DIR = os.path.expanduser("~/.claude")

    # Ensure the persist directory exists
    os.makedirs(CLAUDE_PERSIST_DIR, exist_ok=True)

    # Check current state of ~/.claude
    if os.path.islink(CLAUDE_CONFIG_DIR):
        # Already a symlink - check if it points to our persist dir
        try:
            CURRENT_TARGET = os.readlink(CLAUDE_CONFIG_DIR)
        except OSError:
            CURRENT_TARGET = ""
        if CURRENT_TARGET == CLAUDE_PERSIST_DIR:
            print(f"✅ ~/.claude already symlinked to {CLAUDE_PERSIST_DIR}")
            return
        else:
            print(f"  ~/.claude symlinked to {CURRENT_TARGET}, re-linking to {CLAUDE_PERSIST_DIR}")
            # Migrate existing content if persist dir is empty
            if not os.path.exists(CLAUDE_PERSIST_DIR) or not os.listdir(CLAUDE_PERSIST_DIR):
                print(f"  Migrating existing ~/.claude content to {CLAUDE_PERSIST_DIR}")
                # Remove the symlink first
                os.unlink(CLAUDE_CONFIG_DIR)
                # Copy content from original target if it still exists
                if os.path.isdir(CURRENT_TARGET):
                    shutil.copytree(CURRENT_TARGET, CLAUDE_PERSIST_DIR, dirs_exist_ok=True)
                # Now create symlink
                os.symlink(CLAUDE_PERSIST_DIR, CLAUDE_CONFIG_DIR)
                print(f"  ✅ Migrated ~/.claude → {CLAUDE_PERSIST_DIR}")
            else:
                # Persist dir has content, just re-point the symlink
                os.unlink(CLAUDE_CONFIG_DIR)
                os.symlink(CLAUDE_PERSIST_DIR, CLAUDE_CONFIG_DIR)
                print(f"  ✅ Re-linked ~/.claude → {CLAUDE_PERSIST_DIR}")
            return
    elif os.path.isdir(CLAUDE_CONFIG_DIR):
        # Regular directory - check if it's empty or has content
        if not os.listdir(CLAUDE_CONFIG_DIR):
            # Empty directory - just symlink it
            shutil.rmtree(CLAUDE_CONFIG_DIR)
            os.symlink(CLAUDE_PERSIST_DIR, CLAUDE_CONFIG_DIR)
            print(f"  ✅ Symlinked empty ~/.claude → {CLAUDE_PERSIST_DIR}")
        else:
            # Has content - copy to persist dir and symlink
            print(f"  Copying existing ~/.claude to {CLAUDE_PERSIST_DIR}")
            # Remove the persist dir if it exists and copy content
            if os.path.exists(CLAUDE_PERSIST_DIR):
                shutil.rmtree(CLAUDE_PERSIST_DIR)
            shutil.copytree(CLAUDE_CONFIG_DIR, CLAUDE_PERSIST_DIR, dirs_exist_ok=True)
            # Now replace with symlink
            shutil.rmtree(CLAUDE_CONFIG_DIR)
            os.symlink(CLAUDE_PERSIST_DIR, CLAUDE_CONFIG_DIR)
            print(f"  ✅ Migrated ~/.claude → {CLAUDE_PERSIST_DIR}")
    else:
        # No ~/.claude exists yet - create symlink
        os.symlink(CLAUDE_PERSIST_DIR, CLAUDE_CONFIG_DIR)
        print(f"  ✅ Created ~/.claude → {CLAUDE_PERSIST_DIR} (populated on first launch)")


def setup_statusline_symlink():
    """Set up statusline.sh symlink in the Claude config directory.

    Claude Code looks for statusline.sh in its config directory or we can
    ensure it's available. We'll create a symlink from workspace/statusline.sh
    to the Claude config location.
    """
    CLAUDE_CONFIG_DIR = os.path.expanduser("~/.claude")
    # statusline_src is now set up via the persistence mechanism - it will be in ~/.claude/

    # Check if statusline.sh exists in the workspace
    workspace_statusline = os.path.join(os.path.dirname(os.path.abspath(__file__)), "statusline.sh")

    if os.path.exists(workspace_statusline):
        # Create symlink in the persist dir (which ~/.claude points to)
        statusline_dst = os.path.join(CLAUDE_CONFIG_DIR, "statusline.sh")

        # Resolve the actual path since ~/.claude may be a symlink to .claude_persist
        actual_statusline_path = os.path.realpath(statusline_dst)

        # Check if it's already a symlink pointing to workspace/statusline.sh
        if os.path.islink(statusline_dst):
            # It's already a symlink - check if target is correct
            try:
                real_target = os.readlink(statusline_dst)
                # Normalize the target path - if it's relative, prepend ~/.claude
                if not os.path.isabs(real_target):
                    real_target = os.path.join(CLAUDE_CONFIG_DIR, real_target)
                # Check if the resolved target is the workspace statusline.sh
                resolved_target = os.path.realpath(real_target)
                workspace_resolved = os.path.realpath(workspace_statusline)
                if resolved_target == workspace_resolved:
                    print(f"✅ statusline.sh symlink already correct at {statusline_dst}")
                    return
            except OSError:
                pass
            # Symlink exists but wrong target - remove and recreate
            os.unlink(statusline_dst)
        elif os.path.exists(statusline_dt := statusline_dst):
            # It's a regular file (not symlink) - remove it to create symlink
            # But first check if it's the same as workspace/statusline.sh (by content/inode)
            try:
                if os.path.samestat(os.stat(statusline_dst), os.stat(workspace_statusline)):
                    print(f"✅ statusline.sh already matches workspace version at {statusline_dst}")
                    return
            except OSError:
                pass
            os.unlink(statusline_dst)
        else:
            # File doesn't exist - good, will create symlink
            pass

        # Create the symlink
        try:
            # Ensure parent dir exists (should already via persistence setup)
            os.makedirs(os.path.dirname(statusline_dst), exist_ok=True)
            os.symlink(workspace_statusline, statusline_dst)
            print(f"📐 Symlinked workspace statusline.sh → {statusline_dst}")
        except OSError as e:
            print(f"⚠️  Could not create symlink: {e}")
    else:
        print(f"⚠️  workspace statusline.sh not found at {workspace_statusline}")


def launch_claude_with_model(selected_model, dangerously_skip_permissions=False):
    """Configure environment to use the litellm proxy and launch Claude Code."""
    # Build claude command with optional --dangerously-skip-permissions flag
    claude_cmd = ["claude"]
    if dangerously_skip_permissions:
        claude_cmd.append("--dangerously-skip-permissions")

    env = os.environ.copy()
    # Point Claude Code at the local litellm proxy, which exposes the
    # Anthropic-compatible /v1/messages endpoint.
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:%d" % PROXY_PORT
    env["ANTHROPIC_AUTH_TOKEN"] = PROXY_MASTER_KEY
    env["ANTHROPIC_MODEL"] = "nvidia"
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = "nvidia"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_MODEL_CALLS"] = "1"
    env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
    # Don't let claude try to discover/switch to a gateway model.
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "0"

    # Set up Claude Code persistence so sessions survive devcontainer rebuilds
    setup_claude_persistence()

    # Set up statusline.sh symlink so Claude Code uses our custom status line
    setup_statusline_symlink()

    print("\n🚀 Launching Claude Code with selected NVIDIA model...")
    print("   (This will open an interactive Claude Code session)")

    try:
        subprocess.run(claude_cmd, env=env)
    except FileNotFoundError:
        print("❌ Error: 'claude' CLI tool is not installed on your system.")
        print("   Install it via: curl -fsSL https://claude.ai | bash")
    except subprocess.CalledProcessError as e:
        print(f"\nClaude Code exited with an error code: {e.returncode}")


# ─── Usage Notes ─────────────────────────────────────────────────────────
def print_usage_notes(dangerously_skip_permissions=False):
    """Print usage notes and relevant environment variables."""
    print("=" * 60)
    print("  NVIDIA → Claude Code Bridge Script")
    print("=" * 60)
    print()
    print("📋  SCRIPT PURPOSE:")
    print("   This script bridges NVIDIA NIM models with the Claude Code CLI.")
    print("   It fetches available NVIDIA chat models, lets you select one,")
    print("   runs a local litellm proxy (Anthropic→OpenAI translation),")
    print("   then launches Claude Code configured to use that NVIDIA model.")
    print()
    print("🔑  API KEY SETUP:")
    print("   • Set NVIDIA_API_KEY environment variable export")
    print("     NVIDIA_API_KEY='nvapi-...'")
    print("   • Or run the script once - it will prompt and cache the key")
    print("     to ~/.nvidia_api_key_cache for future runs.")
    print()
    if dangerously_skip_permissions:
        print("⚡  DANGEROUSLY SKIP PERMISSIONS: --dangerously-skip-permissions")
        print("   Flag passed - Claude Code will skip permission prompts.")
        print("   (Safe in devcontainer environments)")
    else:
        print("⚡  PERMISSIONS: Claude Code will show permission prompts")
        print("   (Use --dangerously-skip-permissions to skip these)")
    print()
    print("🌐  HOW IT WORKS:")
    print("   Claude Code speaks the Anthropic Messages API (/v1/messages).")
    print("   NVIDIA exposes an OpenAI-compatible API (/v1/chat/completions).")
    print("   A local litellm proxy translates between them so conversation")
    print("   and tool calls work against the NVIDIA model.")
    print()
    print("   • ANTHROPIC_BASE_URL=http://127.0.0.1:<port>  (litellm proxy)")
    print("   • ANTHROPIC_AUTH_TOKEN=sk-claude-bridge  (proxy master key)")
    print("   • ANTHROPIC_MODEL=nvidia  (model alias on the proxy)")
    print()
    print("📦  PREREQUISITES (automatically checked/installed):")
    print("   • Python3 with 'litellm[proxy]' package")
    print("   • claude CLI tool (https://claude.ai)")
    print()
    print("=" * 60)
    print()


# ─── Main ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Bridge NVIDIA NIM models with the Claude Code CLI"
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        default=False,
        help="Skip Claude Code permission prompts (safe in devcontainer environments)",
    )
    args = parser.parse_args()

    print_usage_notes(dangerously_skip_permissions=args.dangerously_skip_permissions)

    if not ensure_prerequisites():
        print("❌ Prerequisites check failed. Exiting.")
        sys.exit(1)

    api_key = get_api_key()

    print("\n🔄 Fetching model list from NVIDIA NIM API...")
    all_raw_models = fetch_models(api_key)

    standard, free, combined = categorize_models(all_raw_models)
    selected_model = display_and_select(standard, free, combined)

    if not check_model_access(selected_model, api_key):
        print("\n⚠️  The selected model is not accessible with your API key.")
        print("    Please run the script again and pick a different model.")
        print("    (Some NVIDIA models are restricted to certain accounts.)")
        sys.exit(1)

    generate_litellm_config(selected_model, api_key)
    proc = start_proxy()

    try:
        if not test_proxy_connection(selected_model):
            print("\n❌ Proxy validation failed. Claude Code likely won't work.")
            print("   Check ~/.claude_nvidia/proxy.log for details.")
            print("   You may still attempt to launch manually.")
        launch_claude_with_model(selected_model, args.dangerously_skip_permissions)
    finally:
        # Stop the proxy after Claude Code exits.
        stop_running_proxy()
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()