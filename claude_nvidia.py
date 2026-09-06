#!/usr/bin/env python3
"""
NVIDIA NIM → Claude Code Bridge Script

Bridges NVIDIA NIM models with the Claude Code CLI via a local litellm proxy.
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
from prompt_toolkit import Application
from prompt_toolkit.layout import Layout, HSplit
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.styles import Style

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
STATUSLINE_MODE_CACHE_FILE = os.path.expanduser("~/.claude_nvidia_statusline_mode")

PROXY_PORT = 4499  # NVIDIA (default) — overridable via CLAUDE_BRIDGE_PORT
PROXY_PORT = int(os.environ.get("CLAUDE_BRIDGE_PORT", PROXY_PORT))
PROXY_MASTER_KEY = "sk-claude-bridge"
CONFIG_DIR = os.path.expanduser("~/.claude_nvidia")
CONFIG_FILE = os.path.join(CONFIG_DIR, "litellm_proxy.yaml")
LOG_FILE = os.path.join(CONFIG_DIR, "proxy.log")
PID_FILE = os.path.join(CONFIG_DIR, "proxy.pid")

# ─── Provider-Specific Functions ──────────────────────────────────────────────

def fetch_models(api_key):
    """Fetch the list of available chat models from NVIDIA API."""
    try:
        data = http_get_json(NVIDIA_API_URL, api_key)
        return data.get("data", [])
    except Exception as e:
        print(f"❌ Failed to retrieve models: {e}")
        sys.exit(1)


def categorize_models(all_raw_models):
    """Categorize models into standard and free/tier."""
    return filter_chat_models(
        all_raw_models,
        non_chat_keywords=["embed", "rerank", "guard", "clip", "siglip", "vector", "modality", "reward", "parse", "omni"],
        free_keywords=["community", "instruct", "chat", "deepseek", "kimi", "glm", "llama", "gemma", "nemotron"]
    )


def load_last_model():
    return read_cache(MODEL_CACHE_FILE)


def save_last_model(model_id):
    write_cache(MODEL_CACHE_FILE, model_id)


def load_last_context():
    return read_cache(CONTEXT_CACHE_FILE, as_int=True)


def save_last_context(context_window):
    write_cache(CONTEXT_CACHE_FILE, context_window)


def load_favorites_wrapper():
    return load_favorites(FAVORITES_CACHE_FILE)


def save_favorites_wrapper(favs):
    save_favorites(favs, FAVORITES_CACHE_FILE)


def load_statusline_mode_wrapper():
    return load_statusline_mode(STATUSLINE_MODE_CACHE_FILE)


def save_statusline_mode_wrapper(mode):
    save_statusline_mode(mode, STATUSLINE_MODE_CACHE_FILE)


# ─── Arrow Key Selector (Simplified - no favorites) ───────────────────────────
def arrow_key_selector(options, prompt="Select an option:", start_idx=0):
    """Interactive arrow-key selector using prompt_toolkit.

    Returns (selected_index, selected_option) or (None, None) on cancel.
    Supports UP/DOWN arrows, PAGE_UP/PAGE_DOWN, HOME/END, ENTER, ESC.
    The highlighted item is always kept visible via auto-scrolling.
    """
    if not options:
        return None, None

    terminal_height = get_terminal_height()
    visible_count = max(3, min(terminal_height - 4, len(options)))
    start_idx = max(0, min(start_idx, len(options) - 1))
    current = [start_idx]
    result = [None]

    def get_top_idx():
        top = current[0] - (current[0] % visible_count)
        top = max(0, min(top, max(0, len(options) - visible_count)))
        if current[0] < top:
            top = current[0]
        elif current[0] >= top + visible_count:
            top = current[0] - visible_count + 1
        return top

    def get_formatted_options():
        top = get_top_idx()
        fragments = []

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
        return result[0], options[result[0]]
    return None, None


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


def display_and_select(standard, free, combined):
    # Context window mapping for display (NVIDIA API doesn't return this)
    CONTEXT_WINDOWS = {
        "nvidia/nemotron-3.5-lightning-30b-a3b": 1048576,
        "nvidia/nemotron-3-ultra": 200000,
        "nvidia/nemotron-3-8b": 200000,
        "nvidia/nemotron-3-ultra-32b": 200000,
        "nvidia/llama-3.1-nemotron-70b-instruct": 128000,
        "nvidia/llama-3.1-nemotron-8b-instruct": 128000,
        "nvidia/llama-3.2-nemotron-3b-instruct": 128000,
        "nvidia/llama-3.2-nemotron-1b-instruct": 128000,
        "meta/llama-3.1-405b-instruct": 128000,
        "meta/llama-3.1-70b-instruct": 128000,
        "meta/llama-3.1-8b-instruct": 128000,
        "google/gemma-2-27b-it": 8192,
        "google/gemma-2-9b-it": 8192,
        "microsoft/phi-3.5-mini-instruct": 128000,
        "microsoft/phi-3.5-moe-instruct": 128000,
        "nvidia/nemotron-4-ultra": 200000,
        "nvidia/nemotron-4-340b": 200000,
    }

    print("\n========================================")
    print("       AVAILABLE NVIDIA CHAT MODELS     ")
    print("========================================")

    current_number = 1
    if standard:
        print("\n--- Standard & Enterprise Chat Models ---")
        for model_obj in standard:
            model_id = model_obj.get("id", "")
            ctx = CONTEXT_WINDOWS.get(model_id, 0)
            ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
            print(f"[{current_number}] {model_id}{ctx_str}")
            current_number += 1
    if free:
        print("\n--- Free & Community Tier Chat Models ---")
        for model_obj in free:
            model_id = model_obj.get("id", "")
            ctx = CONTEXT_WINDOWS.get(model_id, 0)
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
        ctx = CONTEXT_WINDOWS.get(model_id, 0)
        ctx_str = f" ({ctx:,} tokens)" if ctx > 0 else " (context unknown)"
        display_options.append(f"{model_id}{ctx_str}")

    print("\nUse ↑/↓ arrows to navigate, Enter to select:")
    selected_idx, _ = arrow_key_selector(display_options, "Select a model:", start_idx=last_idx if last_idx is not None else 0)
    if selected_idx is None:
        print("\n👋 No model selected. Exiting.")
        sys.exit(0)
    selected_model = combined[selected_idx].get("id", "")
    print(f"\n🚀 Selected Model: {selected_model}")

    save_last_model(selected_model)

    return selected_model, combined[selected_idx]


# ─── Context Window ───────────────────────────────────────────────────────────
def get_context_window(selected_model, model_data):
    model_ctx = model_data.get("context_window", 0)
    if model_ctx == 0:
        CONTEXT_WINDOWS = {
            "nvidia/nemotron-3.5-lightning-30b-a3b": 1048576,
            "nvidia/nemotron-3-ultra": 200000,
            "nvidia/nemotron-3-8b": 200000,
            "nvidia/nemotron-3-ultra-32b": 200000,
            "nvidia/llama-3.1-nemotron-70b-instruct": 128000,
            "nvidia/llama-3.1-nemotron-8b-instruct": 128000,
            "nvidia/llama-3.2-nemotron-3b-instruct": 128000,
            "nvidia/llama-3.2-nemotron-1b-instruct": 128000,
            "meta/llama-3.1-405b-instruct": 128000,
            "meta/llama-3.1-70b-instruct": 128000,
            "meta/llama-3.1-8b-instruct": 128000,
            "google/gemma-2-27b-it": 8192,
            "google/gemma-2-9b-it": 8192,
            "microsoft/phi-3.5-mini-instruct": 128000,
            "microsoft/phi-3.5-moe-instruct": 128000,
            "nvidia/nemotron-4-ultra": 200000,
            "nvidia/nemotron-4-340b": 200000,
        }
        model_ctx = CONTEXT_WINDOWS.get(selected_model, 0)

    cached_ctx = load_last_context()

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

    save_last_context(context_window)
    return context_window


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
        CONTEXT_WINDOWS = {
            "nvidia/nemotron-3.5-lightning-30b-a3b": 1048576,
            "nvidia/nemotron-3-ultra": 200000,
            "nvidia/nemotron-3-8b": 200000,
            "nvidia/nemotron-3-ultra-32b": 200000,
            "nvidia/llama-3.1-nemotron-70b-instruct": 128000,
            "nvidia/llama-3.1-nemotron-8b-instruct": 128000,
            "nvidia/llama-3.2-nemotron-3b-instruct": 128000,
            "nvidia/llama-3.2-nemotron-1b-instruct": 128000,
            "meta/llama-3.1-405b-instruct": 128000,
            "meta/llama-3.1-70b-instruct": 128000,
            "meta/llama-3.1-8b-instruct": 128000,
            "google/gemma-2-27b-it": 8192,
            "google/gemma-2-9b-it": 8192,
            "microsoft/phi-3.5-mini-instruct": 128000,
            "microsoft/phi-3.5-moe-instruct": 128000,
            "nvidia/nemotron-4-ultra": 200000,
            "nvidia/nemotron-4-340b": 200000,
        }
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
def launch_claude_with_model(selected_model, context_window, dangerously_skip_permissions=False):
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

    setup_claude_persistence()
    setup_statusline_symlink("claude_statusline.sh", "nvidia")

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
def print_usage_notes(dangerously_skip_permissions=False):
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
    print("🔑  API KEY MANAGEMENT:")
    print("   • Set NVIDIA_API_KEY environment variable export")
    print("     NVIDIA_API_KEY='nvapi-...'")
    print("   • Or run the script once - it will prompt and cache the key")
    print("     to ~/.nvidia_api_key_cache for future runs.")
    print("   • Use --clear-api-key to clear the cached key and prompt again")
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
    args = parser.parse_args()

    print_usage_notes(dangerously_skip_permissions=args.dangerously_skip_permissions)

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
    all_raw_models = fetch_models(api_key)

    standard, free, combined = categorize_models(all_raw_models)
    selected_model, model_data = display_and_select(standard, free, combined)

    context_window = get_context_window(selected_model, model_data)

    generate_litellm_config(selected_model, api_key, context_window)
    proc = start_litellm_proxy(CONFIG_FILE, PROXY_PORT, PROXY_MASTER_KEY, LOG_FILE, PID_FILE)

    try:
        if not test_proxy_connection(PROXY_PORT, PROXY_MASTER_KEY, selected_model):
            print("\n❌ Proxy validation failed. Claude Code likely won't work.")
            print("   Check ~/.claude_nvidia/proxy.log for details.")
            print("   You may still attempt to launch manually.")
        launch_claude_with_model(selected_model, context_window, args.dangerously_skip_permissions)
    finally:
        stop_running_proxy(PID_FILE)
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()