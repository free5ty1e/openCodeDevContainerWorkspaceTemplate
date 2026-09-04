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
# TODO: Replace these with your actual provider's base URL and endpoints
ANYAPI_BASE_URL = "https://api.anyapi.example/v1"
ANYAPI_MODELS_ENDPOINT = f"{ANYAPI_BASE_URL}/models"
ANYAPI_CHAT_ENDPOINT_TEMPLATE = f"{ANYAPI_BASE_URL}/models/{{model}}:chat"
CACHE_FILE = os.path.expanduser("~/.anyapi_api_key_cache")
MODEL_CACHE_FILE = os.path.expanduser("~/.claude_anyapi_last_model")
CONTEXT_CACHE_FILE = os.path.expanduser("~/.claude_anyapi_last_context")
PROXY_PORT = 4502
PROXY_MASTER_KEY = "sk-anyapi-bridge"
CONFIG_DIR = os.path.expanduser("~/.claude_anyapi")
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
    """Return a valid API key, prompting if needed and caching it.
    If `clear` is True, the cached key is removed and the user is prompted again.
    If the user provides an empty key, anonymous mode is used.
    """
    # Handle clear flag
    if clear and os.path.exists(CACHE_FILE):
        try:
            os.remove(CACHE_FILE)
            print(f"🗑️  Cleared cached API key at {CACHE_FILE}.")
        except OSError:
            print(f"⚠️  Failed to delete cached API key at {CACHE_FILE}.")

    API_KEY = os.environ.get("ANYAPI_API_KEY")

    if API_KEY:
        print("✅ API key found in environment.")
        return API_KEY

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cached = f.read().strip()
            if cached:
                print(f"✅ Found cached API key in {CACHE_FILE}.")
                os.environ["ANYAPI_API_KEY"] = cached
                return cached
        except OSError:
            print(f"⚠️  Could not read cached API key from {CACHE_FILE}.")

    print("🔑 API key not found.")
    try:
        api_key = input("   Please enter your API key (or press Enter for anonymous): ").strip()
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

    os.environ["ANYAPI_API_KEY"] = api_key
    return api_key


# ─── Step 3: Fetch models from anyAPI ────────────────────────────────────
def http_get_json(url, api_key):
    """GET JSON from the anyAPI provider.
    If api_key is empty, no Authorization header is sent (anonymous mode).
    """
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    # Build URL with key if needed
    sep = "&" if "?" in url else "?"
    authed_url = f"{url}{sep}key={api_key}" if api_key else url
    req = urllib.request.Request(authed_url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_models(api_key):
    """Fetch the list of available models from the anyAPI provider.

    The provider is expected to return a JSON response with a top-level
    "models" array (or "data" like OpenAI), where each model has at least
    an "id" field and optionally "owned_by" and context window fields
    (e.g., "input_token_limit", "max_tokens", "context_length", etc.).

    Free/community models should have "owned_by": "community" or the id
    should contain "free".

    All models are returned in a combined list with free-tier models
    sorted to the bottom (mirroring the behaviour of the NVIDIA/OpenCode/Google scripts).
    """
    try:
        data = http_get_json(ANYAPI_MODELS_ENDPOINT, api_key)
        # Support both "models" and "data" top-level keys
        raw = data.get("models", data.get("data", []))
        models = []
        for m in raw:
            model_id = m.get("id", "")
            if not model_id:
                continue
            owned_by = m.get("owned_by", "").lower()
            model_id_lower = model_id.lower()

            # Non-chat keywords filter
            NON_CHAT_KEYWORDS = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"]
            if any(keyword in model_id_lower for keyword in NON_CHAT_KEYWORDS):
                continue

            is_free = (
                "community" in owned_by
                or "free" in model_id_lower
            )

            # Try to extract context window from the API response
            # Common field names across providers:
            context_window = (
                m.get("input_token_limit")
                or m.get("inputTokenLimit")
                or m.get("max_tokens")
                or m.get("maxTokens")
                or m.get("context_length")
                or m.get("contextLength")
                or 0
            )

            models.append({"id": model_id, "owned_by": owned_by, "is_free": is_free, "context_window": context_window})
        return models
    except Exception as e:
        print(f"❌ Failed to retrieve models: {e}")
        sys.exit(1)


# ─── Step 4: Categorize, filter, and sort models ────────────────────────
def categorize_models(all_raw_models):
    """Categorize models into standard and free/tier, sorted with free at bottom.

    Free models are sorted and separated at the bottom of the selector list.
    Returns full model objects (with context_window) for display.
    """
    NON_CHAT_KEYWORDS = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"]
    FREE_KEYWORDS = ["community", "free"]

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


def arrow_key_selector(options, prompt="Select an option:", start_idx=0):
    """
    Interactive arrow-key selector using prompt_toolkit.

    Returns (selected_index, selected_option) or (None, None) on cancel.
    Supports UP/DOWN arrows, PAGE_UP/PAGE_DOWN, HOME/END, ENTER, ESC.
    The highlighted item is always kept visible via auto-scrolling.
    """
    if not options:
        return None, None

    terminal_height = _terminal_height()
    visible_count = max(3, min(terminal_height - 4, len(options)))
    # Clamp start_idx to valid range
    start_idx = max(0, min(start_idx, len(options) - 1))
    current = [start_idx]  # use list for closure mutability
    result = [None]  # use list for closure mutability

    def get_top_idx():
        """Calculate the top visible index so current selection is always visible."""
        top = current[0] - (current[0] % visible_count)
        top = max(0, min(top, max(0, len(options) - visible_count)))
        if current[0] < top:
            top = current[0]
        elif current[0] >= top + visible_count:
            top = current[0] - visible_count + 1
        return top

    def get_formatted_options():
        """Return formatted text for the list, recalculated on each render.

        Returns a list of (style, text) tuples — the format expected by
        FormattedTextControl. Each line ends with a newline so the control
        renders multi-line content correctly.
        """
        top = get_top_idx()
        fragments = []

        # Prompt + instructions
        fragments.append(("class:prompt", prompt + "\n"))
        fragments.append(("class:hint", "  ↑/↓ navigate • PgUp/PgDn page • Home/End jump • Enter select • Esc cancel\n"))

        items_above = top
        items_below = len(options) - (top + visible_count)

        for i in range(top, min(top + visible_count, len(options))):
            if i == current[0]:
                fragments.append(("class:current", f"  → {options[i]}\n"))
            else:
                fragments.append(("class:normal", f"    {options[i]}\n"))

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
        if current[0] < len(options) - 1:
            current[0] += 1

    @kb.add("pageup")
    def _(event):
        page = min(visible_count - 1, len(options))
        current[0] = max(0, current[0] - page)

    @kb.add("pagedown")
    def _(event):
        page = min(visible_count - 1, len(options))
        current[0] = min(len(options) - 1, current[0] + page)

    @kb.add("home")
    def _(event):
        current[0] = 0

    @kb.add("end")
    def _(event):
        current[0] = len(options) - 1

    @kb.add("enter")
    def _(event):
        result[0] = current[0]
        event.app.exit()

    @kb.add("escape")
    def _(event):
        result[0] = None
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
    print("       ANYAPI CHAT MODELS               ")
    print("========================================")

    current_number = 1
    if standard:
        print("\n--- Standard Chat Models ---")
        for model_obj in standard:
            model_id = model_obj.get("id", "")
            ctx = model_obj.get("context_window", 0)
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
            print(f"[{current_number}] {model_id}{ctx_str}")
            current_number += 1
    if free:
        print("\n--- Free & Community Tier ---")
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
    if last_model:
        # Find the index of last_model
        last_idx = None
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

    # Use arrow key selector
    print("\nUse ↑/↓ arrows to navigate, Enter to select:")
    selected_idx, _ = arrow_key_selector(display_options, "Select a model:", start_idx=last_idx if last_idx is not None else 0)
    if selected_idx is None:
        print("\n👋 No model selected. Exiting.")
        sys.exit(0)
    selected_model = combined[selected_idx].get("id", "")
    print(f"\n🚀 Selected Model: {selected_model}")

    # Save last model
    save_last_model(selected_model)

    return selected_model, combined[selected_idx]


def get_context_window(selected_model, model_data):
    """Prompt user for context window with pre-populated default."""
    # Get default from model data or cache
    model_ctx = model_data.get("context_window", 0)
    cached_ctx = load_last_context()

    # Priority: cached context > model data context > default 200000
    if cached_ctx and cached_ctx > 0:
        default_ctx = cached_ctx
        source = "cached"
    elif model_ctx and model_ctx > 0:
        default_ctx = model_ctx
        source = "model default"
    else:
        default_ctx = 200000
        source = "fallback"

    print(f"\n📏 Context Window Configuration")
    print(f"   Model default: {model_ctx:,} tokens" if model_ctx > 0 else "   Model default: unknown")
    print(f"   Last used: {cached_ctx:,} tokens" if cached_ctx and cached_ctx > 0 else "   Last used: none")
    print(f"   Using: {default_ctx:,} tokens ({source})")

    try:
        user_input = input(f"\nContext window in tokens [{default_ctx:,}]: ").strip()
    except EOFError:
        print(f"\n   Using default: {default_ctx:,} tokens")
        return default_ctx
    except KeyboardInterrupt:
        print("\n👋 Cancelled by user.")
        sys.exit(0)

    if not user_input:
        context_window = default_ctx
        print(f"   ✅ Using {context_window:,} tokens")
    else:
        try:
            context_window = int(user_input.replace(",", "").replace("_", ""))
            if context_window <= 0:
                print(f"   ⚠️  Invalid value, using default: {default_ctx:,}")
                context_window = default_ctx
            else:
                print(f"   ✅ Using custom context window: {context_window:,} tokens")
        except ValueError:
            print(f"   ⚠️  Invalid value, using default: {default_ctx:,}")
            context_window = default_ctx

    # Save for next run
    save_last_context(context_window)
    return context_window


def check_model_access(selected_model, api_key):
    """Verify the selected model is usable with the given API key.

    Some models are not accessible to every account. This catches that early
    so the user can pick a different model instead of failing inside Claude Code.
    Returns True if the model responds, False otherwise.
    """
    print(f"   🔍 Checking access to {selected_model}...")
    # Use the proxy test – it validates that the model works via the local proxy.
    return test_proxy_connection(selected_model)


# ─── Step 6: Generate litellm proxy config ───────────────────────────────
def generate_litellm_config(selected_model, api_key, context_window=None):
    """Write a litellm PROXY config mapping the model to the anyAPI provider.

    Claude Code speaks the Anthropic Messages API (/v1/messages), but the
    anyAPI provider exposes an OpenAI-compatible API. Use the `openai/`
    provider prefix with the anyAPI custom api_base — `anyapi_` is NOT a
    valid litellm provider prefix and breaks /v1/messages routing.
    If api_key is empty (anonymous mode), omit it from the config.

    Includes model_info with context window so Claude Code can use the full
    context size of each model. Context window should be dynamically fetched
    from the provider's models API (see fetch_models()).
    """
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # Use user-provided context window, or fall back to API/fetch
    if context_window is None or context_window <= 0:
        # Re-fetch models to get the context window for the selected model
        # (In production, you might want to pass the model data through to avoid
        # a second API call, but this ensures we have the latest info)
        models = fetch_models(api_key)
        selected_model_data = next((m for m in models if m["id"] == selected_model), None)

        if selected_model_data and selected_model_data.get("context_window", 0) > 0:
            context_window = selected_model_data["context_window"]
            print(f"   📏 Context window from API: {context_window:,} tokens")
        else:
            # Fallback: provider-specific mapping goes here
            # CUSTOMIZE THIS FOR YOUR PROVIDER
            CONTEXT_WINDOWS = {
                # Example: "model-name": 1000000,
            }
            context_window = CONTEXT_WINDOWS.get(selected_model, 4096)
            print(f"   ⚠️  Using fallback context window: {context_window:,} tokens")
    else:
        print(f"   📏 Context window (user-specified): {context_window:,} tokens")

    model_info = {
        "mode": "chat",
        "max_tokens": context_window,
        "max_input_tokens": context_window,
    }

    params = {
        "model": f"openai/{selected_model}",
        "api_base": ANYAPI_BASE_URL,
    }
    if api_key:
        params["api_key"] = api_key

    config = {
        "model_list": [
            {
                "model_name": selected_model,
                "litellm_params": params,
                "model_info": model_info,
            }
        ],
        "general_settings": {"master_key": PROXY_MASTER_KEY, "store_model_in_db": False},
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
    devcontainer rebuilds.
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


def launch_claude_with_model(selected_model, context_window, dangerously_skip_permissions=False):
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

    # Set up Claude Code persistence so sessions survive devcontainer rebuilds
    setup_claude_persistence()

    # Set up statusline.sh symlink so Claude Code uses our custom status line
    setup_statusline_symlink()

    print("\n🚀 Launching Claude Code with selected anyAPI model...")
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
    print("  AnyAPI → Claude Code Bridge Script")
    print("=" * 60)
    print()
    print("📋  SCRIPT PURPOSE:")
    print("   This script bridges anyAPI models with the Claude Code CLI.")
    print("   It fetches available anyAPI chat models, lets you select one,")
    print("   runs a local litellm proxy (anyAPI→Anthropic translation),")
    print("   then launches Claude Code configured to use that anyAPI model.")
    print()
    print("🔑  API KEY SETUP:")
    print("   • Set ANYAPI_API_KEY environment variable export")
    print("     ANYAPI_API_KEY='sk-...'")
    print("   • Or run the script once - it will prompt and cache the key")
    print("     to ~/.anyapi_api_key_cache for future runs.")
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
    print("   anyAPI exposes its own API endpoint.")
    print("   A local litellm proxy translates between them so conversation")
    print("   and tool calls work against the anyAPI model.")
    print()
    print("   • ANTHROPIC_BASE_URL=http://127.0.0.1:<port>  (litellm proxy)")
    print("   • ANTHROPIC_AUTH_TOKEN=sk-claude-bridge  (proxy master key)")
    print("   • ANTHROPIC_MODEL=<selected_model>  (actual model ID from your provider)")
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
        description="Bridge anyAPI models with the Claude Code CLI"
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
        help="Clear the cached API key and prompt again",
    )
    args = parser.parse_args()

    print_usage_notes(dangerously_skip_permissions=args.dangerously_skip_permissions)

    if not ensure_prerequisites():
        print("❌ Prerequisites check failed. Exiting.")
        sys.exit(1)

    api_key = get_api_key(clear=args.clear_api_key)

    print("\n🔄 Fetching model list from anyAPI...")
    all_raw_models = fetch_models(api_key)

    standard, free, combined = categorize_models(all_raw_models)
    selected_model, model_data = display_and_select(standard, free, combined)

    # Prompt for context window
    context_window = get_context_window(selected_model, model_data)

    # Generate config and start the proxy FIRST, then validate the connection
    # through the proxy. (check_model_access requires a live proxy, so it can't
    # run before start_proxy().)
    generate_litellm_config(selected_model, api_key, context_window)
    proc = start_proxy()

    try:
        if not test_proxy_connection(selected_model):
            print("\n❌ Proxy validation failed. Claude Code likely won't work.")
            print("   Check ~/.claude_anyapi/proxy.log for details.")
            print("   You may still attempt to launch manually.")
        launch_claude_with_model(selected_model, context_window, args.dangerously_skip_permissions)
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