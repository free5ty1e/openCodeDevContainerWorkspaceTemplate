#!/usr/bin/env python3
"""
Google Gemini / Gemma → Claude Code Bridge Script

Bridges Google Gemini/Gemma models with the Claude Code CLI via a local litellm proxy.
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
    categorize_models,
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
# Google Gemini API uses ?key=API_KEY for authentication, NOT Bearer tokens!
GOOGLE_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_CHAT_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

def get_google_chat_url(model):
    return GOOGLE_CHAT_URL_TEMPLATE.format(model=model)

CACHE_FILE = os.path.expanduser("~/.google_api_key_cache")
MODEL_CACHE_FILE = os.path.expanduser("~/.claude_google_last_model")
FAVORITES_CACHE_FILE = os.path.expanduser("~/.claude_google_favorites")
CONTEXT_CACHE_FILE = os.path.expanduser("~/.claude_google_last_context")
CONTEXT_WINDOW_CACHE_DIR = os.path.expanduser("~/.claude_google_context_windows")
STATUSLINE_MODE_CACHE_FILE = os.path.expanduser("~/.claude_google_statusline_mode")

PROXY_PORT = 4500
PROXY_MASTER_KEY = "sk-google-bridge"
CONFIG_DIR = os.path.expanduser("~/.claude_google")
CONFIG_FILE = os.path.join(CONFIG_DIR, "litellm_proxy.yaml")
LOG_FILE = os.path.join(CONFIG_DIR, "proxy.log")
PID_FILE = os.path.join(CONFIG_DIR, "proxy.pid")
AUTO_COMPACTION_THRESHOLD = 91
PROVIDER_INDICATOR = "google"

# ─── Provider-Specific Functions ──────────────────────────────────────────────

def http_get_json_google(url, api_key):
    """GET JSON from the Google Gemini API.

    Google authenticates via ?key=<API_KEY> query param (or x-goog-api-key
    header) — NOT Authorization: Bearer. Use both for max compatibility.
    """
    sep = "&" if "?" in url else "?"
    authed_url = f"{url}{sep}key={api_key}"
    req = urllib.request.Request(
        authed_url, headers={"Accept": "application/json", "x-goog-api-key": api_key}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_models(api_key):
    """Fetch the list of available models from the Google Gemini API."""
    try:
        data = http_get_json_google(GOOGLE_API_URL, api_key)
        raw = data.get("models", [])
        models = []
        for m in raw:
            methods = m.get("supportedGenerationMethods", [])
            if methods and "generateContent" not in methods:
                continue
            name = m.get("name", "")
            model_id = name.split("/", 1)[-1] if "/" in name else name
            if model_id:
                input_token_limit = m.get("inputTokenLimit", 0)
                output_token_limit = m.get("outputTokenLimit", 0)
                context_window = input_token_limit if input_token_limit > 0 else output_token_limit
                models.append({
                    "id": model_id,
                    "owned_by": "google",
                    "context_window": context_window
                })
        return models
    except Exception as e:
        print(f"❌ Failed to retrieve models: {e}")
        sys.exit(1)


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


def load_statusline_mode_wrapper():
    return load_statusline_mode(STATUSLINE_MODE_CACHE_FILE)


def save_statusline_mode_wrapper(mode):
    save_statusline_mode(mode, STATUSLINE_MODE_CACHE_FILE)


# ─── Favorites Selector ───────────────────────────────────────────────────────
def favorites_selector(models, current_favorites):
    """Present a favorites toggle list and return updated favorites set."""
    if not models:
        return current_favorites

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
    print("       GOOGLE GEMINI / GEMMA MODELS   ")
    print("========================================")

    current_number = 1
    if standard:
        print("\n--- Standard Google AI Models ---")
        for model_obj in standard:
            model_id = model_obj.get("id", "")
            ctx = model_obj.get("context_window", 0)
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else ""
            print(f"[{current_number}] {model_id}{ctx_str}")
            current_number += 1
    if free:
        print("\n--- Free & Community Tier ---")
        for model_obj in free:
            model_id = model_obj.get("id", "")
            ctx = model_obj.get("context_window", 0)
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else ""
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
        ctx = m.get("context_window", 0)
        ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else ""
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
    return test_proxy_connection(selected_model)


# ─── Generate LiteLLM Config ─────────────────────────────────────────────────
def generate_litellm_config(selected_model, api_key, context_window=None):
    os.makedirs(CONFIG_DIR, exist_ok=True)

    if context_window is None or context_window <= 0:
        models = fetch_models(api_key)
        selected_model_data = next((m for m in models if m["id"] == selected_model), None)

        if selected_model_data and selected_model_data.get("context_window", 0) > 0:
            context_window = selected_model_data["context_window"]
            print(f"   📏 Context window from API: {context_window:,} tokens")
        else:
            CONTEXT_WINDOWS = {
                "gemini-1.5-pro": 2000000,
                "gemini-1.5-pro-001": 2000000,
                "gemini-1.5-pro-002": 2000000,
                "gemini-1.5-flash": 1000000,
                "gemini-1.5-flash-001": 1000000,
                "gemini-1.5-flash-002": 1000000,
                "gemini-1.5-flash-8b": 1000000,
                "gemini-2.0-flash": 1000000,
                "gemini-2.0-flash-lite": 1000000,
                "gemini-2.0-pro": 2000000,
                "gemini-2.5-pro": 2000000,
                "gemini-2.5-flash": 1000000,
                "gemini-3.5-flash-lite": 1000000,
                "gemini-3.5-flash": 1000000,
                "gemini-3.6-flash": 1000000,
                "gemini-3.7-flash": 1000000,
                "gemini-3.8-flash": 1000000,
                "gemini-3.1-pro": 2000000,
                "gemini-1.0-pro": 32768,
                "gemini-1.0-pro-vision": 16384,
                "gemini-1.0-pro-001": 32768,
            }
            context_window = CONTEXT_WINDOWS.get(selected_model, 1000000)
            print(f"   ⚠️  Using fallback context window: {context_window:,} tokens")
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
                "model": f"gemini/{selected_model}",
                "api_key": api_key,
            },
            "model_info": model_info,
        }],
        "general_settings": {"master_key": PROXY_MASTER_KEY, "store_model_in_db": False},
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

    print("\n🚀 Launching Claude Code with selected Google model...")
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
    print("   This script bridges Google Gemini/Gemma models with the Claude Code CLI.")
    print("   It fetches available Google AI chat models, lets you select one,")
    print("   runs a local litellm proxy (Gemini→Anthropic translation),")
    print("   then launches Claude Code configured to use that Google model.")
    print()
    print("🔑  API KEY SETUP:")
    print("   • Set GOOGLE_API_KEY environment variable export")
    print("     GOOGLE_API_KEY='AIza...'")
    print("   • Or run the script once - it will prompt and cache the key")
    print("     to ~/.google_api_key_cache for future runs.")
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
    print("   Google exposes a Gemini API endpoint.")
    print("   A local litellm proxy translates between them so conversation")
    print("   and tool calls work against the Google model.")
    print()
    print("   • ANTHROPIC_BASE_URL=http://127.0.0.1:<port>  (litellm proxy)")
    print("   • ANTHROPIC_AUTH_TOKEN=sk-claude-bridge  (proxy master key)")
    print("   • ANTHROPIC_MODEL=<selected_model>  (actual model ID, e.g. gemini-1.5-flash)")
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
        description="Bridge Google Gemini/Gemma models with the Claude Code CLI"
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
        help="Clear the cached Google API key and prompt again",
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

    api_key = get_and_cache_api_key(CACHE_FILE, "GOOGLE_API_KEY", clear=args.clear_api_key)
    if api_key is None:
        print("🔑 Google API key not found.")
        try:
            api_key = input("   Please enter your Google API key (or press Enter for anonymous): ").strip()
        except EOFError:
            print("⚠️  No API key provided – proceeding in anonymous mode.")
            api_key = ""
        if not api_key:
            print("⚠️  No API key provided – proceeding in anonymous mode.")
        else:
            cache_api_key(CACHE_FILE, api_key)
            os.environ["GOOGLE_API_KEY"] = api_key

    print("\n🔄 Fetching model list from Google AI API...")
    all_raw_models = fetch_models(api_key)

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
            print("   Check ~/.claude_google/proxy.log for details.")
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