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
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_API_URL = f"{OPENCODE_ZEN_BASE_URL}/models"
OPENCODE_CHAT_URL = f"{OPENCODE_ZEN_BASE_URL}/chat/completions"
CACHE_FILE = os.path.expanduser("~/.opencode_api_key_cache")
MODEL_CACHE_FILE = os.path.expanduser("~/.claude_opencode_last_model")
CONTEXT_CACHE_FILE = os.path.expanduser("~/.claude_opencode_last_context")
FAVORITES_CACHE_FILE = os.path.expanduser("~/.claude_opencode_favorites")
CONTEXT_WINDOW_CACHE_DIR = os.path.expanduser("~/.claude_opencode_context_windows")
PROVIDER_INDICATOR = "opencode"  # For statusline to identify provider
STATUSLINE_MODE_CACHE_FILE = os.path.expanduser("~/.claude_opencode_statusline_mode")
AUTO_COMPACTION_THRESHOLD = 91  # Auto-compaction when usage >= this percentage
PROXY_PORT = 4501
PROXY_MASTER_KEY = "sk-opencode-bridge"
CONFIG_DIR = os.path.expanduser("~/.claude_opencode")
CONFIG_FILE = os.path.join(CONFIG_DIR, "litellm_proxy.yaml")
LOG_FILE = os.path.join(CONFIG_DIR, "proxy.log")
PID_FILE = os.path.join(CONFIG_DIR, "proxy.pid")

# Ensure prompt_toolkit is available for the favorites menu system
try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.styles import Style
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    print("  📦 Installing prompt_toolkit for favorites menu...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "prompt_toolkit"], check=False)
    try:
        from prompt_toolkit import Application, KeyBindings, Layout, HSplit
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.styles import Style
        PROMPT_TOOLKIT_AVAILABLE = True
    except ImportError:
        print("  ⚠️  prompt_toolkit not available - using basic selection")
        PROMPT_TOOLKIT_AVAILABLE = False
PROXY_MASTER_KEY = "sk-opencode-bridge"
CONFIG_DIR = os.path.expanduser("~/.claude_opencode")
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
    """Check for updates to Claude CLI and optionally upgrade."""
    # Get current version
    rc, out = _run(["claude", "--version"], label="cli-version")
    if rc == 0:
        current_version = out.strip()
        print(f"   Current claude CLI version: {current_version}")
    # Prompt for upgrade (handle EOF gracefully – default to 'n')
    try:
        resp = input("   Check for Claude CLI upgrade? (y/N): ").strip().lower()
    except EOFError:
        resp = "n"
    if resp == "y":
        print("   Upgrading claude CLI via npm...")
        _run(["npm", "install", "-g", "@anthropic-ai/claude-code@latest"], label="cli-upgrade")
        # Re-verify
        rc2, out2 = _run(["claude", "--version"], label="cli-version-after")
        if rc2 == 0:
            print(f"   New claude CLI version: {out2.strip()}")
    # Continue with existing logic (return True if already okay)
    if _claude_version_ok():
        return True
    # If not ok, fall through to install (original install logic follows)
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


def ensure_litellm():
    """Ensure litellm is installed and the CLI binary is available on PATH.

    Handles both venv and system installs. This is a prerequisite so the
    proxy can start immediately without failing at launch time.
    Tries [proxy] extra first, falls back to base litellm.
    Always uses --break-system-packages for system installs (PEP 668).
    """
    import shutil

    print("🔍 Ensuring litellm is available...")

    # 1. Check if litellm CLI is already on PATH
    if shutil.which("litellm"):
        print("  ✅ litellm CLI already available on PATH")
        return True

    # 2. Check workspace venv
    venv_exists = os.path.isdir("/workspace/.venv")
    venv_python = "/workspace/.venv/bin/python3" if venv_exists else None
    venv_litellm = "/workspace/.venv/bin/litellm" if venv_exists else None

    # 3. Try installing litellm[proxy] system-wide with --break-system-packages
    print("  📦 Ensuring litellm[proxy] system-wide...")
    subprocess.run(
        ["pip", "install", "--break-system-packages", "-q", "litellm[proxy]"],
        capture_output=True, timeout=180,
    )
    if shutil.which("litellm"):
        print("  ✅ litellm[proxy] installed system-wide")
        return True

    # 4. Fall back to pip install --user as alternative
    print("  📦 Trying pip install --user...")
    subprocess.run(
        ["pip", "install", "--user", "-q", "litellm[proxy]"],
        capture_output=True, timeout=120,
    )
    # Check user bin dir for litellm CLI
    user_bin = os.path.expanduser("~/.local/bin")
    if os.path.isfile(os.path.join(user_bin, "litellm")) and os.access(os.path.join(user_bin, "litellm"), os.X_OK):
        # Add user bin to PATH for this session's subprocess.Popen
        os.environ["PATH"] = f"{user_bin}:{os.environ.get('PATH', '')}"
        print("  ✅ litellm[proxy] installed via pip --user")
        return True

    print("  ❌ Could not install litellm. "
          "Try: pip install --break-system-packages litellm[proxy]")
    return False


def ensure_prerequisites():
    """Ensure litellm (with the proxy extras) and the claude CLI are available."""
    print("🔍 Checking prerequisites...")
    ok = True
    ok &= ensure_litellm()
    ok &= ensure_claude_cli()
    return ok


# ─── Step 2: Prompt for API key if not cached ───────────────────────────
def get_api_key(clear=False):
    """Return a valid OpenCode Zen API key, prompting if needed and caching it.
    If `clear` is True, the cached key is removed and the user is prompted again.
    If the user provides an empty key, anonymous mode is used (no Authorization header).
    """
    # Handle clear flag
    if clear and os.path.exists(CACHE_FILE):
        try:
            os.remove(CACHE_FILE)
            print(f"🗑️  Cleared cached OpenCode API key at {CACHE_FILE}.")
        except OSError:
            print(f"⚠️  Failed to delete cached OpenCode API key at {CACHE_FILE}.")

    OPENCODE_API_KEY = os.environ.get("OPENCODE_ZEN_API_KEY") or os.environ.get("OPENCODE_API_KEY")

    if OPENCODE_API_KEY:
        print("✅ OpenCode Zen API key found in environment.")
        return OPENCODE_API_KEY

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cached = f.read().strip()
            if cached:
                print(f"✅ Found cached API key in {CACHE_FILE}.")
                os.environ["OPENCODE_API_KEY"] = cached
                return cached
        except OSError:
            print(f"⚠️  Could not read cached API key from {CACHE_FILE}.")

    print("🔑 OpenCode API key not found.")
    try:
        api_key = input("   Please enter your OpenCode API key (or press Enter for anonymous): ").strip()
    except EOFError:
        print("⚠️  No API key provided – proceeding in anonymous mode.")
        return ""
    if not api_key:
        print("⚠️  No API key provided – proceeding in anonymous mode.")
        return ""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            f.write(api_key)
        print(f"💾 API key cached to {CACHE_FILE}.")
    except OSError:
        print(f"⚠️  Could not cache API key to {CACHE_FILE}.")

    os.environ["OPENCODE_API_KEY"] = api_key
    return api_key


# ─── Step 3: Fetch models from OpenCode ────────────────────────────────────
def http_get_json(url, api_key):
    """GET JSON from the OpenCode Zen API.

    If api_key is empty (anonymous mode), no Authorization header is sent.
    Includes User-Agent to avoid 403 Forbidden from Cloudflare.
    """
    headers = {"Accept": "application/json", "User-Agent": "curl/8.5.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# Curated list of KNOWN FREE MODELS from OpenCode Zen (from setup script & docs).
# These work WITHOUT an API key. ONLY these are used as fallback — NO proprietary models.
# Context windows: user-verified for OpenCode free tier (all >= 200K, most 1M)
OPENCODE_KNOWN_FREE_MODELS = [
    {"id": "big-pickle", "context_window": 200000},
    {"id": "hy3-free", "context_window": 200000},
    {"id": "laguna-s-2.1-free", "context_window": 1048576},  # Verified: max_position_embeddings=1048576
    {"id": "ling-3.0-flash-fin-free", "context_window": 200000},
    {"id": "deepseek-v4-flash-free", "context_window": 200000},
    {"id": "nemotron-3-ultra-free", "context_window": 1048576},   # User-verified: 1M on OpenCode free tier
    {"id": "muse-spark-1.2-contributor-free", "context_window": 200000},
    {"id": "muse-spark-1.3-contributor-free", "context_window": 200000},
    {"id": "mimo-v2.5-free", "context_window": 200000},
    {"id": "nemotron-3.5-lightning-free", "context_window": 200000},  # Verified: NIM version = 1M
]


def fetch_models(api_key):
    """Fetch the list of available chat models from the OpenCode API.

    On success, normalizes to [{"id": ..., "owned_by": ..., "context_window": ...}].
    If the live endpoint is unreachable (e.g. Cloudflare block), falls back to
    the curated KNOWN FREE MODELS list so anonymous users can still proceed.
    """
    # Build curated context window lookup
    curated_ctx = {m["id"]: m["context_window"] for m in OPENCODE_KNOWN_FREE_MODELS}

    try:
        data = http_get_json(OPENCODE_API_URL, api_key)
        raw = data.get("data", [])
        models = []
        for m in raw:
            model_id = m.get("id", "")
            if not model_id:
                continue
            owned_by = m.get("owned_by", "")
            # OpenCode API doesn't return context window, so add from curated list
            context_window = curated_ctx.get(model_id, 0)
            models.append({"id": model_id, "owned_by": owned_by, "context_window": context_window})
        if models:
            print("   ✅ Model list fetched from OpenCode API.")
            # Log how many got context from curated list
            with_ctx = sum(1 for m in models if m["context_window"] > 0)
            if with_ctx > 0:
                print(f"   📏 Added context windows for {with_ctx} known models from curated list")
            return models
        raise RuntimeError("Empty model list from API")
    except Exception as e:
        print(f"   ⚠️  Could not reach OpenCode API ({str(e)[:100]}).")
        print("   Falling back to curated list of known free models (no API key needed).")
        # Return curated free models with owned_by="community" and context_window
        return [{"id": m["id"], "owned_by": "community", "context_window": m["context_window"]} for m in OPENCODE_KNOWN_FREE_MODELS]


# ─── Step 4: Categorize, filter, and sort models ────────────────────────
def categorize_models(all_raw_models):
    """Categorize models into standard and free/tier, sorted with free at bottom.

    Free models (with "free" in title or specific free-tier keywords) are
    sorted and separated at the bottom of the selector list.
    Returns full model objects (with context_window) for display.
    """
    NON_CHAT_KEYWORDS = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"]
    FREE_KEYWORDS = ["community", "instruct", "chat", "deepseek", "kimi", "glm", "llama", "gemma", "nemotron", "free"]

    standard_chat_models = []
    free_tier_chat_models = []

    for model_obj in all_raw_models:
        model_id = model_obj.get("id", "")
        owned_by = model_obj.get("owned_by", "").lower()
        model_id_lower = model_id.lower()

        if any(keyword in model_id_lower for keyword in NON_CHAT_KEYWORDS):
            continue

        # Check if this is a free model
        is_free = (
            "community" in owned_by
            or any(keyword in model_id_lower for keyword in ["deepseek", "kimi", "glm", "llama", "gemma", "nemotron", "free"])
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
    combined_models_list = standard_chat_models + free_tier_chat_models
    return standard_chat_models, free_tier_chat_models, combined_models_list


def load_last_model():
    """Load the last selected model from cache."""
    if os.path.exists(MODEL_CACHE_FILE):
        try:
            with open(MODEL_CACHE_FILE, "r") as f:
                return f.read().strip()
        except OSError:
            pass
    return None


def save_last_model(model_id):
    """Save the selected model to cache."""
    try:
        os.makedirs(os.path.dirname(MODEL_CACHE_FILE), exist_ok=True)
        with open(MODEL_CACHE_FILE, "w") as f:
            f.write(model_id)
    except OSError:
        pass


def load_last_context():
    """Load the last context window from cache."""
    if os.path.exists(CONTEXT_CACHE_FILE):
        try:
            with open(CONTEXT_CACHE_FILE, "r") as f:
                val = f.read().strip()
                return int(val) if val.isdigit() else None
        except (OSError, ValueError):
            pass
    return None


def save_last_context(context_window):
    """Save the context window to cache."""
    try:
        os.makedirs(os.path.dirname(CONTEXT_CACHE_FILE), exist_ok=True)
        with open(CONTEXT_CACHE_FILE, "w") as f:
            f.write(str(context_window))
    except OSError:
        pass


# ─── Model-specific context window cache (for remembering per-model context) ────────────────────────────────────────
def load_model_context(model_id):
    """Load the last context window for a specific model from cache."""
    cache_file = os.path.join(CONTEXT_WINDOW_CACHE_DIR, f"{model_id}.txt")
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
        os.makedirs(CONTEXT_WINDOW_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CONTEXT_WINDOW_CACHE_DIR, f"{model_id}.txt")
        with open(cache_file, "w") as f:
            f.write(str(context_window))
    except OSError:
        pass


# ─── Model-specific auto-compaction threshold cache ─────────────────────────────────────────────────────────────
def load_model_compaction(model_id):
    """Load the last auto-compaction threshold (%) for a specific model from cache."""
    cache_file = os.path.join(CONTEXT_WINDOW_CACHE_DIR, f"{model_id}.compaction.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                val = f.read().strip()
                return int(val) if val.isdigit() else None
        except (OSError, ValueError):
            pass
    return None


def save_model_compaction(model_id, threshold):
    """Save the auto-compaction threshold (%) for a specific model to cache."""
    try:
        os.makedirs(CONTEXT_WINDOW_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CONTEXT_WINDOW_CACHE_DIR, f"{model_id}.compaction.txt")
        with open(cache_file, "w") as f:
            f.write(str(threshold))
    except OSError:
        pass


# ─── Statusline mode cache (1-line "compact" vs 2-line "full") ───────────────────────────────────────────────
def load_statusline_mode():
    """Load the last used statusline mode ('full' or 'compact') from cache."""
    if os.path.exists(STATUSLINE_MODE_CACHE_FILE):
        try:
            with open(STATUSLINE_MODE_CACHE_FILE, "r") as f:
                val = f.read().strip().lower()
                if val in ("full", "compact"):
                    return val
        except OSError:
            pass
    return None


def save_statusline_mode(mode):
    """Save the last used statusline mode ('full' or 'compact') to cache."""
    try:
        os.makedirs(os.path.dirname(STATUSLINE_MODE_CACHE_FILE), exist_ok=True)
        with open(STATUSLINE_MODE_CACHE_FILE, "w") as f:
            f.write(mode)
    except OSError:
        pass


# ─── Favorites cache ───────────────────────────────────────────────────────────────────────────────────────────────
def load_favorites():
    """Load the set of favorite model IDs from cache."""
    if os.path.exists(FAVORITES_CACHE_FILE):
        try:
            with open(FAVORITES_CACHE_FILE, "r") as f:
                content = f.read().strip()
                if content:
                    return set(content.split("\n"))
        except OSError:
            pass
    return set()


def save_favorites(favorites_set):
    """Save the set of favorite model IDs to cache."""
    try:
        os.makedirs(os.path.dirname(FAVORITES_CACHE_FILE), exist_ok=True)
        with open(FAVORITES_CACHE_FILE, "w") as f:
            f.write("\n".join(sorted(favorites_set)))
    except OSError:
        pass


# Favorites selector using prompt_toolkit for a robust TUI with proper arrow-key
# navigation, SPACE to toggle favorites, and Enter to confirm.
# Returns the updated set of favorite model IDs.
def favorites_selector(models, current_favorites):
    """Present a favorites toggle list and return updated favorites set.

    Shows each model with its current favorite status (★ if favorited).
    models: list of model ID strings (not dicts).
    Users can toggle favorites with SPACE, navigate with arrows, and confirm with Enter.
    """
    if not models:
        return current_favorites

    terminal_height = _terminal_height()
    visible_count = max(3, min(terminal_height - 4, len(models)))
    # Clamp start_idx to valid range
    start_idx = 0

    current = [start_idx]  # use list for closure mutability
    result = [None, None, None]  # use list for closure mutability: [0]=idx, [1]=model, [2]=favorites

    # Build display: favorite status indicator + model ID with context
    def get_formatted_options():
        """Return formatted text for the list, recalculated on each render."""
        top = get_top_idx()
        fragments = []

        # Prompt + instructions
        fragments.append(("class:prompt", "Toggle favorites • SPACE to toggle • Enter to confirm • Esc to cancel\n"))

        items_above = top
        items_below = len(models) - (top + visible_count)

        for i in range(top, min(top + visible_count, len(models))):
            if i == current[0]:
                # Currently highlighted item
                model_id = models[i]
                is_fav = model_id in current_favorites
                prefix = "★ " if is_fav else "  "
                fragments.append(("class:current", f"  {prefix}{model_id}\n"))
            else:
                model_id = models[i]
                is_fav = model_id in current_favorites
                prefix = "★ " if is_fav else "  "
                fragments.append(("class:normal", f"    {prefix}{model_id}\n"))

        if items_above > 0 or items_below > 0:
            hint_parts = []
            if items_above > 0:
                hint_parts.append(f"{items_above} above")
            if items_below > 0:
                hint_parts.append(f"{items_below} below")
            fragments.append(("class:hint", "  " + " ".join(hint_parts) + "\n"))

        return fragments

    def get_top_idx():
        """Calculate the top visible index so current selection is always visible."""
        top = current[0] - (current[0] % visible_count)
        top = max(0, min(top, max(0, len(models) - visible_count)))
        if current[0] < top:
            top = current[0]
        elif current[0] >= top + visible_count:
            top = current[0] - visible_count + 1
        return top

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        if current[0] > 0:
            current[0] -= 1

    @kb.add("down")
    def _(event):
        if current[0] < len(models) - 1:
            current[0] += 1

    @kb.add("space")
    def _(event):
        """Toggle favorite status of the current model."""
        model_id = models[current[0]].get("id", "")
        if model_id in current_favorites:
            current_favorites.discard(model_id)
        else:
            current_favorites.add(model_id)
        # Re-render by just continuing - the display will update

    @kb.add("enter")
    def _(event):
        result[0] = current[0]
        event.app.exit()

    @kb.add("escape")
    def _(event):
        result[0] = None  # Cancel without saving
        event.app.exit()

    control = FormattedTextControl(get_formatted_options)
    window = Window(
        content=control,
        height=max(visible_count + 4, 8),
        always_hide_cursor=True,
    )

    style = Style.from_dict({
        "current": "reverse",        # highlighted (selected) item
        "normal": "",
        "hint": "italic #888888",     # dim instructions / scroll hints
        "prompt": "bold",
    })

    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=False,
        style=style,
        mouse_support=False,
    )

    app.run()

    if result[0] is not None:
        # User confirmed - save favorites
        save_favorites(current_favorites)
        return current_favorites
    return current_favorites  # Canceled - return unchanged


# Interactive menu selector using prompt_toolkit (already a dependency of
# the launch scripts). Provides a robust, tested TUI with proper scrolling:
#   • UP/DOWN arrows move the highlight one line; the selected item is always
#     kept visible by auto-scrolling the viewport.
#   • PageUp / PageDown move by a page.
#   • Home / End jump to first/last.
#   • Enter selects; Esc (or Ctrl-C / Ctrl-D) cancels.
# prompt_toolkit handles terminal resize and escape sequences internally,
# avoiding the hand-rolled ANSI / termios code that caused scrolling bugs.
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.styles import Style


def _terminal_height():
    """Return usable terminal height, clamped to a sane minimum."""
    try:
        import shutil
        rows = shutil.get_terminal_size().lines
        if rows and rows > 4:
            return rows
    except Exception:
        pass
    return 24


def arrow_key_selector(options, prompt="Select an option:", start_idx=0, favorites=None):
    """
    Interactive arrow-key selector using prompt_toolkit.

    Returns (selected_index, selected_option, updated_favorites) or (None, None, None) on cancel.
    Supports UP/DOWN arrows, PAGE_UP/PAGE_DOWN, HOME/END, LEFT/RIGHT to toggle views,
    ENTER, ESC, and SPACE to toggle favorites.
    LEFT cycles to favorites-only view; RIGHT cycles back to full model list.
    The highlighted item is always kept visible via auto-scrolling.
    Favorites are shown with ★ prefix; SPACE toggles favorite status.
    """
    if not options:
        return None, None, None

    # If not running in a terminal, fall back to input-based selection
    if not hasattr(sys.stdin, 'isatty') or not sys.stdin.isatty():
        print("⚠️  Not running in a terminal - using number selection instead")
        selected = get_selection_input(prompt, len(options))
        return selected, options[selected - 1] if selected else (None, None, favorites)

    terminal_height = _terminal_height()
    visible_count = max(3, min(terminal_height - 4, len(options)))
    # Clamp start_idx to valid range
    start_idx = max(0, min(start_idx, len(options) - 1))
    current = [start_idx, 0]  # use list for closure mutability: [0]=idx, [1]=view (0=full, 1=favorites)
    result = [None, None, None]  # use list for closure mutability: [0]=idx, [1]=model, [2]=favorites

    # Ensure favorites is a set
    if favorites is None:
        favorites = set()

    def build_options():
        """Build display entries for the current view mode (reads current[1])."""
        view_mode = current[1]
        if view_mode == 0:
            # Full list - all options with their original indices
            return [{"type": "model", "id": opt, "idx": i} for i, opt in enumerate(options)]
        else:
            # Favorites-only view - show only favorited models
            fav_entries = []
            for i, opt in enumerate(options):
                if opt in favorites:
                    fav_entries.append({"type": "model", "id": opt, "idx": i})
            return fav_entries

    def get_formatted_options():
        """Return formatted text for the list, recalculated on each render."""
        opts = build_options()
        view_mode = current[1]
        top = current[0] - (current[0] % visible_count)
        top = max(0, min(top, max(0, len(opts) - visible_count)))
        if current[0] < top:
            top = current[0]
        elif current[0] >= top + visible_count:
            top = current[0] - visible_count + 1

        # Navigation hint - always displayed at top of menu
        if view_mode == 0:
            hint_text = "  ↑/↓ navigate • PgUp/PgDn page • Home/End jump  Left/Right toggle view• Enter select• SPACE toggle fav• Esc cancel"
        else:
            hint_text = "  ↑/↓ navigate • PgUp/PgDn page • Home/End jump  Left/Right toggle view• Enter select• SPACE toggle fav• Esc cancel"

        fragments = []

        # Navigation hint first - always visible at top
        fragments.append(("class:hint", hint_text + "\n"))

        # Prompt + instructions showing current view
        if view_mode == 0:
            view_label = "Full List"
        else:
            visible_favs = len([e for e in build_options() if e is not None])
            view_label = f"Favorites ({visible_favs} fav)"
        fragments.append(("class:prompt", prompt + f"  ({view_label}) • "))

        items_above = top
        items_below = len(opts) - (top + visible_count)

        for i in range(top, min(top + visible_count, len(opts))):
            if i == current[0]:
                entry = opts[i]
                model_id = entry["id"]
                is_fav = model_id in favorites
                prefix = "★ " if is_fav else "  "
                fragments.append(("class:current", f"  {prefix}{model_id}\n"))
            else:
                entry = opts[i]
                model_id = entry["id"]
                is_fav = model_id in favorites
                prefix = "★ " if is_fav else "  "
                fragments.append(("class:normal", f"    {prefix}{model_id}\n"))

        if items_above > 0 or items_below > 0:
            hint_parts = []
            if items_above > 0:
                hint_parts.append(f"{items_above} above")
            if items_below > 0:
                hint_parts.append(f"{items_below} below")
            fragments.append(("class:hint", "  " + " ".join(hint_parts) + "\n"))

        return fragments

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        if current[0] > 0:
            current[0] -= 1

    @kb.add("down")
    def _(event):
        opts_len = len(build_options())
        if current[0] < opts_len - 1:
            current[0] += 1

    @kb.add("pageup")
    def _(event):
        page = min(visible_count - 1, len(build_options()))
        current[0] = max(0, current[0] - page)

    @kb.add("pagedown")
    def _(event):
        page = min(visible_count - 1, len(build_options()))
        current[0] = min(len(build_options()) - 1, current[0] + page)

    @kb.add("home")
    def _(event):
        current[0] = 0

    @kb.add("end")
    def _(event):
        opts_len = len(build_options())
        current[0] = opts_len - 1 if opts_len > 0 else 0

    @kb.add("left")
    def _(event):
        """Cycle to favorites-only view."""
        if current[1] != 1:
            current[1] = 1
            current[0] = min(current[0], len(build_options()) - 1) if build_options() else 0

    @kb.add("right")
    def _(event):
        """Cycle back to full model list view."""
        if current[1] != 0:
            current[1] = 0
            current[0] = min(current[0], len(options) - 1)

    @kb.add("enter")
    def _(event):
        result[0] = current[0]
        # Save favorites state when selecting
        result[2] = favorites
        event.app.exit()

    @kb.add("space")
    def _(event):
        """Toggle favorite status of the current model."""
        opts = build_options()
        if current[0] < len(opts):
            model_id = opts[current[0]]["id"]
            if model_id in favorites:
                favorites.discard(model_id)
            else:
                favorites.add(model_id)

    @kb.add("escape")
    def _(event):
        result[0] = None  # Cancel without saving
        result[2] = None  # No favorites change
        event.app.exit()

    control = FormattedTextControl(get_formatted_options)
    window = Window(
        content=control,
        height=max(visible_count + 4, 8),
        always_hide_cursor=True,
    )

    style = Style.from_dict({
        "current": "reverse",        # highlighted (selected) item
        "normal": "",
        "hint": "italic #888888",     # dim instructions / scroll hints
        "prompt": "bold",
    })

    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=False,
        style=style,
        mouse_support=False,
    )

    app.run()

    if result[0] is not None:
        # Map the selected index back to the original options list
        selected_idx = result[0]
        # Always return 3 values: (selected_index, selected_option, updated_favorites)
        # If in favorites view, map back to original model index
        if current[1] == 1:
            # Find the selection in the favorites entries and map to original index
            fav_entries = build_options()
            if selected_idx < len(fav_entries):
                orig_idx = fav_entries[selected_idx]["idx"]
                return orig_idx, options[orig_idx] if orig_idx < len(options) else options[0], favorites
            # Fallback if selection not in favorites
            return 0, options[0] if options else None, favorites
        # In full view, just return the selected index with its option and current favorites
        return selected_idx, options[selected_idx] if selected_idx < len(options) else options[0] if options else None, favorites
    return None, None, None

    style = Style.from_dict({
        "current": "reverse",        # highlighted (selected) item
        "normal": "",
        "hint": "italic #888888",     # dim instructions / scroll hints
        "prompt": "bold",
    })

    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=False,
        style=style,
        mouse_support=False,
    )

    app.run()

    if result[0] is not None:
        return result[0], options[result[0]]
    return None, None

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
    print("       OPENCODE ZEN CHAT MODELS       ")
    print("========================================")

    current_number = 1
    if standard:
        print("\n--- Standard & Enterprise Chat Models ---")
        for model_obj in standard:
            model_id = model_obj.get("id", "")
            ctx = model_obj.get("context_window", 0)
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
            print(f"[{current_number}] {model_id}{ctx_str}")
            current_number += 1
    if free:
        print("\n--- Free & Community Tier Chat Models ---")
        for model_obj in free:
            model_id = model_obj.get("id", "")
            ctx = model_obj.get("context_window", 0)
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
            print(f"[{current_number}] {model_id}{ctx_str} (Free Tier)")
            current_number += 1

    print("========================================")
    print(f"\nTotal models: {len(combined)}")

    # Check for last used model
    last_model = load_last_model()
    last_idx = None
    if last_model:
        # Find the index of last_model
        for i, m in enumerate(combined):
            if m.get("id") == last_model:
                last_idx = i  # 0-based for arrow selector
                break

    # Build display options with context info
    display_options = []
    for m in combined:
        model_id = m.get("id", "")
        ctx = m.get("context_window", 0)
        ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
        display_options.append(f"{model_id}{ctx_str}")

    # Load favorites before selection
    current_favorites = load_favorites()

    # Use arrow key selector
    print("\nUse ↑/↓ arrows to navigate, Enter to select:")
    selected_idx, selected_model, current_favorites = arrow_key_selector(
        display_options, "Select a model:", start_idx=last_idx if last_idx is not None else 0, favorites=current_favorites
    )
    if selected_idx is None:
        print("\n👋 No model selected. Exiting.")
        sys.exit(0)
    selected_model = combined[selected_idx].get("id", "")
    print(f"\n🚀 Selected Model: {selected_model}")

    # Save last model
    save_last_model(selected_model)

    # Save favorites (persists what was toggled with SPACE during selection)
    # Note: current_favorites was already modified during selection via SPACE;
    # do NOT re-load from cache here or the SPACE toggles would be lost.
    save_favorites(current_favorites)

    # Get model data for the selected model
    model_data = combined[selected_idx] if selected_idx is not None and selected_idx < len(combined) else {}

    return selected_model, model_data


def get_context_window(selected_model, model_data):
    """Prompt user for context window with a menu-based selection.

    The menu shows:
    - Detected Context Window (from model data)
    - Last Used Context Window (from cache, model-specific)
    - Enter Custom Context Window (input prompt)

    Each model remembers its last used context window independently.
    """
    # Get context values
    model_ctx = model_data.get("context_window", 0)
    model_cached_ctx = load_model_context(selected_model)
    default_ctx = model_ctx if model_ctx > 0 else 200000

    # Build menu options
    options = []
    if model_ctx > 0:
        options.append(("Detected Context Window", model_ctx, "model"))
    if model_cached_ctx:
        options.append(("Last Used Context Window", model_cached_ctx, "cached"))
    options.append(("Enter Custom Context Window", default_ctx, "custom"))

    print(f"\n📏 Context Window Configuration for {selected_model}")
    print(f"   Model default: {model_ctx:,} tokens" if model_ctx > 0 else "   Model default: unknown")
    print(f"   Last used: {model_cached_ctx:,} tokens" if model_cached_ctx else "   Last used: none")

    # Display menu number
    for i, (label, value, src) in enumerate(options, 1):
        src_indicator = {"model": "📦", "cached": "💾", "custom": "✏️"}[src]
        print(f"   [{i}] {src_indicator} {label}: {value:,} tokens")

    print(f"\n   [0] Cancel")

    try:
        choice = input(f"\n📏 Select context window [0-{len(options)}] (ENTER = default, custom number for option {len(options)}): ").strip()
        if not choice:
            # ENTER accepts the default:
            #   – last used cached value (option 2 if available)
            #   – otherwise the detected context window (option 1)
            if model_cached_ctx:
                choice = "2"  # Last Used Context Window
            else:
                choice = "1"  # Detected Context Window
    except (EOFError, KeyboardInterrupt):
        print("\n👋 Cancelled by user.")
        return default_ctx

    try:
        choice_idx = int(choice)
        if choice_idx == 0:
            return default_ctx
        elif 1 <= choice_idx <= len(options):
            label, value, source = options[choice_idx - 1]
            if source == "custom":
                custom_ctx = input(f"Enter custom context window [{value:,}]: ").strip()
                if not custom_ctx:
                    context_window = value
                else:
                    context_window = int(custom_ctx.replace(",", "").replace("_", ""))
                print(f"   ✅ Using custom: {context_window:,} tokens")
            else:
                context_window = value
                print(f"   ✅ Selected: {label} = {context_window:,} tokens")

            # Save to model-specific cache
            save_model_context(selected_model, context_window)
            return context_window
        else:
            print(f"   ⚠️  Invalid selection, using default: {default_ctx:,}")
            return default_ctx
    except (ValueError, KeyboardInterrupt):
        print(f"\n   Using default: {default_ctx:,} tokens")
        return default_ctx

def check_model_access(selected_model, api_key):
    """Check model access using the litellm proxy test step.
    This validates that the selected model works via the local proxy.
    Any 200 response from the proxy counts as "accessible".
    """
    print(f"   🔍 Checking access to {selected_model}...")
    return test_proxy_connection(selected_model)


# ─── Step 6: Generate litellm proxy config ───────────────────────────────
def generate_litellm_config(selected_model, api_key, context_window=None):
    """Write a litellm PROXY config mapping the model to OpenCode's API.

    Claude Code speaks the Anthropic Messages API (/v1/messages), but OpenCode
    exposes an OpenAI-compatible API at https://opencode.ai/zen/v1. Use the
    `openai/` provider prefix with that custom api_base — the `opencode/`
    prefix is NOT a valid litellm provider and breaks /v1/messages routing.

    For free models (no API key), we must omit the Authorization header entirely.
    litellm requires an api_key but we can override with extra_headers to send
    an empty Authorization header. Free models are identified by the "-free" suffix
    or being "big-pickle".

    IMPORTANT: By default, litellm routes Anthropic /v1/messages to OpenAI's
    Responses API for the "openai" provider. We must set
    `use_chat_completions_url_for_anthropic_messages: true` to force it to use
    chat/completions instead, which is what OpenCode Zen expects.

    Context window: User-provided (from prompt) or curated fallback.
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Determine if this is a free model that doesn't need auth
    is_free_model = (
        selected_model == "big-pickle"
        or selected_model.endswith("-free")
        or (api_key is None or api_key == "")
    )

    params = {
        "model": f"openai/{selected_model}",
        "api_base": OPENCODE_ZEN_BASE_URL,
    }

    if is_free_model:
        # For free models: provide dummy api_key (litellm requires it) but
        # override Authorization header to empty string so no auth is sent
        params["api_key"] = "dummy"  # litellm requires this field
        params["extra_headers"] = {"Authorization": ""}
    elif api_key:
        # For paid models with valid API key
        params["api_key"] = api_key

    # Use user-provided context window, or fall back to curated mapping
    if context_window is None or context_window <= 0:
        # Known context windows for OpenCode Zen free models (curated fallback)
        # OpenCode API does not return context window info
        # User-verified: All OpenCode free tier models have >= 200K, most 1M context
        CONTEXT_WINDOWS = {
            "big-pickle": 1048576,
            "hy3-free": 1048576,
            "laguna-s-2.1-free": 1048576,      # Verified: max_position_embeddings=1048576
            "ling-3.0-flash-fin-free": 1048576,
            "deepseek-v4-flash-free": 1048576,
            "nemotron-3-ultra-free": 1048576,  # User-verified: 1M on OpenCode free tier
            "muse-spark-1.2-contributor-free": 1048576,
            "muse-spark-1.3-contributor-free": 1048576,
            "mimo-v2.5-free": 1048576,
            "nemotron-3.5-lightning-free": 1048576,  # Verified: NIM version = 1M
        }
        context_window = CONTEXT_WINDOWS.get(selected_model, 200000)
        print(f"   📏 Context window (curated fallback): {context_window:,} tokens")
    else:
        print(f"   📏 Context window (user-specified): {context_window:,} tokens")

    model_info = {
        "mode": "chat",
        "max_tokens": context_window,
        "max_input_tokens": context_window,
    }

    config = {
        "model_list": [
            {
                "model_name": selected_model,
                "litellm_params": params,
                "model_info": model_info,
            }
        ],
        "general_settings": {"master_key": PROXY_MASTER_KEY, "store_model_in_db": False},
        "litellm_settings": {
            "drop_params": True,
            "use_chat_completions_url_for_anthropic_messages": True,
        },
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
    # Determine if workspace venv exists
    venv_exists = os.path.isdir("/workspace/.venv")
    litellm_bin = None

    # 1. Try workspace venv litellm binary (if venv exists)
    if venv_exists:
        venv_litellm = "/workspace/.venv/bin/litellm"
        if os.path.isfile(venv_litellm) and os.access(venv_litellm, os.X_OK):
            litellm_bin = venv_litellm

    # 2. If venv binary not usable, try installing litellm[proxy] into venv
    if litellm_bin is None and venv_exists:
        venv_python = "/workspace/.venv/bin/python3"
        if os.path.isfile(venv_python) and os.access(venv_python, os.X_OK):
            print("   📦 Ensuring litellm[proxy] is installed in venv...")
            subprocess.run(
                [venv_python, "-m", "pip", "install", "-q", "litellm[proxy]"],
                capture_output=True, timeout=120,
            )
            venv_litellm = "/workspace/.venv/bin/litellm"
            if os.path.isfile(venv_litellm) and os.access(venv_litellm, os.X_OK):
                litellm_bin = venv_litellm

    # 3. Fall back to shutil.which for system-installed litellm
    if litellm_bin is None:
        import shutil
        bin_path = shutil.which("litellm")
        if bin_path:
            litellm_bin = bin_path

    # 4. Last resort: install litellm[proxy] system-wide
    if litellm_bin is None:
        print("   📦 Installing litellm[proxy] system-wide...")
        subprocess.run(
            ["pip", "install", "--break-system-packages", "-q", "litellm[proxy]"],
            capture_output=True, timeout=180,
        )
        bin_path = shutil.which("litellm")
        if bin_path:
            litellm_bin = bin_path

    # 5. Absolute fallback: if still nothing, try installing and check again
    if litellm_bin is None:
        print("   📦 Ensuring litellm is available...")
        # Install litellm base package system-wide
        subprocess.run(
            ["pip", "install", "--break-system-packages", "-q", "litellm"],
            capture_output=True, timeout=180,
        )
        bin_path = shutil.which("litellm")
        if bin_path:
            litellm_bin = bin_path

    with open(LOG_FILE, "w") as logf:
        if litellm_bin is None:
            print("   ❌ Could not find or install litellm CLI binary")
            print("   Please install litellm manually: pip install litellm[proxy]")
            return None
        proc = subprocess.Popen(
            [litellm_bin, "--config", CONFIG_FILE, "--port", str(PROXY_PORT)],
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
        "model": selected_model,
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
                    # Ensure the workspace statusline.sh is executable
                    if os.path.isfile(workspace_statusline):
                        os.chmod(workspace_statusline, 0o755)
                    print(f"✅ statusline.sh symlink already correct at {statusline_dst}")
                    return
            except OSError:
                pass
            # Symlink exists but wrong target - remove and recreate
            os.unlink(statusline_dst)
        elif os.path.exists(statusline_dst):
            # It's a regular file (not symlink) - remove it to create symlink
            # But first check if it's the same as workspace/statusline.sh (by content/inode)
            try:
                if os.path.samestat(os.stat(statusline_dst), os.stat(workspace_statusline)):
                    # Ensure the workspace statusline.sh is executable
                    if os.path.isfile(workspace_statusline):
                        os.chmod(workspace_statusline, 0o755)
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
            # Ensure the workspace statusline.sh is executable
            if os.path.isfile(workspace_statusline):
                os.chmod(workspace_statusline, 0o755)
            os.symlink(workspace_statusline, statusline_dst)
            print(f"📐 Symlinked workspace statusline.sh → {statusline_dst}")
        except OSError as e:
            print(f"⚠️  Could not create symlink: {e}")

        # Now configure the statusLine in settings.json
        settings_file = os.path.join(CLAUDE_CONFIG_DIR, "settings.json")
        resolved_statusline = os.path.realpath(statusline_dst)
        statusline_command = f'ZEN_STATUSLINE_MODE=full bash {resolved_statusline}'

        try:
            # Read existing settings
            if os.path.exists(settings_file):
                with open(settings_file, "r") as f:
                    settings = json.load(f)
            else:
                settings = {}

            # Update or add statusLine configuration
            settings["statusLine"] = {
                "type": "command",
                "command": statusline_command
            }

            # Write back
            with open(settings_file, "w") as f:
                json.dump(settings, f, indent=2)

            print(f"⚙️  Configured statusLine in {settings_file}")
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️  Could not configure statusLine: {e}")
    else:
        print(f"⚠️  workspace statusline.sh not found at {workspace_statusline}")


def launch_claude_with_model(selected_model, context_window, dangerously_skip_permissions=False, compaction_threshold=None):
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
    env["ANTHROPIC_MODEL"] = selected_model
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = selected_model
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_MODEL_CALLS"] = "1"
    env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
    # Set max context tokens for the model (from user selection)
    env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(context_window)
    # Don't let claude try to discover/switch to a gateway model.
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "0"
    # Enable auto-compaction when context usage reaches the configured threshold.
    # Use the per-model cached value if provided, else the default.
    compaction_value = compaction_threshold if compaction_threshold is not None else AUTO_COMPACTION_THRESHOLD
    env["CLAUDE_CODE_COMPACTION_LEVEL"] = str(compaction_value)
    # Set provider env var for statusline.sh - use the provider indicator from config
    env["CLAUDE_CODE_PROVIDER"] = "openai"
    # Set statusline mode env var
    # Read from cache; if not set, statusline.sh will default based on JSON payload
    statusline_mode = load_statusline_mode()
    if statusline_mode == "compact":
        env["CLAUDE_CODE_STATUSLINE_MODE"] = "compact"
    elif statusline_mode == "full":
        env["CLAUDE_CODE_STATUSLINE_MODE"] = "full"
    # If no cached mode, leave unset and statusline.sh will use its normal logic

    # Set up Claude Code persistence so sessions survive devcontainer rebuilds
    setup_claude_persistence()

    # Set up statusline.sh symlink so Claude Code uses our custom status line
    setup_statusline_symlink()

    print("\n🚀 Launching Claude Code with selected OpenCode Zen model...")
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
    print("  OpenCode Zen → Claude Code Bridge Script")
    print("=" * 60)
    print()
    print("📋  SCRIPT PURPOSE:")
    print("   This script bridges OpenCode Zen models with the Claude Code CLI.")
    print("   It fetches available OpenCode chat models, lets you select one,")
    print("   runs a local litellm proxy (OpenCode→Anthropic translation),")
    print("   then launches Claude Code configured to use that OpenCode model.")
    print()
    print("🔑  API KEY SETUP:")
    print("   • Set OPENCODE_API_KEY environment variable export")
    print("     OPENCODE_API_KEY='sk-...'")
    print("   • Or run the script once - it will prompt and cache the key")
    print("     to ~/.opencode_api_key_cache for future runs.")
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
    print("   OpenCode exposes its own API endpoint.")
    print("   A local litellm proxy translates between them so conversation")
    print("   and tool calls work against the OpenCode model.")
    print()
    print("   • ANTHROPIC_BASE_URL=http://127.0.0.1:<port>  (litellm proxy)")
    print("   • ANTHROPIC_AUTH_TOKEN=sk-claude-bridge  (proxy master key)")
    print("   • ANTHROPIC_MODEL=<selected_model>  (actual model ID, e.g. big-pickle)")
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
        description="Bridge OpenCode Zen models with the Claude Code CLI"
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        default=False,
        help="Skip Claude Code permission prompts (safe in devcontainer environments)",
    )
    parser.add_argument(
        "--clear-api-key",
        action="store_true",
        default=False,
        help="Clear the cached OpenCode API key and prompt again",
    )
    parser.add_argument(
        "--accept-all-defaults",
        action="store_true",
        default=False,
        help="Auto-accept cached/default values for all prompts (quick re-launch)",
    )
    args = parser.parse_args()

    print_usage_notes(dangerously_skip_permissions=args.dangerously_skip_permissions)

    if not ensure_prerequisites():
        print("❌ Prerequisites check failed. Exiting.")
        sys.exit(1)

    # Get API key exactly once. --clear-api-key removes the cached key first.
    api_key = get_api_key(clear=args.clear_api_key)

    print("\n🔄 Fetching model list from OpenCode API...")
    all_raw_models = fetch_models(api_key)

    standard, free, combined = categorize_models(all_raw_models)
    selected_model, model_data = display_and_select(standard, free, combined)

    # Prompt for context window
    context_window = get_context_window(selected_model, model_data)

    # Prompt for auto-compaction threshold after context window is selected.
    # Default = cached per-model value if available, else AUTO_COMPACTION_THRESHOLD.
    # User can press ENTER to accept the default, or type a custom 0-100 value.
    cached_compaction = load_model_compaction(selected_model)
    default_compaction = cached_compaction if cached_compaction is not None else AUTO_COMPACTION_THRESHOLD
    try:
        if args.accept_all_defaults:
            compaction_input = ""
        else:
            compaction_input = input(f"\n🗜️  Auto-Compaction Threshold % [0-100, default: {default_compaction}% (ENTER to accept, custom number to set)]: ").strip()
        if not compaction_input:
            context_window_compaction = default_compaction
        else:
            try:
                compaction_val = int(compaction_input)
                if 0 <= compaction_val <= 100:
                    context_window_compaction = compaction_val
                else:
                    print(f"   ⚠️  Value must be 0-100, using default: {default_compaction}%")
                    context_window_compaction = default_compaction
            except ValueError:
                print(f"   ⚠️  Invalid number, using default: {default_compaction}%")
                context_window_compaction = default_compaction

        # Cache the per-model compaction setting for next time
        save_model_compaction(selected_model, context_window_compaction)

        print(f"   ✅ Auto-Compaction Threshold set to {context_window_compaction}%")
    except (EOFError, KeyboardInterrupt):
        context_window_compaction = default_compaction
        print(f"   Using default auto-compaction: {default_compaction}%")

    # === NEW: Prompt for statusline mode after auto-compaction ===
    # Default to cached value if available, otherwise fall back to 'full'
    cached_mode = load_statusline_mode()
    default_mode = cached_mode if cached_mode in ("full", "compact") else "full"
    if args.accept_all_defaults:
        selected_mode = default_mode
        print(f"   ✅ Using cached statusline mode: {selected_mode} (accept-all-defaults)")
    else:
        mode_input = input(f"\n📏 Statusline Mode [full/2-line or compact/1-line, default: {default_mode} (ENTER to accept)]: ").strip()
        if not mode_input:
            selected_mode = default_mode
        elif mode_input in ("full", "compact"):
            selected_mode = mode_input
        else:
            selected_mode = default_mode
    # Save the chosen mode for next time
    save_statusline_mode(selected_mode)
    # End new section

    # Set provider env var for statusline.sh
    # Use PROVIDER_INDICATOR or derive from API base URL
    provider_name = PROVIDER_INDICATOR  # "opencode" by default, but could be overridden

    # Generate config and start the proxy FIRST, then validate the connection
    # through the proxy. (check_model_access requires a live proxy, so it can't
    # run before start_proxy().)
    generate_litellm_config(selected_model, api_key, context_window)
    proc = start_proxy()

    try:
        if not test_proxy_connection(selected_model):
            print("\n❌ Proxy validation failed. Claude Code likely won't work.")
            print("   Check ~/.claude_opencode/proxy.log for details.")
            print("   You may still attempt to launch manually.")
        launch_claude_with_model(selected_model, context_window, args.dangerously_skip_permissions, context_window_compaction)
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