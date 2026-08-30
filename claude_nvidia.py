#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import shutil
import requests

# ─── Configuration ───────────────────────────────────────────────────────
API_URL = "https://integrate.api.nvidia.com/v1/models"
CACHE_FILE = os.path.expanduser("~/.nvidia_api_key_cache")
LITELLM_CONFIG_DIR = os.path.expanduser("~/.litellm")
LITELLM_CONFIG_FILE = os.path.join(LITELLM_CONFIG_DIR, "config.json")

# ─── Step 1: Check & install prerequisites ──────────────────────────────
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
            [sys.executable, "-m", "pip", "install", "-q", pip_name],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        print(f"  ❌ Failed to install {pip_name}.")
        return False

def ensure_prerequisites():
    """Ensure requests, litellm, and claude CLI are available."""
    print("🔍 Checking prerequisites...")
    ok = True
    ok &= install_if_missing("requests", "requests")
    ok &= install_if_missing("litellm", "litellm")
    # Check claude CLI
    if shutil.which("claude") is None:
        print("  ⚠️  'claude' CLI not found in PATH.")
        print("   Please install it manually: https://claude.ai")
        ok = False
    else:
        print("  ✅ claude CLI found.")
    return ok

# ─── Step 2: Prompt for API key if not cached ───────────────────────────
def get_api_key():
    """Return a valid NVIDIA API key, prompting if needed and caching it."""
    NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")

    if NVIDIA_API_KEY:
        print("✅ NVIDIA API key found in environment.")
        return NVIDIA_API_KEY

    # Check cache file
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

    # Prompt user interactively
    print("🔑 NVIDIA API key not found.")
    try:
        api_key = input("   Please enter your NVIDIA API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n❌ No API key provided. Exiting.")
        sys.exit(1)

    if not api_key:
        print("❌ No API key provided. Exiting.")
        sys.exit(1)

    # Cache the key
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
def fetch_models(api_key):
    """Fetch the list of available chat models from NVIDIA API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    try:
        response = requests.get(API_URL, headers=headers)
        response.raise_for_status()
        return response.json().get("data", [])
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to retrieve models: {e}")
        sys.exit(1)

# ─── Step 4: Categorize, filter, and sort models ────────────────────────
def categorize_models(all_raw_models):
    """Categorize models into standard and free/tier, sorted with free at bottom."""
    NON_CHAT_KEYWORDS = ["embed", "rerank", "guard", "clip", "siglip", "vector", "modality"]
    FREE_KEYWORDS = ["community", "instruct", "chat", "deepseek", "kimi", "glm", "llama", "gemma", "nemotron"]

    standard_chat_models = []
    free_tier_chat_models = []

    for model_obj in all_raw_models:
        model_id = model_obj.get("id", "")
        owned_by = model_obj.get("owned_by", "").lower()
        model_id_lower = model_id.lower()

        # Exclusion check: skip if it matches non-chat keywords
        if any(keyword in model_id_lower for keyword in NON_CHAT_KEYWORDS):
            continue

        # Classify: community/open models go to free tier
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

    # Sort alphabetically within each tier
    standard_chat_models.sort()
    free_tier_chat_models.sort()

    # Combine: standard first, then free (free at bottom of combined list)
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

    # Handle user selection
    print(f"\nSelect a model number [1-{len(combined)}]:")
    selected_idx = get_selection_input("> ", len(combined))
    selected_model = combined[selected_idx]
    print(f"\n🚀 Selected Model: {selected_model}")
    return selected_model

# ─── Step 6: Configure litellm and launch Claude Code ───────────────────
def generate_litellm_config(selected_model, api_key):
    """Generate a litellm config file for the selected NVIDIA model."""
    os.makedirs(LITELLM_CONFIG_DIR, exist_ok=True)

    config = {
        "model_order": [selected_model],
        "models": {
            selected_model: {
                "litellm_params": {
                    "api_key": api_key,
                    "base_url": "https://integrate.api.nvidia.com/v1",
                },
            }
        },
    }

    with open(LITELLM_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print(f"📝 litellm config written to {LITELLM_CONFIG_FILE}")
    return LITELLM_CONFIG_FILE

def launch_claude_with_model(selected_model, api_key):
    """Configure environment and launch Claude Code litellm."""
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = "https://integrate.api.nvidia.com/v1"
    env["ANTHROPIC_AUTH_TOKEN"] = api_key
    env["ANTHROPIC_MODEL"] = selected_model
    env["CLAUDE_CODE_SUBAGENT_MODEL"] = selected_model
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"

    # Set litellm config path if it was generated
    if os.path.exists(LITELLM_CONFIG_FILE):
        env["LITELLM_CONFIG_FILE"] = LITELLM_CONFIG_FILE

    print("🚀 Launching Claude Code with selected NVIDIA model...")
    print("   (This will open an interactive Claude Code session)")

    try:
        subprocess.run(["claude"], env=env, check=True)
    except FileNotFoundError:
        print("❌ Error: 'claude' CLI tool is not installed on your system.")
        print("   Install it via: curl -fsSL https://claude.ai | bash")
    except subprocess.CalledProcessError as e:
        print(f"\nClaude Code exited with an error code: {e.returncode}")

# ─── Step 7: CLI test/validation step ───────────────────────────────────
def test_model_selection(selected_model, api_key):
    """Quick validation test to verify the selected model responds."""
    print(f"\n🧪 Testing selected model: {selected_model}...")
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # Simple chat completion test
        payload = {
            "model": selected_model,
            "messages": [{"role": "user", "content": "Hello! Please respond with a simple greeting."}],
            "max_tokens": 50,
            "temperature": 0.5,
        }
        resp = requests.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"✅ Model test successful!")
            print(f"   Full response: {content}")
            # Also print the model echo back
            model_used = data.get("model", selected_model)
            print(f"   Model used: {model_used}")
            print(f"   Usage: {data.get('usage', {})}")
            return True
        else:
            print(f"⚠️  Model test returned status {resp.status_code}")
            print(f"   Full error response: {resp.text}")
            return False
    except Exception as e:
        print(f"⚠️  Model test failed: {type(e).__name__}: {str(e)[:100]}")
        return False

# ─── Usage Notes ─────────────────────────────────────────────────────────
def print_usage_notes():
    """Print usage notes and relevant environment variables."""
    print("=" * 60)
    print("  NVIDIA → Claude Code Bridge Script")
    print("=" * 60)
    print()
    print("📋  SCRIPT PURPOSE:")
    print("   This script bridges NVIDIA NIM models with the Claude Code CLI.")
    print("   It fetches available NVIDIA chat models, lets you select one,")
    print("   then launches Claude Code configured to use that NVIDIA model.")
    print()
    print("🔑  API KEY SETUP:")
    print("   • Set NVIDIA_API_KEY environment variable export")
    print("     NVIDIA_API_KEY='nvapi-...'")
    print("   • Or run the script once - it will prompt and cache the key")
    print("     to ~/.nvidia_api_key_cache for future runs.")
    print()
    print("🌐  ENVIRONMENT VARIABLES THIS SCRIPT SETS:")
    print("   • ANTHROPIC_BASE_URL=https://integrate.api.nvidia.com/v1")
    print("     (Routes Anthropic-compatible calls to NVIDIA API)")
    print("   • ANTHROPIC_AUTH_TOKEN=<your_nvapi_key>")
    print("     (Your NVIDIA API key for authentication)")
    print("   • ANTHROPIC_MODEL=<selected_model>")
    print("     (The NVIDIA model you chose from the list)")
    print("   • CLAUDE_CODE_SUBAGENT_MODEL=<selected_model>")
    print("   • CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1")
    print("   • LITELLM_CONFIG_FILE=<path to generated config>")
    print("     (Generated ~/.litellm/config.json with model mapping)")
    print()
    print("📦  PREREQUISITES (automatically checked/installed):")
    print("   • Python3 with 'requests' and 'litellm' packages")
    print("   • claude CLI tool (https://claude.ai)")
    print()
    print("🤖  MODEL SELECTION:")
    print("   • Script fetches ~71 chat models from NVIDIA API")
    print("   • Models are categorized: Standard/Enterprise first,")
    print("     Free/Community tier at the bottom")
    print("   • Select by number - free models are indices 67-71")
    print("   • Quick validation test runs before Claude Code launch")
    print()
    print("=" * 60)
    print()

# ─── Main ────────────────────────────────────────────────────────────────
def main():
    print_usage_notes()

    # 1. Ensure prerequisites
    if not ensure_prerequisites():
        print("❌ Prerequisites check failed. Exiting.")
        sys.exit(1)

    # 2. Get API key
    api_key = get_api_key()

    # 3. Fetch models
    print("\n🔄 Fetching model list from NVIDIA NIM API...")
    all_raw_models = fetch_models(api_key)

    # 4. Categorize and sort
    standard, free, combined = categorize_models(all_raw_models)

    # 5. Display and select
    selected_model = display_and_select(standard, free, combined)

    # 6. Generate litellm config
    generate_litellm_config(selected_model, api_key)

    # 7. CLI test/validation
    test_model_selection(selected_model, api_key)

    # 8. Launch Claude Code
    print()
    launch_claude_with_model(selected_model, api_key)

if __name__ == "__main__":
    main()