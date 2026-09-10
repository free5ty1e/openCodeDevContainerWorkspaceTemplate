#!/usr/bin/env python3
"""NVIDIA NIM → Claude Code Bridge Script

Bridges NVIDIA NIM models with the Claude Code CLI via a local litellm proxy.

**IMPORTANT**: This script is designed to run from the workspace virtualenv at /workspace/.venv/.
If running from a different python, it will attempt to use the workspace
virtualenv at /workspace/.venv/ for all pip operations and the litellm binary.

**Running from the system python3 (without the workspace virtualenv) may result in missing
dependencies (notably the prisma module for the litellm proxy).**
"""
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

# ── Workspace virtualenv detection ──────────────────────────────────────────────
# Ensure we're using the workspace virtualenv if it exists.
def _get_venv_python():
    """Return the workspace virtualenv python executable, if it exists."""
    venv_py = "/workspace/.venv/bin/python3"
    if os.path.isfile(venv_py) and os.access(venv_py, os.X_OK):
        return venv_py
    return sys.executable

def _get_venv_litellm():
    """Return the workspace venv litellm binary, if it exists."""
    litellm_bin = "/workspace/.venv/bin/litellm"
    if os.path.isfile(litellm_bin) and os.access(litellm_bin, os.X_OK):
        return litellm_bin
    return None

def _get_venv_pip():
    """Return the workspace venv pip executable, if it exists."""
    pip_bin = "/workspace/.venv/bin/pip"
    if os.path.isfile(pip_bin) and os.access(pip_bin, os.X_OK):
        return pip_bin
    return shutil.which("pip") or shutil.which("pip3")

# Check if we need to switch to the venv
if _get_venv_python() != sys.executable:
    # Not already running from the venv — switch to it.
    # We re-exec the interpreter so that all subsequent imports/use of pip etc.
    # use the virtual environment.
    os.execv(_get_venv_python(), [_get_venv_python()] + sys.argv)
    # The above call never returns, but if it does, fall through
# ── End workspace virtualenv detection ─────────────────────────────────────────

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

# Use the workspace virtualenv python for all pip operations and the litellm binary.
# Fall back to the system python if the venv is not available.
_VENV_PYTHON = "/workspace/.venv/bin/python3"
_VENV_LITELLM = "/workspace/.venv/bin/litellm"
_VENV_PIP = "/workspace/.venv/bin/pip"

def _get_venv_python():
    """Return the workspace virtualenv python executable, if it exists."""
    if os.path.isfile(_VENV_PYTHON) and os.access(_VENV_PYTHON, os.X_OK):
        return _VENV_PYTHON
    return sys.executable

def _get_venv_litellm():
    """Return the workspace venv litellm binary, if it exists."""
    if os.path.isfile(_VENV_LITELLM) and os.access(_VENV_LITELLM, os.X_OK):
        return _VENV_LITELLM
    return None

def _get_venv_pip():
    """Return the workspace venv pip executable, if it exists."""
    if os.path.isfile(_VENV_PIP) and os.access(_VENV_PIP, os.X_OK):
        return _VENV_PIP
    return shutil.which("pip") or shutil.which("pip3")

# Import shared library
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_library import (
    # Prerequisites
    ensure_prerequisites,
    # API Key management
    get_and_cache_api_key,
    cache_api_key,
    # Model utilities
    filter_chat_models,
    format_token_count,
    # HTTP
    http_get_json,
    # Proxy management
    start_litellm_proxy,
    stop_running_proxy,
    test_proxy_connection,
    # Cache
    read_cache,
    write_cache,
    # Context window
    load_model_context,
    save_model_context,
    # Compaction
    load_model_compaction,
    save_model_compaction,
    # Model utilities
    categorize_models,
    # Context window (dynamic NVIDIA catalog scrape)
    fetch_nvidia_context_windows,
    # Statusline mode
    load_statusline_mode,
    save_statusline_mode,
    # Favorites
    load_favorites,
    save_favorites,
    # Terminal
    get_terminal_height,
    # Persistence & statusline
    setup_claude_persistence,
    setup_statusline_symlink,
)

# ─── Configuration ─────────────────────────────────────────────────────────────
NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

CACHE_FILE = os.path.expanduser("~/.nvidia_api_key_cache")
MODEL_CACHE_FILE = os.path.expanduser("~/.claude_nvidia_last_model")
FAVORITES_CACHE_FILE = os.path.expanduser("~/.claude_nvidia_favorites")
CONTEXT_CACHE_FILE = os.path.expanduser("~/.claude_nvidia_last_context")
CONTEXT_WINDOW_CACHE_DIR = os.path.expanduser("~/.claude_nvidia_context_windows")
CONTEXT_WINDOW_SCRAPE_CACHE = os.path.expanduser("~/.claude_nvidia_scraped_context.json")
STATUSLINE_MODE_CACHE_FILE = os.path.expanduser("~/.claude_nvidia_statusline_mode")

PROXY_PORT = 4499  # NVIDIA (default) — overridable via CLAUDE_BRIDGE_PORT
PROXY_PORT = int(os.environ.get("CLAUDE_BRIDGE_PORT", PROXY_PORT))
PROXY_MASTER_KEY = "sk-claude-bridge"
CONFIG_DIR = os.path.expanduser("~/.claude_nvidia")
CONFIG_FILE = os.path.join(CONFIG_DIR, "litellm_proxy.yaml")
LOG_FILE = os.path.join(CONFIG_DIR, "proxy.log")
PID_FILE = os.path.join(CONFIG_DIR, "proxy.pid")
AUTO_COMPACTION_THRESHOLD = 91
PROVIDER_INDICATOR = "nvidia"

# Context window mapping for display (NVIDIA API doesn't return this)
# This curated map covers every NVIDIA NIM model currently in the catalog,
# with values sourced from:
#   • live scrapes of build.nvidia.com (models in the scrape results)
#   • NVIDIA documentation / model cards (Nemotron Ultra = 4K, 3.5 Lightning = 1M, etc.)
#   • authoritative online references (mistral-large = 32K, etc.)
CONTEXT_WINDOWS = {
    # Models that were scraped from build.nvidia.com — these will be overridden
    # by live scrape results on first run, but are kept here as fallback.
    "nvidia/nemotron-3-super-120b-a12b": 1048576,
    "nvidia/nemotron-3-ultra-550b-a55b": 1048576,
    "nvidia/nemotron-3.5-lightning-30b-a3b": 1048576,  # Verified: NIM version = 1M
    "moonshotai/kimi-k3": 1048576,
    "deepseek-ai/deepseek-v4-pro-0813": 1048576,
    "deepseek-ai/deepseek-v4-flash-0731": 1048576,
    "poolside/laguna-xs-2.1": 262144,
    "meta/muse-glimmer-30b": 131072,
    "google/gemma-4-31b-it": 262144,
    # Additional NVIDIA NIM models with verified context windows (from daaff93 commit research):
    "nvidia/nemotron-3.5-content-safety": 131072,
    "nvidia/nemotron-3-embed-1b": 32768,
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning": 262144,
    "nvidia/llama-nemotron-embed-vl-1b-v2": 16384,
    "openai/gpt-oss-20b": 131072,
    # Full NVIDIA catalog coverage — see scrape results at /tmp/nvidia_ctx.json
    # Additional verified values from NVIDIA docs/model cards:
    "nvidia/nemotron-4-340b-instruct": 4096,
    "nvidia/nemotron-4-340b-reward": 4096,
    "nvidia/nemotron-nano-3-30b-a3b": 32768,
    "nvidia/neva-22b": 16384,
    # Meta / Llama family (NVIDIA-hosted):
    "meta/llama-3.2-11b-vision-instruct": 131072,
    "meta/llama-3.2-90b-vision-instruct": 131072,
    "meta/llama-guard-4-12b": 163840,
    # Mistral family (NVIDIA-hosted):
    "mistralai/mistral-large": 32768,
    "mistralai/mistral-large-2-instruct": 32768,
    "mistralai/mistral-nemotron": 262144,
    "mistralai/mistral-7b-instruct-v0.3": 8192,
    "mistralai/mistral-nemo-minitron-8b-8k-instruct": 8192,
    # IBM Granite (NVIDIA-hosted):
    "ibm/granite-3.0-3b-a800m-instruct": 32768,
    "ibm/granite-3.0-8b-instruct": 32768,
    "ibm/granite-34b-code-instruct": 32768,
    "ibm/granite-8b-code-instruct": 8192,
    # Google / Gemma (NVIDIA-hosted):
    "google/codegemma-1.1-7b": 8192,
    "google/codegemma-7b": 8192,
    "google/gemma-2b": 8192,
    "google/gemma-3-12b-it": 131072,
    "google/gemma-3-4b-it": 131072,
    "google/gemma-3-12b-it": 131072,
    # Microsoft Phi (NVIDIA-hosted):
    "microsoft/phi-3-vision-128k-instruct": 131072,
    "microsoft/phi-3.5-moe-instruct": 131072,
    # More NVIDIA-hosted models from the catalog:
    "nvidia/cosmos-reason2-8b": 8192,
    "nvidia/embed-qa-4": 4096,
    # Mentioned in commit daaff93:
    "deepinfra/nvidia/Llama-3.1-Nemotron-70B-Instruct": 131072,
    "deepinfra/nvidia/Llama-3.3-Nemotron-Super-49B-v1.5": 262144,
    # Moonshot / Kimi:
    "moonshotai/kimi-k2.6": 262144,
    # Snowflake / Writer:
    "writer/palmyra-creative-122b": 128000,
    "writer/palmyra-fin-70b-32k": 32768,
    # Starcoder / Code:
    "bigcode/starcoder2-15b": 16384,
    # Models that appear in NVIDIA catalog:
    "nvidia/llama3-chatqa-1.5-70b": 8192,
    "nvidia/mistral-nemo-minitron-8b-8k-instruct": 8192,
    # Fallthrough: standard unknown
    "unknown": 4096,
}

# ─── Provider-Specific Functions ──────────────────────────────────────────────

def fetch_models(api_key, force_scrape=False):
    """Fetch available NVIDIA NIM chat models with dynamic context windows.

    Uses two complementary sources:

    1. **Live NVIDIA catalog scrape** (``build.nvidia.com``) — for models whose
       context length is not known a priori, we scrape ``specifications.contextLength``
       from the model page. The results are persisted to a JSON cache so repeat runs
       are instant; only models not in the cache are (re)fetched.

    2. **Hard-curated fallback map** — every NVIDIA NIM model that appeared in the
       menu after the live scrape has its context window resolved from the curated
       ``CONTEXT_WINDOWS`` dict below. Any model missing from both the live scrape
       and the curated map is marked ``context_window=0`` and will display as
       ``(context unknown)``.

    Args:
        api_key: NVIDIA API key (unused for nongated endpoints).
        force_scrape: If True, return only cached values; skip live scraping.
                      Used when user opts out of scraping.

    Returns a list of model dicts enriched with ``context_window`` keys.
    """
    # Fetch the raw model list from the official NVIDIA /v1/models endpoint
    # (it returns only id/object/created/owned_by — no context info).
    try:
        data = http_get_json(NVIDIA_API_URL, api_key)
        raw = data.get("data", [])
    except Exception as e:
        print(f"❌ Failed to retrieve NVIDIA models: {e}")
        sys.exit(1)

    model_ids = [m.get("id", "") for m in raw if m.get("id", "")]

    # ─── Step 1: try a live scrape for context windows ────────────────────────
    # This pulls contextLength from NVIDIA's own catalog pages.
    # Results are cached to ~/.claude_nvidia_scraped_context.json for repeat runs.
    scraped = fetch_nvidia_context_windows(
        model_ids,
        cache_file=CONTEXT_WINDOW_SCRAPE_CACHE,
        timeout=15,
        force=force_scrape,
    )

    # ─── Step 2: enrich each model with the best-known context window ───────────
    ctx_map = {}
    ctx_map.update(scraped)  # live scrape first (or cached)

    # Then fill in from the curated fallback map for any still-missing models
    for mid in model_ids:
        if mid not in ctx_map and mid in CONTEXT_WINDOWS:
            ctx_map[mid] = CONTEXT_WINDOWS[mid]

    # ─── Step 3: build output dicts ───────────────────────────────────────────
    models = []
    for m in raw:
        mid = m.get("id", "")
        if not mid:
            continue
        owned_by = m.get("owned_by", "").lower()
        ctx = ctx_map.get(mid, 0)
        # Skip non-chat models using the usual keyword filter
        non_chat_keywords = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"]
        skip = any(kw in mid.lower() for kw in non_chat_keywords)
        if skip:
            continue
        models.append({
            "id": mid,
            "owned_by": owned_by,
            "context_window": ctx,
        })

    return models


# ─── Cache Management (Provider-Specific) ────────────────────────────────────

def load_last_model():
    return read_cache(MODEL_CACHE_FILE)


def save_last_model(model_id):
    write_cache(MODEL_CACHE_FILE, model_id)


def load_last_context():
    return read_cache(CONTEXT_CACHE_FILE, as_int=True)


def save_last_context(context_window):
    write_cache(CONTEXT_CACHE_FILE, context_window)


def load_model_context(model_id):
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
    try:
        os.makedirs(CONTEXT_WINDOW_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CONTEXT_WINDOW_CACHE_DIR, f"{model_id}.txt")
        with open(cache_file, "w") as f:
            f.write(str(context_window))
    except OSError:
        pass


def load_model_compaction(model_id):
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
    try:
        os.makedirs(CONTEXT_WINDOW_CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CONTEXT_WINDOW_CACHE_DIR, f"{model_id}.compaction.txt")
        with open(cache_file, "w") as f:
            f.write(str(threshold))
    except OSError:
        pass


def load_statusline_mode_wrapper():
    return load_statusline_mode(STATUSLINE_MODE_CACHE_FILE)


def save_statusline_mode_wrapper(mode):
    save_statusline_mode(mode, STATUSLINE_MODE_CACHE_FILE)


def load_favorites_wrapper():
    return load_favorites(FAVORITES_CACHE_FILE)


def save_favorites_wrapper(favs):
    save_favorites(favs, FAVORITES_CACHE_FILE)


# ─── Favorites Selector ───────────────────────────────────────────────────────
def favorites_selector(models, current_favorites):
    """Present a favorites toggle list and return updated favorites set."""
    if not models:
        return current_favorites

    # Import prompt_toolkit here (after prerequisites are ensured installed)
    from prompt_toolkit import Application
    from prompt_toolkit.layout import Layout, HSplit
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.styles import Style

    terminal_height = get_terminal_height()
    visible_count = max(3, min(terminal_height - 4, len(models)))
    start_idx = 0

    current = [start_idx]
    result = [None, None, None]

    def get_formatted_options():
        top = get_top_idx()
        fragments = []
        fragments.append(("class:prompt", "Toggle favorites • SPACE to toggle • Enter to confirm • Esc to cancel\n"))

        items_above = top
        items_below = len(models) - (top + visible_count)

        for i in range(top, min(top + visible_count, len(models))):
            if i == current[0]:
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
        model_id = models[current[0]]
        if model_id in current_favorites:
            current_favorites.discard(model_id)
        else:
            current_favorites.add(model_id)

    @kb.add("enter")
    def _(event):
        result[0] = current[0]
        event.app.exit()

    @kb.add("escape")
    def _(event):
        result[0] = None
        event.app.exit()

    control = FormattedTextControl(get_formatted_options)
    window = Window(content=control, height=max(visible_count + 4, 8), always_hide_cursor=True)

    style = Style.from_dict({
        "current": "reverse",
        "normal": "",
        "hint": "italic #888888",
        "prompt": "bold",
    })

    app = Application(layout=Layout(HSplit([window])), key_bindings=kb, full_screen=False, style=style, mouse_support=False)
    app.run()

    if result[0] is not None:
        save_favorites_wrapper(current_favorites)
        return current_favorites
    return current_favorites


# ─── Arrow Key Selector ──────────────────────────────────────────────────────
def arrow_key_selector(options, prompt="Select an option:", start_idx=0, favorites=None):
    """Interactive arrow-key selector using prompt_toolkit.

    Returns (selected_index, selected_option, updated_favorites) or (None, None, None) on cancel.
    Supports UP/DOWN arrows, PAGE_UP/PAGE_DOWN, HOME/END, LEFT/RIGHT to toggle views,
    ENTER, ESC, and SPACE to toggle favorites.
    LEFT cycles to favorites-only view; RIGHT cycles back to full model list.
    The highlighted item is always kept visible via auto-scrolling.
    Favorites are shown with ★ prefix; SPACE toggles favorite status.
    """
    # Import prompt_toolkit here (after prerequisites check)
    from prompt_toolkit import Application
    from prompt_toolkit.layout import Layout, HSplit
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.styles import Style

    """Interactive arrow-key selector using prompt_toolkit.

    Returns (selected_index, selected_option, updated_favorites) or (None, None, None) on cancel.
    Supports UP/DOWN arrows, PAGE_UP/PAGE_DOWN, HOME/END, LEFT/RIGHT to toggle views,
    ENTER, ESC, and SPACE to toggle favorites.
    LEFT cycles to favorites-only view; RIGHT cycles back to full model list.
    The highlighted item is always kept visible via auto-scrolling.
    Favorites are shown with ★ prefix; SPACE toggles favorite status.
    """
    if not options:
        return None, None, None

    terminal_height = get_terminal_height()
    visible_count = max(3, min(terminal_height - 6, len(options)))
    start_idx = max(0, min(start_idx, len(options) - 1))
    current = [start_idx, 0]  # [0]=idx, [1]=view (0=full, 1=favorites)
    result = [None, None, None]  # [0]=idx, [1]=model, [2]=favorites

    if favorites is None:
        favorites = set()

    def build_options():
        view_mode = current[1]
        if view_mode == 0:
            return [{"type": "model", "id": opt, "idx": i} for i, opt in enumerate(options)]
        else:
            fav_entries = []
            for i, opt in enumerate(options):
                if opt in favorites:
                    fav_entries.append({"type": "model", "id": opt, "idx": i})
            return fav_entries

    def get_formatted_options():
        opts = build_options()
        view_mode = current[1]
        top = current[0] - (current[0] % visible_count)
        top = max(0, min(top, max(0, len(opts) - visible_count)))
        if current[0] < top:
            top = current[0]
        elif current[0] >= top + visible_count:
            top = current[0] - visible_count + 1

        if view_mode == 0:
            hint_text = "  ↑/↓ navigate • PgUp/PgDn page • Home/End jump  Left/Right toggle view• Enter select• SPACE toggle fav• Esc cancel"
        else:
            hint_text = "  ↑/↓ navigate • PgUp/PgDn page • Home/End jump  Left/Right toggle view• Enter select• SPACE toggle fav• Esc cancel"

        fragments = []
        fragments.append(("class:hint", hint_text + "\n"))

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
        if current[1] != 1:
            current[1] = 1
            current[0] = min(current[0], len(build_options()) - 1) if build_options() else 0

    @kb.add("right")
    def _(event):
        if current[1] != 0:
            current[1] = 0
            current[0] = min(current[0], len(options) - 1)

    @kb.add("enter")
    def _(event):
        result[0] = current[0]
        result[2] = favorites
        event.app.exit()

    @kb.add("space")
    def _(event):
        opts = build_options()
        if current[0] < len(opts):
            model_id = opts[current[0]]["id"]
            if model_id in favorites:
                favorites.discard(model_id)
            else:
                favorites.add(model_id)

    @kb.add("escape")
    def _(event):
        result[0] = None
        result[2] = None
        event.app.exit()

    control = FormattedTextControl(get_formatted_options)
    window = Window(
        content=control,
        height=max(visible_count + 4, 8),
        always_hide_cursor=True,
    )

    style = Style.from_dict({
        "current": "reverse",
        "normal": "",
        "hint": "italic #888888",
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
        selected_idx = result[0]
        if current[1] == 1:
            fav_entries = build_options()
            if selected_idx < len(fav_entries):
                orig_idx = fav_entries[selected_idx]["idx"]
                return orig_idx, options[orig_idx] if orig_idx < len(options) else options[0], favorites
            return 0, options[0] if options else None, favorites
        return selected_idx, options[selected_idx] if selected_idx < len(options) else options[0] if options else None, favorites
    return None, None, None


# ─── Model Selection ──────────────────────────────────────────────────────────
def get_selection_input(prompt, max_val):
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


def display_and_select(standard, free, combined, args=None):
    print("\n========================================")
    print("       AVAILABLE NVIDIA CHAT MODELS     ")
    print("========================================")

    current_number = 1
    if standard:
        print("\n--- Standard & Enterprise Chat Models ---")
        for model_obj in standard:
            model_id = model_obj.get("id", "")
            ctx = model_obj.get("context_window", 0)  # from fetch_models enriched dict
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
            print(f"[{current_number}] {model_id}{ctx_str}")
            current_number += 1
    if free:
        print("\n--- Free & Community Tier Chat Models ---")
        for model_obj in free:
            model_id = model_obj.get("id", "")
            ctx = model_obj.get("context_window", 0)  # from fetch_models enriched dict
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
            print(f"[{current_number}] {model_id}{ctx_str} (Free Tier)")
            current_number += 1

    print("========================================")
    print(f"\nTotal models: {len(combined)}")

    last_model = load_last_model()
    last_idx = None
    if last_model:
        for i, m in enumerate(combined):
            if m.get("id") == last_model:
                last_idx = i
                break

    display_options = []
    for m in combined:
        model_id = m.get("id", "")
        ctx = m.get("context_window", 0)  # from fetch_models enriched dict
        ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
        display_options.append(f"{model_id}{ctx_str}")

    current_favorites = load_favorites_wrapper()

    if args and args.numeric_model_menu:
        print("\nUse number selection to choose a model:")
        default_num = None
        last_model = load_last_model()
        if last_model:
            for i, m in enumerate(combined):
                if m.get("id") == last_model:
                    default_num = i + 1
                    break

        selected_idx = None
        if default_num:
            print(f"   (default: {default_num} - {last_model or 'last used'})")

        try:
            sel = input(f"   Enter model number (1-{len(combined)}): ").strip()
            if sel and int(sel) > 0 and int(sel) <= len(combined):
                selected_idx = int(sel) - 1
            elif not sel and default_num:
                selected_idx = default_num - 1
        except (ValueError, KeyboardInterrupt):
            print("\n👋 Cancelled by user.")
            sys.exit(0)

        if selected_idx is None:
            print("\n👋 No model selected. Exiting.")
            sys.exit(0)

        selected_model = combined[selected_idx].get("id", "")
        selected_favorites = current_favorites
    else:
        if args and args.accept_all_defaults:
            last_model = load_last_model()
            if last_model:
                for i, m in enumerate(combined):
                    if m.get("id") == last_model:
                        selected_idx = i
                        break
                else:
                    selected_idx = 0
            else:
                selected_idx = None
        else:
            print("\nUse ↑/↓ arrows to navigate, Enter to select:")
            selected_idx, selected_model, current_favorites = arrow_key_selector(
                display_options, "Select a model:", start_idx=last_idx if last_idx is not None else 0, favorites=current_favorites
            )

        if selected_idx is None:
            print("\n👋 No model selected. Exiting.")
            sys.exit(0)
        selected_model = combined[selected_idx].get("id", "")
        selected_favorites = current_favorites
    print(f"\n🚀 Selected Model: {selected_model}")

    save_last_model(selected_model)
    save_favorites_wrapper(current_favorites)

    model_data = combined[selected_idx] if selected_idx is not None and selected_idx < len(combined) else {}

    return selected_model, model_data


# ─── Context Window ───────────────────────────────────────────────────────────
def get_context_window(selected_model, model_data, args=None):
    model_ctx = model_data.get("context_window", 0)
    if model_ctx == 0:
        model_ctx = CONTEXT_WINDOWS.get(selected_model, 0)

    model_cached_ctx = load_model_context(selected_model)
    default_ctx = model_ctx if model_ctx > 0 else 200000

    options = []
    if model_ctx > 0:
        options.append(("Detected Context Window", model_ctx, "model"))
    if model_cached_ctx:
        options.append(("Last Used Context Window", model_cached_ctx, "cached"))
    options.append(("Enter Custom Context Window", default_ctx, "custom"))

    print(f"\n📏 Context Window Configuration for {selected_model}")
    print(f"   Model default: {model_ctx:,} tokens" if model_ctx > 0 else "   Model default: unknown")
    print(f"   Last used: {model_cached_ctx:,} tokens" if model_cached_ctx else "   Last used: none")

    for i, (label, value, src) in enumerate(options, 1):
        src_indicator = {"model": "📦", "cached": "💾", "custom": "✏️"}[src]
        print(f"   [{i}] {src_indicator} {label}: {value:,} tokens")

    print(f"\n   [0] Cancel")

    try:
        if args and args.accept_all_defaults:
            choice = "1"
        else:
            choice = input(f"\n📏 Select context window [0-{len(options)}] (ENTER = default, custom number for option {len(options)}): ").strip()
        if not choice:
            if model_cached_ctx:
                choice = "2"
            else:
                choice = "1"
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

            save_model_context(selected_model, context_window)
            return context_window
        else:
            print(f"   ⚠️  Invalid selection, using default: {default_ctx:,}")
            return default_ctx
    except (ValueError, KeyboardInterrupt):
        print(f"\n   Using default: {default_ctx:,} tokens")
        return default_ctx


# ─── Model Access Check ──────────────────────────────────────────────────────
def check_model_access(selected_model, api_key):
    """Verify the selected model is usable with the given API key."""
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


# ─── Generate LiteLLM Config ─────────────────────────────────────────────────
def generate_litellm_config(selected_model, api_key, context_window=None):
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if context_window is None or context_window <= 0:
        context_window = CONTEXT_WINDOWS.get(selected_model, 0)
        if context_window > 0:
            print(f"   📏 Context window (curated fallback): {context_window:,} tokens")
        else:
            context_window = 4096
            print(f"   ⚠️  Unknown model - using default context window: {context_window:,} tokens")
    else:
        print(f"   📏 Context window (user-specified): {context_window:,} tokens")

    model_info = {
        "mode": "chat",
        "max_tokens": context_window,
        "max_input_tokens": context_window,
    }

    config = {
        "model_list": [{
            "model_name": selected_model,
            "litellm_params": {
                "model": f"nvidia_nim/{selected_model}",
                "api_key": api_key,
                "api_base": "https://integrate.api.nvidia.com/v1",
            },
            "model_info": model_info,
        }],
        "general_settings": {"master_key": PROXY_MASTER_KEY},
        "litellm_settings": {"drop_params": True},
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"📝 litellm proxy config written to {CONFIG_FILE}")
    return CONFIG_FILE


# ─── Launch Claude Code ──────────────────────────────────────────────────────
def launch_claude_with_model(selected_model, context_window, dangerously_skip_permissions=False, compaction_threshold=None):
    claude_cmd = ["claude"]
    if dangerously_skip_permissions:
        claude_cmd.append("--dangerously-skip-permissions")

    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{PROXY_PORT}"
    env["ANTHROPIC_AUTH_TOKEN"] = PROXY_MASTER_KEY
    env["ANTHROPIC_MODEL"] = selected_model
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = selected_model
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_MODEL_CALLS"] = "1"
    env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
    env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(context_window)
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "0"

    compaction_value = compaction_threshold if compaction_threshold is not None else AUTO_COMPACTION_THRESHOLD
    env["CLAUDE_CODE_COMPACTION_LEVEL"] = str(compaction_value)
    env["CLAUDE_CODE_PROVIDER"] = PROVIDER_INDICATOR

    statusline_mode = load_statusline_mode(STATUSLINE_MODE_CACHE_FILE)
    if statusline_mode == "compact":
        env["CLAUDE_CODE_STATUSLINE_MODE"] = "compact"
    elif statusline_mode == "full":
        env["CLAUDE_CODE_STATUSLINE_MODE"] = "full"

    setup_claude_persistence()
    setup_statusline_symlink("claude_statusline.sh", PROVIDER_INDICATOR)

    print("\n🚀 Launching Claude Code with selected NVIDIA model...")
    print("   (This will open an interactive Claude Code session)")

    try:
        subprocess.run(claude_cmd, env=env)
    except FileNotFoundError:
        print("❌ Error: 'claude' CLI tool is not installed on your system.")
        print("   Install it via: curl -fsSL https://claude.ai | bash")
    except subprocess.CalledProcessError as e:
        print(f"\nClaude Code exited with an error code: {e.returncode}")


# ─── Usage Notes ─────────────────────────────────────────────────────────────
def print_usage_notes(dangerously_skip_permissions=False, args=None):
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
    print("⚡  PARAMETERS:")
    print(f"   --dangerously-skip-permissions: {'PASSED' if dangerously_skip_permissions else 'NOT passed'}")
    print(f"   --accept-all-defaults: {'PASSED' if args and args.accept_all_defaults else 'NOT passed'}")
    print(f"   --numeric-model-menu: {'PASSED' if args and args.numeric_model_menu else 'NOT passed'}")
    print(f"   --clear-api-key: {'PASSED' if args and args.clear_api_key else 'NOT passed'}")
    print()
    print("🌐  HOW IT WORKS:")
    print("   Claude Code speaks the Anthropic Messages API (/v1/messages).")
    print("   NVIDIA exposes an OpenAI-compatible API (/v1/chat/completions).")
    print("   A local litellm proxy translates between them so conversation")
    print("   and tool calls work against the NVIDIA model.")
    print()
    print("   • ANTHROPIC_BASE_URL=http://127.0.0.1:<port>  (litellm proxy)")
    print("   • ANTHROPIC_AUTH_TOKEN=sk-claude-bridge  (proxy master key)")
    print("   • ANTHROPIC_MODEL=<selected_model>  (actual model ID, e.g. poolside/laguna-xs-2.1)")
    print()
    print("📦  PREREQUISITES (automatically checked/installed):")
    print("   • Python3 with 'litellm[proxy]' package")
    print("   • claude CLI tool (https://claude.ai)")
    print()
    print("=" * 60)
    print()


# ─── Main ────────────────────────────────────────────────────────────────────
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
    parser.add_argument(
        "--clear-api-key",
        action="store_true",
        default=False,
        help="Clear the cached NVIDIA API key and prompt again",
    )
    parser.add_argument(
        "--accept-all-defaults",
        action="store_true",
        default=False,
        help="Auto-accept cached/default values for all prompts (quick re-launch)",
    )
    parser.add_argument(
        "--numeric-model-menu",
        action="store_true",
        default=False,
        help="Force TTY model menu with number selection instead of arrow-key selector",
    )
    args = parser.parse_args()

    print_usage_notes(dangerously_skip_permissions=args.dangerously_skip_permissions, args=args)

    if not ensure_prerequisites(args):
        print("❌ Prerequisites check failed. Exiting.")
        sys.exit(1)

    api_key = get_and_cache_api_key(CACHE_FILE, "NVIDIA_API_KEY", clear=args.clear_api_key)
    if api_key is None:
        print("🔑 NVIDIA API key not found.")
        try:
            api_key = input("   Please enter your NVIDIA API key (or press Enter for anonymous): ").strip()
        except EOFError:
            print("⚠️  No API key provided – proceeding in anonymous mode.")
            api_key = ""
        if not api_key:
            print("⚠️  No API key provided – proceeding in anonymous mode.")
        else:
            cache_api_key(CACHE_FILE, api_key)
            os.environ["NVIDIA_API_KEY"] = api_key

    print("\n🔄 Fetching model list from NVIDIA NIM API...")

    # Prompt for whether to scrape updated context windows from NVIDIA catalog.
    # Only prompt if we have cached data (meaning we've scraped before).
    # Default is "No" (use cached), but user can type Y to force refresh.
    # --accept-all-defaults auto-answers "No" (use cached values).
    cache_exists = os.path.exists(CONTEXT_WINDOW_SCRAPE_CACHE)
    # force_scrape=True means: skip live scraping, use cached-only (empty dict if no cache)
    # force_scrape=False (default) means: scrape missing models
    force_scrape = False  # default: scrape missing models

    if cache_exists:
        # Check if --accept-all-defaults is set (auto-answer "No")
        auto_accept = args is not None and args.accept_all_defaults
        if auto_accept:
            # --accept-all-defaults answers "No" (skip scrape, use cached)
            force_scrape = True
            print("   📏 Using cached context values (--accept-all-defaults).")
        else:
            # Prompt user for confirmation
            try:
                resp = input(f"\n📏 Update NVIDIA context windows from catalog? [y/N]: ").strip().lower()
                if resp in ("n", "no", ""):
                    force_scrape = True  # skip scraping, use cached only
            except (EOFError, KeyboardInterrupt):
                force_scrape = False  # default to scrape on interrupt

    all_raw_models = fetch_models(api_key, force_scrape=force_scrape)

    standard, free, combined = categorize_models(all_raw_models)
    selected_model, model_data = display_and_select(standard, free, combined, args)

    context_window = get_context_window(selected_model, model_data, args)

    # Prompt for auto-compaction threshold
    cached_compaction = load_model_compaction(selected_model)
    default_compaction = cached_compaction if cached_compaction is not None else AUTO_COMPACTION_THRESHOLD
    try:
        if args and args.accept_all_defaults:
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

        save_model_compaction(selected_model, context_window_compaction)
        print(f"   ✅ Auto-Compaction Threshold set to {context_window_compaction}%")
    except (EOFError, KeyboardInterrupt):
        context_window_compaction = default_compaction
        print(f"   Using default auto-compaction: {default_compaction}%")

    # Prompt for statusline style
    cached_mode = load_statusline_mode(STATUSLINE_MODE_CACHE_FILE)
    if cached_mode == "compact":
        default_num = 1
    elif cached_mode == "full":
        default_num = 2
    else:
        default_num = 2

    if args and args.accept_all_defaults:
        selected_mode = "compact" if default_num == 1 else "full"
        print(f"   ✅ Using cached statusline mode: {selected_mode} (accept-all-defaults)")
    else:
        try:
            mode_sel = input(f"\n📏 Statusline style [1=compact 1-line, 2=full 2-line, default: {default_num}]: ").strip()
            if not mode_sel:
                mode_sel = str(default_num)
            mode_num = int(mode_sel)
            if mode_num == 1:
                selected_mode = "compact"
            elif mode_num == 2:
                selected_mode = "full"
            else:
                selected_mode = "full" if default_num == 2 else "compact"
        except (ValueError, TypeError):
            selected_mode = "full" if default_num == 2 else "compact"
    save_statusline_mode(selected_mode, STATUSLINE_MODE_CACHE_FILE)

    generate_litellm_config(selected_model, api_key, context_window)
    proc = start_litellm_proxy(CONFIG_FILE, PROXY_PORT, PROXY_MASTER_KEY, LOG_FILE, PID_FILE)

    try:
        if not test_proxy_connection(PROXY_PORT, PROXY_MASTER_KEY, selected_model):
            print("\n❌ Proxy validation failed. Claude Code likely won't work.")
            print("   Check ~/.claude_nvidia/proxy.log for details.")
            print("   You may still attempt to launch manually.")
        launch_claude_with_model(selected_model, context_window, args.dangerously_skip_permissions, context_window_compaction)
    finally:
        stop_running_proxy(PID_FILE)
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()