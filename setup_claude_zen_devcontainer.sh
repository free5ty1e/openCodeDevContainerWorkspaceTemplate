#!/usr/bin/env bash
# ==============================================================================
# setup_claude_zen_devcontainer.sh
#
# Portable setup script for running ANY OpenAI-compatible model through the
# Claude Code CLI.  Uses a lightweight translation proxy to convert Anthropic
# Messages ↔ OpenAI Chat Completions.
#
# ── What it does ──────────────────────────────────────────────────────────────
#  1. Installs prerequisites: pip packages (fastapi, uvicorn, httpx, tiktoken)
#  2. Creates a standalone translation proxy (proxy.py)
#  3. Creates a JSON backends configuration file
#  4. Creates shell aliases for launching Claude CLI through the proxy
#  5. Migrates ~/.claude/ to the workspace ($SCRIPT_DIR/.claude_persist/) for full
#     session/config persistence across devcontainer rebuilds via symlink
#  6. Creates a symlink so Claude Code's memory survives rebuilds:
#     $HOME/.claude/projects/<slug>/memory/ → $SCRIPT_DIR/.ai_memory/
#  7. All state lives in .claude_config/, .ai_memory/, or .claude_persist/
#     — critical data is never lost on rebuild
#
# ── How the proxy works ───────────────────────────────────────────────────────
#   Claude CLI speaks the Anthropic Messages API (POST /v1/messages with SSE).
#   OpenAI-compatible models speak the Chat Completions API (POST /v1/chat/completions).
#   This proxy translates between the two protocols:
#
#     claude  ──ANTHROPIC_BASE_URL──►  zen-proxy (:8083)  ──Chat Completions──►  upstream
#       (Anthropic Messages SSE)         ↕ translation          (OpenAI SSE)
#
# ── Coexistence with ollama+claude setup ──────────────────────────────────────
#   Both scripts share ~/.claude -> .claude_persist/ so sessions/history are
#   visible across models. Each script writes disjoint files into .clau le_config/.
#   Run 'c' (ollama) and 'cz' (zen/proxy) in separate windows for different models.
#   ┌──────────────────────┬───────────────────────────┬──────────────────────────┐
#   │                      │  ollama+claude setup      │  zen+claude setup (this) │
#   ├──────────────────────┼───────────────────────────┼──────────────────────────┤
#   │ Persistence dir      │ .claude_config/           │ .claude_config/       │
#   │ ~/.clau de ->        │ .claude_persist/          │ .claule_persist/    │
#   │ Proxy port           │ N/A (ollama built-in)     │ 8083                     │
#   │ Shell aliases        │ c, c-new, cc              │ cz, cz-new, ccz          │
#   │ Shell markers        │ claude-ollama-devcontainer│ claude-zen-devcontainer   │
#   │ ANTHROPIC_BASE_URL   │ not set (ollama launch)   │ http://127.0.0.1:8083     │
#   │ Auth mechanism       │ ollama launch wrapper     │ ANTHROPIC_AUTH_TOKEN      │
#   └──────────────────────┴───────────────────────────┴──────────────────────────┘
#
# ── Quick start ───────────────────────────────────────────────────────────────
#   ./setup_claude_zen_devcontainer.sh
#   source ~/.zshrc
#   cz         # pick a model from any family → Claude CLI launches with it
#
# ── Testing / validation ───────────────────────────────────────────────────────
#   After setup, verify the proxy works end-to-end:
#
#   1. Check proxy is running:
#        curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8083/
#      Expected: 200
#
#   2. Test model list endpoint:
#        curl -s http://127.0.0.1:8083/v1/models | python3 -m json.tool
#
#   3. Test non-streaming chat via proxy (single JSON response):
#        curl -s -X POST http://127.0.0.1:8083/v1/messages \
#          -H "Content-Type: application/json" -H "x-api-key: test" \
#          -d '{"model":"claude-fable-5","max_tokens":50,"messages":[{"role":"user","content":"Say hi in one word"}],"stream":false}' \
#          | python3 -m json.tool
#      Expected: response with content[].text like "Hi"
#
#   4. Test streaming chat via proxy (SSE events):
#        curl -s -N -X POST http://127.0.0.1:8083/v1/messages \
#          -H "Content-Type: application/json" -H "x-api-key: test" \
#          -d '{"model":"claude-fable-5","max_tokens":100,"messages":[{"role":"user","content":"Say hi in one word"}],"stream":true}'
#      Expected: SSE events: message_start → content_block_start → content_block_delta* → content_block_stop → message_delta → message_stop
#
#   5. Test end-to-end with Claude Code CLI print mode:
#        echo "Say hi" | ANTHROPIC_BASE_URL=http://127.0.0.1:8083 ANTHROPIC_API_KEY=test \
#          /path/to/claude --print --model claude-fable-5
#      Expected: Claude responds via the proxy (exit 0, prints response)
#
#   6. Use the shell wrapper (recommended):
#        source ~/.zshrc
#        echo "What model are you?" | cz -p
#      Expected: Claude responds via the proxy
#
# ── After setup: shell aliases ────────────────────────────────────────────────
#   cz              Pick a model and launch Claude CLI through the proxy
#   cz-new          Same as cz
#   cz-danger       Pick a model -> launch Claude CLI (auto-accept permissions)
#   cz-cloud        Launch Claude CLI directly (cloud, no proxy)
#   ccz             Continue most recent Claude cloud session
#   cz-model        Pick/change the default model
#   cz-model-current  Show currently selected model
#   cz-proxy-start  Start the proxy daemon (auto-started on first use)
#   cz-proxy-stop   Stop the proxy daemon
#   cz-proxy-status Check proxy daemon status
#   cz-undo-danger  Remove danger guardrails from workspace CLAUDE.md
#
# ── Backends configuration ────────────────────────────────────────────────────
# Edit the JSON file at .claude_config/backends.json:
#
#   {
#     "zen": {
#       "base_url": "https://opencode.ai/zen/v1",
#       "api_key_env": "ZEN_API_KEY",
#       "model": "",
#       "provider_name": "ZEN",
#       "models": {
#         "Claude":     ["claude-fable-5", "claude-opus-4-8", ...],   # paid — set ZEN_API_KEY
#         "GPT":        ["gpt-5.5", "gpt-5.5-pro", ...],               # paid — set ZEN_API_KEY
#         "Gemini":     ["gemini-3.5-flash", ...],                      # paid — set ZEN_API_KEY
#         "DeepSeek":   [...],                                          # paid — set ZEN_API_KEY
#         "xAI":        [...],                                          # paid — set ZEN_API_KEY
#         "Other":      [...],                                          # paid — set ZEN_API_KEY
#         "Free":       ["big-pickle", "deepseek-v4-flash-free", ...]       # free — no key needed
#       }
#     },
#     "openai": {
#       "base_url": "https://api.openai.com/v1",
#       "api_key_env": "OPENAI_API_KEY",
#       "model": "gpt-4o",
#       "provider_name": "OpenAI"
#     }
#   }
#
# Set "model" to "" for Zen to pass through any model from the picker.
# Add/remove models under the "models" dict to customize your list.
#
# ── Environment variables ─────────────────────────────────────────────────────
#   CLAUDE_ZEN_CONFIG_DIR   Override persistence dir (default: .claude_config)
#   CLAUDE_ZEN_PROXY_PORT   Override proxy port (default: 8083)
#   ZEN_API_KEY             API key for OpenCode Zen (required for paid models; free models work without it)
#   ANTHROPIC_AUTH_TOKEN    Proxy auth token (default: "freecc")
#   ZEN_BACKENDS            Override path to backends JSON (default: backends.json)
#   ZEN_HOST                Override proxy host (default: 0.0.0.0)
#   ZEN_PORT                Override proxy port (default: 8083)
#   ZEN_DEFAULT_PROVIDER    Override default provider ID (default: first backend)
#
# ── Requirements ──────────────────────────────────────────────────────────────
#   - Python 3.12+ (system)
#   - pip packages: fastapi, uvicorn, httpx, tiktoken
#   - Claude Code CLI (@anthropic-ai/claude-code)
#   - curl
#
# ── Files created ─────────────────────────────────────────────────────────────
#   .claude_config/
#   ├── backends.json        Backend provider definitions (edit to add more)
#   ├── proxy.py             Standalone translation proxy
#   ├── selected-model       Last selected MODEL= string
#   ├── proxy.log            Proxy daemon log
#   ├── proxy.pid            Proxy daemon PID
#   └── danger/              Danger-mode guardrails (CLAUDE.md backup + rules)
#
#   .ai_memory/
#   └── (research files)     Claude Code memory files (symlinked from home folder)
#                            Persists across devcontainer rebuilds
#
#   .claude_persist/
#   └── (full ~/.claude copy) Migrated Claude config, sessions, tasks, history
#                              Survives devcontainer rebuild via symlink:
#                              ~/.claude -> .claude_persist/
# ==============================================================================
set -euo pipefail

# ─── Guard: root ──────────────────────────────────────────────────────────────
if [ "${EUID}" -eq 0 ]; then
    if [ -n "${SUDO_USER:-}" ]; then
        target_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
        exec sudo -u "${SUDO_USER}" env HOME="${target_home}" PATH="${PATH}" bash "$0" "$@"
    fi
    printf 'Run as normal user, not root.\n' >&2
    exit 1
fi

# ─── Configuration ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Use _SETUP suffix so the variable is NOT shadowed by an already-exported
# CLAUDE_ZEN_CONFIG_DIR from a previous install (same pattern as the ollama
# script). The baked wrapper exports CLAUDE_ZEN_CONFIG_DIR at shell-source
# time; re-running the setup script must not inherit the old value.
PERSISTENCE_DIR="${CLAUDE_ZEN_CONFIG_DIR_SETUP:-${SCRIPT_DIR}/.claude_config}"
PROXY_PORT="${CLAUDE_ZEN_PROXY_PORT:-8083}"
PROXY_SCRIPT="${PERSISTENCE_DIR}/proxy.py"
BACKENDS_FILE="${PERSISTENCE_DIR}/backends.json"
SELECTED_MODEL_FILE="${PERSISTENCE_DIR}/selected-model"
PID_FILE="${PERSISTENCE_DIR}/proxy.pid"
LOG_FILE="${PERSISTENCE_DIR}/proxy.log"
MARKER_BEGIN="# >>> claude-zen-devcontainer >>>"
MARKER_END="# <<< claude-zen-devcontainer <<<"
NPM_GLOBAL_DIR="${HOME}/.npm-global"

have() { command -v "$1" >/dev/null 2>&1; }
export PATH="${NPM_GLOBAL_DIR}/bin:${PATH}"

# ─── 1. Prerequisites: dedicated proxy venv ──────────────────────────────────
# Use a venv inside the workspace config dir so packages are:
#   - isolated from the system Python and any active devcontainer venv
#   - always found by the proxy regardless of what 'python3' resolves to
#   - persisted across devcontainer rebuilds (lives on the workspace volume)
printf '\n%s\n' "=== Step 1: Proxy Python environment ==="
PROXY_VENV="${PERSISTENCE_DIR}/proxy-venv"
# Find the base Python executable, bypassing any active venv shim.
# sys._base_executable points at the real interpreter even inside a venv.
SYSTEM_PY="$(python3 -c 'import sys; print(sys._base_executable)' 2>/dev/null || command -v python3)"
printf '  Base Python: %s (%s)\n' "${SYSTEM_PY}" "$(${SYSTEM_PY} --version 2>&1)"

USE_SYSTEM_PY_FLAG="${PERSISTENCE_DIR}/.USE_SYSTEM_PY"

if [ -x "${PROXY_VENV}/bin/python3" ] && "${PROXY_VENV}/bin/python3" -m pip --version >/dev/null 2>&1; then
    # Venv exists and pip is usable
    printf '  Proxy venv already exists. Ensuring packages...\n'
    "${PROXY_VENV}/bin/python3" -m pip install -q fastapi uvicorn httpx tiktoken
    printf '  ✓ Proxy venv ready: %s\n' "${PROXY_VENV}/bin/python3"

elif [ -f "${USE_SYSTEM_PY_FLAG}" ] || { [ -x "${PROXY_VENV}/bin/python3" ] && ! "${PROXY_VENV}/bin/python3" -m pip --version >/dev/null 2>&1; }; then
    # Either previously fell back to system Python, or the venv exists but is
    # broken (no pip -- happens when ensurepip is unavailable).
    # Fall back to system Python with --break-system-packages.
    if [ -x "${PROXY_VENV}/bin/python3" ] && ! "${PROXY_VENV}/bin/python3" -m pip --version >/dev/null 2>&1; then
    printf '  Warning: proxy venv is incomplete (pip not installed).\n'
        printf '  Removing broken venv and falling back to system Python.\n'
        rm -rf "${PROXY_VENV}"
        printf '%s\n' "${SYSTEM_PY}" > "${USE_SYSTEM_PY_FLAG}"
        printf '  Using system Python. Ensuring packages...\n'
else
        printf '  Using system Python (from previous run). Ensuring packages...\n'
    fi
    pip3 install --break-system-packages -q fastapi uvicorn httpx tiktoken 2>&1
    printf '  ✓ Using system Python (packages installed with --break-system-packages)\n'

else
    # Try creating a venv first
    printf '  Creating proxy venv at %s ...\n' "${PROXY_VENV}"
    if "${SYSTEM_PY}" -m venv "${PROXY_VENV}" 2>/dev/null; then
        printf '  Installing proxy dependencies into venv...\n'
        "${PROXY_VENV}/bin/python3" -m pip install -q fastapi uvicorn httpx tiktoken
        printf '  ✓ Proxy venv ready: %s\n' "${PROXY_VENV}/bin/python3"
else
    printf '  Warning: standard venv creation failed (ensurepip not available).\n'
        printf '  Falling back to system Python with --break-system-packages.\n'
        pip3 install --break-system-packages -q fastapi uvicorn httpx tiktoken 2>&1
        printf '%s\n' "${SYSTEM_PY}" > "${USE_SYSTEM_PY_FLAG}"
        printf '  ✓ Using system Python (packages installed via --break-system-packages)\n'
        printf '    System Python: %s\n' "${SYSTEM_PY}"
    fi
fi

# ─── 2. Claude CLI ─────────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 2: Claude CLI ==="
if have claude; then
    printf '  Found: %s\n' "$(command -v claude)"
else
    printf '  Installing @anthropic-ai/claude-code via npm...\n'
    mkdir -p "${NPM_GLOBAL_DIR}"
    npm config set prefix "${NPM_GLOBAL_DIR}" 2>/dev/null || true
    npm install -g @anthropic-ai/claude-code 2>&1 || {
        printf '  WARNING: npm install failed.\n'
        printf '  Install Claude CLI manually: npm install -g @anthropic-ai/claude-code\n' >&2
    }
    if have claude; then
        printf '  Installed: %s\n' "$(command -v claude)"
else
    printf '  Warning: claude not in PATH yet. It may be at %s/bin/claude\n' "${NPM_GLOBAL_DIR}"
        printf '  Restart your shell or run: export PATH="%s/bin:\${PATH}"\n' "${NPM_GLOBAL_DIR}"
    fi
fi

# ─── 3. Persistence dir ───────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 3: Persistence dir ==="
mkdir -p "${PERSISTENCE_DIR}"

# ─── 4. Proxy script ──────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 4: Proxy script ==="
cat > "${PROXY_SCRIPT}" << 'PYEOF'
"""Lightweight Anthropic-to-OpenAI translation proxy.

Usage:
    python3 proxy.py --backends /path/to/backends.json

Listens on ZEN_HOST:ZEN_PORT (default 0.0.0.0:8083).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import tiktoken
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Backend configuration
# ---------------------------------------------------------------------------

@dataclass
class Backend:
    provider_id: str
    base_url: str
    api_key: str
    model: str
    provider_name: str = ""
    api_key_env: str = ""
    models: dict | None = None
    # Enhanced metadata for rich model picker
    description: str = ""
    free_tier_info: str = ""
    requires_api_key: bool = False

# ---------------------------------------------------------------------------
# Provider Registry with rich metadata for model picker
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY = {
    "zen": {
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "description": "Free and paid models from multiple providers via OpenCode proxy",
        "free_tier_info": "Free models available without API key (big-pickle, deepseek-v4-flash-free, nemotron-3-ultra-free, north-mini-code-free)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "description": "Ultra-fast inference for open-source models",
        "free_tier_info": "14,400 requests/day free tier (llama-3.3-70b, mixtral-8x7b, gemma-7b, etc.)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "google": {
        "name": "Google (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GOOGLE_API_KEY",
        "description": "Google's Gemini models via OpenAI-compatible API",
        "free_tier_info": "1,500 requests/day free tier (gemini-1.5-flash, gemini-1.5-pro)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "description": "Access to 100+ models via single API",
        "free_tier_info": "Some models free (mistral-7b, phi-3-mini, etc.) - check OpenRouter for current list",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "together": {
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "description": "Cloud platform for open-source models (Llama, Mistral, DeepSeek, etc.)",
        "free_tier_info": "Free tier with rate limits ($1 free credits/month)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "deepinfra": {
        "name": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_key_env": "DEEPINFRA_API_KEY",
        "description": "Serverless inference for open-source LLMs",
        "free_tier_info": "Free tier with rate limits (Llama-3, Mixtral, DeepSeek, etc.)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "description": "Fast inference for open-source and custom models",
        "free_tier_info": "Free tier with rate limits (Llama-3, DeepSeek, Qwen, etc.)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "together": {
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "description": "Cloud platform for open-source models",
        "free_tier_info": "Free tier with rate limits",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "deepinfra": {
        "name": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_key_env": "DEEPINFRA_API_KEY",
        "description": "Serverless inference for open-source LLMs",
        "free_tier_info": "Free tier with rate limits",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "description": "Fast inference for open-source and custom models",
        "free_tier_info": "Free tier with rate limits",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
}

# ---------------------------------------------------------------------------
# API Key Vault - persistent storage with prompting
# ---------------------------------------------------------------------------

import base64
import hashlib

class APIKeyVault:
    """Persistent API key storage with optional encryption/obfuscation."""
    
    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._vault = {}
        self._load()
    
    def _load(self):
        if self.vault_path.exists():
            try:
                with open(self.vault_path, 'r') as f:
                    self._vault = json.load(f)
            except Exception:
                self._vault = {}
    
    def _save(self):
        try:
            with open(self.vault_path, 'w') as f:
                json.dump(self._vault, f, indent=2)
        except Exception as e:
            print(f"Warning: could not save API key vault: {e}", file=sys.stderr)
    
    def get_key(self, provider_id: str) -> str | None:
        """Get API key for a provider."""
        return self._vault.get(provider_id)
    
    def set_key(self, provider_id: str, key: str):
        """Store API key for a provider."""
        self._vault[provider_id] = key
        self._save()
    
    def has_key(self, provider_id: str) -> bool:
        """Check if provider has a stored key."""
        return provider_id in self._vault and bool(self._vault[provider_id])
    
    def remove_key(self, provider_id: str):
        """Remove stored key for a provider."""
        if provider_id in self._vault:
            del self._vault[provider_id]
            self._save()

# ---------------------------------------------------------------------------
# Anthropic → OpenAI request conversion (adapted from claude-code-proxy)
# ---------------------------------------------------------------------------

def set_if_not_none(d: dict, key: str, value: Any) -> None:
    if value is not None:
        d[key] = value


def convert_messages(anthropic_messages: list[dict]) -> list[dict]:
    """Convert Anthropic messages to OpenAI chat format."""
    openai_messages = []
    system_content = None

    for msg in anthropic_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            system_content = content if isinstance(content, str) else ""
            continue

        if role == "assistant":
            if isinstance(content, list):
                text_parts = []
                tool_calls = []
                for block in content:
                    match block.get("type"):
                        case "text":
                            text_parts.append(block.get("text", ""))
                        case "thinking":
                            text_parts.append(
                                block.get("signature", "") or block.get("text", "")
                            )
                        case "tool_use":
                            func_args = json.dumps(block.get("input", {}))
                            tool_calls.append({
                                "id": block.get("id", f"tool_{uuid.uuid4().hex[:8]}"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", "unknown"),
                                    "arguments": func_args,
                                },
                            })
                        case "tool_result":
                            pass
                msg_dict = {"role": "assistant"}
                if text_parts:
                    msg_dict["content"] = "\n".join(text_parts)
                if tool_calls:
                    msg_dict["tool_calls"] = tool_calls
                openai_messages.append(msg_dict)
            else:
                openai_messages.append({"role": "assistant", "content": str(content)})
            continue

        if role == "user":
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": str(block.get("content", "")),
                        })
                if text_parts:
                    openai_messages.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                openai_messages.append({"role": "user", "content": str(content)})
            continue

    if system_content:
        openai_messages.insert(0, {"role": "system", "content": system_content})

    return openai_messages


def convert_tools(anthropic_tools: list[dict] | None) -> list[dict]:
    """Convert Anthropic tool format to OpenAI function format."""
    if not anthropic_tools:
        return []
    tools = []
    for tool in anthropic_tools:
        tools.append({
            "type": "function",
            "function": {
                "name": tool.get("name", "unknown"),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return tools


# ---------------------------------------------------------------------------
# OpenAI → Anthropic SSE conversion
# ---------------------------------------------------------------------------

_enc = tiktoken.get_encoding("cl100k_base")


def _estimate_tokens(text: str) -> int:
    return len(_enc.encode(text))


async def make_anthropic_stream(
    openai_stream: AsyncIterator[dict],
    model: str,
    allow_thinking: bool = False,
) -> AsyncIterator[str]:
    """Convert an OpenAI streaming response to Anthropic SSE format."""

    message_id = f"msg_{uuid.uuid4().hex}"
    input_tokens = 0
    finish_reason = None

    yield f'event: message_start\ndata: {json.dumps({"type": "message_start","message":{"id":message_id,"type":"message","role":"assistant","content":[],"model":model,"stop_reason":None,"stop_sequence":None,"usage":{"input_tokens":0,"output_tokens":0}}})}\n\n'

    text_buffer = ""
    tool_calls: dict[int, dict] = {}
    thinking_block_open = False
    text_block_open = False

    async for chunk in openai_stream:
        choices = chunk.get("choices", [])
        if not choices:
            usage = chunk.get("usage")
            if usage:
                input_tokens = usage.get("prompt_tokens", 0) or input_tokens
            continue

        delta = choices[0].get("delta", {})
        finish = choices[0].get("finish_reason")
        if finish:
            finish_reason = finish

        # Reasoning content
        reasoning = delta.get("reasoning_content")
        if reasoning:
            if allow_thinking:
                if not thinking_block_open:
                    yield f'event: content_block_start\ndata: {json.dumps({"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}})}\n\n'
                    thinking_block_open = True
                yield f'event: content_block_delta\ndata: {json.dumps({"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":reasoning}})}\n\n'
            else:
                if not text_block_open:
                    yield f'event: content_block_start\ndata: {json.dumps({"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}})}\n\n'
                    text_block_open = True
                yield f'event: content_block_delta\ndata: {json.dumps({"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":reasoning}})}\n\n'
            text_buffer += reasoning

        # Text content
        text = delta.get("content", "")
        if text:
            if not text_block_open:
                idx = 1 if thinking_block_open else 0
                yield f'event: content_block_start\ndata: {json.dumps({"type":"content_block_start","index":idx,"content_block":{"type":"text","text":""}})}\n\n'
                text_block_open = True
            idx = 1 if thinking_block_open else 0
            yield f'event: content_block_delta\ndata: {json.dumps({"type":"content_block_delta","index":idx,"delta":{"type":"text_delta","text":text}})}\n\n'
            text_buffer += text

        # Tool calls
        tc_list = delta.get("tool_calls", [])
        for tc in tc_list:
            idx = tc.get("index", 0)
            if idx not in tool_calls:
                tool_calls[idx] = {
                    "id": tc.get("id", f"tool_{uuid.uuid4().hex[:8]}"),
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": "",
                }
                yield f'event: content_block_start\ndata: {json.dumps({"type":"content_block_start","index":idx+2,"content_block":{"type":"tool_use","id":tool_calls[idx]["id"],"name":tool_calls[idx]["name"]}})}\n\n'
            args_delta = tc.get("function", {}).get("arguments", "")
            if args_delta:
                tool_calls[idx]["arguments"] += args_delta
                yield f'event: content_block_delta\ndata: {json.dumps({"type":"content_block_delta","index":idx+2,"delta":{"type":"input_json_delta","partial_json":args_delta}})}\n\n'

        usage = chunk.get("usage")
        if usage:
            input_tokens = usage.get("prompt_tokens", 0) or input_tokens

    # Close content blocks
    if thinking_block_open:
        yield f'event: content_block_stop\ndata: {json.dumps({"type":"content_block_stop","index":0})}\n\n'
    if text_block_open:
        idx = 1 if thinking_block_open else 0
        yield f'event: content_block_stop\ndata: {json.dumps({"type":"content_block_stop","index":idx})}\n\n'
    for idx in sorted(tool_calls.keys()):
        yield f'event: content_block_stop\ndata: {json.dumps({"type":"content_block_stop","index":idx+2})}\n\n'

    # Estimate output tokens
    output_tokens = _estimate_tokens(text_buffer)
    for tc in tool_calls.values():
        output_tokens += _estimate_tokens(tc.get("arguments", ""))

    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    anthropic_stop = stop_map.get(finish_reason, "end_turn")

    yield f'event: message_delta\ndata: {json.dumps({"type":"message_delta","delta":{"stop_reason":anthropic_stop,"stop_sequence":None},"usage":{"output_tokens":output_tokens}})}\n\n'
    yield f'event: message_stop\ndata: {json.dumps({"type":"message_stop"})}\n\n'


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class MessagesRequest(BaseModel):
    model: str = "claude-sonnet-4-6"
    messages: list[dict] = Field(default_factory=list)
    max_tokens: int = 4096
    system: str | list[dict] | None = None
    tools: list[dict] | None = None
    temperature: float | None = None
    top_p: float | None = None
    stream: bool = True

    model_config = {"extra": "allow"}


backends: dict[str, Backend] = {}
default_provider_id: str = ""
http_client: httpx.AsyncClient | None = None


def load_backends(path: Path) -> dict[str, Backend]:
    with open(path) as f:
        data = json.load(f)
    result = {}
    for pid, info in data.items():
        api_key = info.get("api_key") or os.environ.get(
            info.get("api_key_env", ""), ""
        )
        result[pid] = Backend(
            provider_id=pid,
            base_url=info["base_url"].rstrip("/"),
            api_key=api_key,
            model=info.get("model", ""),
            provider_name=info.get("provider_name", pid.upper()),
            api_key_env=info.get("api_key_env", ""),
            models=info.get("models"),
        )
    return result


def get_backend(request: Request) -> Backend:
    """Select backend based on x-api-key suffix."""
    global default_provider_id, backends

    auth = request.headers.get("x-api-key") or request.headers.get("authorization") or ""
    pid = default_provider_id

    if ":" in auth:
        suffix = auth.split(":", 1)[1].strip()
        if suffix and suffix in backends:
            pid = suffix

    if pid not in backends:
        raise HTTPException(status_code=400, detail=f"Unknown backend: {pid}. Available: {list(backends)}")
    return backends[pid]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0))
    yield
    if http_client:
        await http_client.aclose()


app = FastAPI(lifespan=lifespan)


def _get_client() -> httpx.AsyncClient:
    if http_client is None:
        raise RuntimeError("HTTP client not initialized")
    return http_client


def _probe_response() -> Response:
    return Response(status_code=204, headers={"Allow": "GET, POST, HEAD, OPTIONS"})


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/")
async def root():
    be = backends.get(default_provider_id)
    return {
        "status": "ok",
        "provider": default_provider_id,
        "model": f"{default_provider_id}/{backends[default_provider_id].model}" if default_provider_id in backends else "",
    }


@app.api_route("/", methods=["HEAD", "OPTIONS"])
async def probe_root():
    return _probe_response()


@app.api_route("/v1/messages", methods=["HEAD", "OPTIONS"])
async def probe_messages():
    return _probe_response()


@app.get("/v1/models")
async def list_models():
    models = []
    for pid, be in backends.items():
        display = be.provider_name or pid
        # Does this backend have an API key requirement at all?
        has_key_req = bool(be.api_key_env)
        # If backend has a models dict, list all models from it
        if be.models:
            for family, model_list in be.models.items():
                # Free family models don't need a key; all others do
                family_needs_key = has_key_req and (family != "Free")
                for m in model_list:
                    models.append({
                        "id": m,
                        "display_name": f"{display} {family} ({m})",
                        "created_at": "2025-01-01T00:00:00Z",
                        "type": "model",
                        "api_key_required": family_needs_key,
                    })
        else:
            model_id = be.model or f"{pid}/default"
            models.append({
                "id": model_id,
                "display_name": f"{display} ({model_id})",
                "created_at": "2025-01-01T00:00:00Z",
                "type": "model",
                "api_key_required": has_key_req,
            })
    return {"data": models}


@app.post("/v1/messages")
async def create_message(request: Request):
    body = await request.json()
    req = MessagesRequest(**body)
    be = get_backend(request)

    anthropic_beta = request.headers.get("anthropic-beta", "")
    allow_thinking = "thinking-2025-01-02" in anthropic_beta

    openai_messages = convert_messages(req.messages)
    if req.system is not None:
        if isinstance(req.system, list):
            system_text = "\n".join(
                b.get("text", "") for b in req.system if isinstance(b, dict) and b.get("type") == "text"
            )
            if system_text:
                openai_messages.insert(0, {"role": "system", "content": system_text})
        elif req.system:
            openai_messages.insert(0, {"role": "system", "content": req.system})

    openai_tools = convert_tools(req.tools)
    upstream_model = be.model or req.model.split("/")[-1] if "/" in req.model else req.model

    payload = {
        "model": upstream_model,
        "messages": openai_messages,
        "max_tokens": req.max_tokens or 4096,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    set_if_not_none(payload, "temperature", req.temperature)
    set_if_not_none(payload, "top_p", req.top_p)
    if openai_tools:
        payload["tools"] = openai_tools

    headers = {"Content-Type": "application/json"}
    if be.api_key:
        headers["Authorization"] = f"Bearer {be.api_key}"

    client = _get_client()

    try:
        upstream_resp = await client.post(
            f"{be.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        upstream_resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        detail = f"Upstream error: {e.response.status_code}"
        try:
            detail += f" - {e.response.text[:200]}"
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=detail)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Upstream connection error: {e}")

    if req.stream:
        return StreamingResponse(
            make_anthropic_stream(_iter_openai_sse(upstream_resp), req.model, allow_thinking),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming: collect chunks and build single response
    content_parts: list[dict] = []
    thinking_text = ""
    text_buffer = ""
    tool_calls: dict[int, dict] = {}
    finish_reason: str | None = None
    input_tokens = 0

    async for chunk in _iter_openai_sse(upstream_resp):
        choices = chunk.get("choices", [])
        if not choices:
            usage = chunk.get("usage")
            if usage:
                input_tokens = usage.get("prompt_tokens", 0) or input_tokens
            continue
        delta = choices[0].get("delta", {})
        finish = choices[0].get("finish_reason")
        if finish:
            finish_reason = finish

        reasoning = delta.get("reasoning_content")
        if reasoning:
            thinking_text += reasoning

        text = delta.get("content", "")
        if text:
            text_buffer += text

        for tc in delta.get("tool_calls", []):
            idx = tc.get("index", 0)
            if idx not in tool_calls:
                tool_calls[idx] = {
                    "id": tc.get("id", f"tool_{uuid.uuid4().hex[:8]}"),
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": "",
                }
            tool_calls[idx]["arguments"] += tc.get("function", {}).get("arguments", "")

        usage = chunk.get("usage")
        if usage:
            input_tokens = usage.get("prompt_tokens", 0) or input_tokens

    if allow_thinking and thinking_text:
        content_parts.append({"type": "thinking", "thinking": thinking_text})
    text_buffer = thinking_text + text_buffer if not allow_thinking and thinking_text else text_buffer
    if text_buffer:
        content_parts.append({"type": "text", "text": text_buffer})
    for idx in sorted(tool_calls.keys()):
        tc = tool_calls[idx]
        content_parts.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["name"],
            "input": json.loads(tc["arguments"]) if tc["arguments"] else {},
        })

    if not content_parts:
        content_parts = [{"type": "text", "text": ""}]

    output_tokens = _estimate_tokens(text_buffer)
    for tc in tool_calls.values():
        output_tokens += _estimate_tokens(tc.get("arguments", ""))

    stop_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
    anthropic_stop = stop_map.get(finish_reason, "end_turn")

    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "content": content_parts,
        "model": req.model,
        "stop_reason": anthropic_stop,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


async def _iter_openai_sse(resp: httpx.Response) -> AsyncIterator[dict]:
    """Iterate over an OpenAI streaming response, yielding parsed JSON chunks."""
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    body = await request.json()
    text = ""
    for msg in body.get("messages", []):
        c = msg.get("content", "")
        if isinstance(c, str):
            text += c
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "")
    tokens = _estimate_tokens(text)
    return {"input_tokens": tokens}


def main():
    global default_provider_id, backends

    parser = argparse.ArgumentParser()
    parser.add_argument("--backends", default=os.environ.get("ZEN_BACKENDS", "backends.json"))
    parser.add_argument("--host", default=os.environ.get("ZEN_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ZEN_PORT", "8083")))
    args = parser.parse_args()

    backends_path = Path(args.backends)
    if not backends_path.exists():
        print(f"Backends file not found: {backends_path}", file=sys.stderr)
        sys.exit(1)

    backends = load_backends(backends_path)
    if not backends:
        print("No backends configured", file=sys.stderr)
        sys.exit(1)

    default_provider_id = os.environ.get("ZEN_DEFAULT_PROVIDER") or next(iter(backends))

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
PYEOF
chmod +x "${PROXY_SCRIPT}"
printf '  Created %s\n' "${PROXY_SCRIPT}"
# ─── 4.5. API Key Vault ──────────────────────────────────────────────────────
KEY_VAULT_SCRIPT="${PERSISTENCE_DIR}/key_vault.py"
cat > "${KEY_VAULT_SCRIPT}" << 'PYEOF'
"""API Key Vault — resolve, prompt, and persist API keys for LLM providers.

Usage:
    python3 key_vault.py resolve <provider_id> <backends_file> <vault_file>
        -> prints the key to stdout (empty = no key needed or user skipped)

The vault file is a simple JSON dict: { "provider_id": "key_value", ... }
"""
import json, os, sys

VAULT_FILE = ""
BACKENDS_FILE = ""

KEY_ENV_CACHE = {}  # provider_id -> env_var_name

def get_key_env(pid):
    """Get the env var name for a provider from backends.json."""
    if pid in KEY_ENV_CACHE:
        return KEY_ENV_CACHE[pid]
    try:
        with open(BACKENDS_FILE) as f:
            cfg = json.load(f)
        be = cfg.get(pid, {})
        env_name = be.get("api_key_env", "")
        KEY_ENV_CACHE[pid] = env_name
        return env_name
    except Exception:
        return ""

def get_provider_meta(pid):
    """Get provider metadata from backends.json."""
    try:
        with open(BACKENDS_FILE) as f:
            cfg = json.load(f)
        be = cfg.get(pid, {})
        return {
            "name": be.get("provider_name", pid),
            "description": be.get("description", ""),
            "free_tier_info": be.get("free_tier_info", ""),
        }
    except Exception:
        return {"name": pid, "description": "", "free_tier_info": ""}

def load_vault():
    try:
        if os.path.exists(VAULT_FILE):
            with open(VAULT_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_vault(vault):
    try:
        os.makedirs(os.path.dirname(VAULT_FILE), exist_ok=True)
        with open(VAULT_FILE, 'w') as f:
            json.dump(vault, f, indent=2)
    except Exception as e:
        print(f"  Warning: could not save API key: {e}", file=sys.stderr)

def cmd_resolve(pid, model_name=""):
    """Resolve the API key for provider pid.

    Priority: 1) env var  2) vault  3) prompt user
    Returns the key on stdout, empty if no key needed / user skipped.
    If model_name is in a "Free" family in backends.json, skip prompting.
    """
    # Check if the selected model is free — skip key prompt entirely
    if model_name:
        try:
            with open(BACKENDS_FILE) as f:
                cfg = json.load(f)
            be = cfg.get(pid, {})
            models_dict = be.get("models", {})
            for family, model_list in models_dict.items():
                if family.startswith("Free") and model_name in model_list:
                    return ""  # Free model — no API key needed
        except Exception:
            pass

    key_env = get_key_env(pid)
    if not key_env:
        return ""  # No API key needed for this provider

    # 1. Environment variable already set?
    val = os.environ.get(key_env, "") or ""
    if val:
        return val

    # 2. Check vault
    vault = load_vault()
    stored = vault.get(pid, "")
    if stored:
        return stored

    # 3. Prompt user
    meta = get_provider_meta(pid)
    provider_name = meta.get("name", pid)
    description = meta.get("description", "")
    free_tier_info = meta.get("free_tier_info", "")

    prompt_parts = [f"\n  {provider_name} requires an API key."]
    if description:
        prompt_parts.append(f"  {description}")
    if free_tier_info:
        prompt_parts.append(f"  {free_tier_info}")
    prompt_parts.append(f"  Set ${key_env} or enter your key (or press Enter to skip): ")
    prompt_text = "\n".join(prompt_parts)

    try:
        with open("/dev/tty", "r", encoding="utf-8") as tty:
            sys.stderr.write(prompt_text + " ")
            sys.stderr.flush()
            key = tty.readline().strip()
    except Exception:
        return ""

    if not key:
        sys.stderr.write(f"  [SKIP] No key provided. Model may not work.\n")
        return ""

    # Store in vault
    vault[pid] = key
    save_vault(vault)
    return key

def cmd_set(pid, key_value):
    """Store a key in the vault."""
    vault = load_vault()
    vault[pid] = key_value
    save_vault(vault)
    return ""

def cmd_check(pid):
    """Check if a key is available (env var or vault). Return the key if found."""
    key_env = get_key_env(pid)
    if not key_env:
        return ""
    val = os.environ.get(key_env, "") or ""
    if val:
        return val
    vault = load_vault()
    return vault.get(pid, "")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: key_vault.py <resolve|set|check> <provider_id> <backends_file> <vault_file> [key_value]",
              file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    pid = sys.argv[2]
    BACKENDS_FILE = sys.argv[3]
    VAULT_FILE = sys.argv[4]

    if cmd == "resolve":
        model_name = sys.argv[5] if len(sys.argv) >= 6 else ""
        result = cmd_resolve(pid, model_name)
        if result:
            print(result)
    elif cmd == "set":
        if len(sys.argv) >= 6:
            cmd_set(pid, sys.argv[5])
    elif cmd == "check":
        result = cmd_check(pid)
        if result:
            print(result)
    else:
        sys.exit(1)
PYEOF
chmod +x "${KEY_VAULT_SCRIPT}"
printf '  Created %s\n' "${KEY_VAULT_SCRIPT}"


# ─── 5. Backends config ───────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 5: Backends config ==="
printf '  Querying OpenCode Zen API for available models...\n'
python3 << 'PY' > "${BACKENDS_FILE}"
"""Generate backends.json dynamically by querying ALL providers' APIs."""
import json, os, sys, urllib.request
from urllib.error import URLError

# Provider registry (mirrors proxy.py's PROVIDER_REGISTRY)
PROVIDER_REGISTRY = {
    "zen": {
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "description": "Free and paid models from multiple providers via OpenCode proxy",
        "free_tier_info": "Free models available without API key (big-pickle, deepseek-v4-flash-free, nemotron-3-ultra-free, north-mini-code-free)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "description": "Ultra-fast inference for open-source models",
        "free_tier_info": "14,400 requests/day free tier (llama-3.3-70b, mixtral-8x7b, gemma-7b, etc.)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "google": {
        "name": "Google (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GOOGLE_API_KEY",
        "description": "Google's Gemini models via OpenAI-compatible API",
        "free_tier_info": "1,500 requests/day free tier (gemini-1.5-flash, gemini-1.5-pro)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "description": "Access to 100+ models via single API",
        "free_tier_info": "Some models free (mistral-7b, phi-3-mini, etc.) - check OpenRouter for current list",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "together": {
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "description": "Cloud platform for open-source models (Llama, Mistral, DeepSeek, etc.)",
        "free_tier_info": "Free tier with rate limits ($1 free credits/month)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "deepinfra": {
        "name": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_key_env": "DEEPINFRA_API_KEY",
        "description": "Serverless inference for open-source LLMs",
        "free_tier_info": "Free tier with rate limits (Llama-3, Mixtral, DeepSeek, etc.)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "description": "Fast inference for open-source and custom models",
        "free_tier_info": "Free tier with rate limits (Llama-3, DeepSeek, Qwen, etc.)",
        "supports_dynamic_discovery": True,
        "models_endpoint": "/models",
        "chat_endpoint": "/chat/completions",
    },
}

def fetch_models(base_url, models_endpoint, api_key_env):
    url = base_url.rstrip('/') + models_endpoint
    headers = {"User-Agent": "Mozilla/5.0"}
    api_key = os.environ.get(api_key_env, '')
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return [m.get("id", m.get("name", "")) for m in data.get("data", [])]
    except Exception as e:
        print(f"  Warning: could not fetch models from {base_url} ({e}).", file=sys.stderr)
        return []

def probe_free(base_url, chat_endpoint, model):
    body = json.dumps({"model": model, "messages": [{"role":"user","content":"hi"}], "max_tokens":1, "stream":False}).encode()
    headers = {"Content-Type":"application/json", "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(base_url.rstrip('/') + "/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8):
            return True
    except URLError as e:
        if getattr(e, "code", None) == 401:
            return False
        return True
    except Exception:
        return True

print('  Querying providers for available models...', file=sys.stderr)
all_models = {}
for pid, provider in PROVIDER_REGISTRY.items():
    if provider.get('supports_dynamic_discovery'):
        models = fetch_models(provider['base_url'], provider['models_endpoint'], provider['api_key_env'])
        if models:
            print(f'  Found {len(models)} models from {provider["name"]}', file=sys.stderr)
            all_models[pid] = models
        else:
            print(f'  No models found from {provider["name"]}', file=sys.stderr)

if not all_models:
    print('  Warning: no models fetched from any provider. Using fallback.', file=sys.stderr)
    all_models = {'zen': ['big-pickle']}

# Probe free models
zen_models_list = all_models.get('zen', [])
free_candidates = [m for m in zen_models_list if m.endswith('-free') or m == 'big-pickle']

free_models = []
expired = []
if free_candidates:
    print(f'  Probing {len(free_candidates)} potential free model(s)...', file=sys.stderr)
    zen_provider = PROVIDER_REGISTRY['zen']
    for m in free_candidates:
        if probe_free(zen_provider['base_url'], zen_provider['chat_endpoint'], m):
            print(f'    ✓ {m}', file=sys.stderr)
            free_models.append(m)
        else:
            print(f'    ✗ {m} (needs API key)', file=sys.stderr)
            expired.append(m)

FAMILY_RULES = [
    ("claude-", "Claude"), ("gpt-", "GPT"), ("gemini-", "Gemini"),
    ("deepseek-", "DeepSeek"), ("grok-", "xAI"),
    ("glm-", "Other"), ("minimax-", "Other"), ("kimi-", "Other"),
    ("qwen", "Other"), ("mimo-", "Other"), ("nemotron-", "Other"),
    ("north-", "Other"),
]

def classify(mid):
    for prefix, family in FAMILY_RULES:
        if mid.startswith(prefix):
            return family
    return "Other"

families = {}
zen_only = all_models.get('zen', [])
for m in zen_only:
    fam = classify(m)
    families.setdefault(fam, []).append(m)

free_set = set(free_models)
expired_set = set(expired)

ORDER = ["Claude", "GPT", "Gemini", "DeepSeek", "xAI", "Other", "Free"]
zen_models_out = {}

for fam in ORDER:
    if fam == "Free":
        continue
    models_in_fam = [m for m in families.get(fam, []) if m not in free_set and m not in expired_set]
    if models_in_fam:
        zen_models_out[fam] = models_in_fam

if free_models:
    zen_models_out["Free"] = sorted(set(free_models))

if expired:
    zen_models_out.setdefault("Other", []).extend(expired)

result = {
    "zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "api_key": "",
        "model": "",
        "provider_name": "ZEN",
        "models": zen_models_out,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "api_key": "",
        "model": "llama-3.3-70b-versatile",
        "provider_name": "Groq",
        "models": {"Groq": all_models.get("groq", [])},
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key_env": "GOOGLE_API_KEY",
        "api_key": "",
        "model": "gemini-1.5-flash",
        "provider_name": "Google (Gemini)",
        "models": {"Gemini": all_models.get("google", [])},
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "api_key": "",
        "model": "openrouter/auto",
        "provider_name": "OpenRouter",
        "models": {"OpenRouter": all_models.get("openrouter", [])},
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "api_key": "",
        "model": "meta-llama/Llama-3.3-70B-Instruct",
        "provider_name": "Together AI",
        "models": {"Together": all_models.get("together", [])},
    },
    "deepinfra": {
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_key_env": "DEEPINFRA_API_KEY",
        "api_key": "",
        "model": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "provider_name": "DeepInfra",
        "models": {"DeepInfra": all_models.get("deepinfra", [])},
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "api_key": "",
        "model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "provider_name": "Fireworks AI",
        "models": {"Fireworks": all_models.get("fireworks", [])},
    },
}

json.dump(result, sys.stdout, indent=4)
print()
PY
    if [ -s "${BACKENDS_FILE}" ]; then
    printf '  Generated %s with dynamic model list\n' "${BACKENDS_FILE}"
else
    printf '  Warning: dynamic generation failed, writing fallback.\n' >&2
    cat > "${BACKENDS_FILE}" << JSONEOF
{
    "zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "api_key": "",
        "model": "",
        "provider_name": "ZEN",
        "models": {
            "Free": ["big-pickle"]
        }
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "api_key": "",
        "model": "llama-3.3-70b-versatile",
        "provider_name": "Groq"
    }
}
JSONEOF
    printf '  Created %s (fallback)\n' "${BACKENDS_FILE}"
fi

# ─── 6. Shell wrappers ────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 6: Shell wrappers ==="

_wrapper_block() {
    cat << 'WRAPEOF' | sed \
        -e "s|__MARKER_BEGIN__|${MARKER_BEGIN}|g" \
        -e "s|__MARKER_END__|${MARKER_END}|g" \
        -e "s|__PERSISTENCE_DIR__|${PERSISTENCE_DIR}|g" \
        -e "s|__PROXY_PORT__|${PROXY_PORT}|g" \
        -e "s|__PROXY_SCRIPT__|${PROXY_SCRIPT}|g" \
        -e "s|__NPM_GLOBAL_DIR__|${NPM_GLOBAL_DIR}|g" \
        -e "s|__SELECTED_MODEL_FILE__|${SELECTED_MODEL_FILE}|g" \
        -e "s|__BACKENDS_FILE__|${BACKENDS_FILE}|g" \
        -e "s|__PID_FILE__|${PID_FILE}|g" \
        -e "s|__LOG_FILE__|${LOG_FILE}|g"
__MARKER_BEGIN__
export CLAUDE_ZEN_CONFIG_DIR="__PERSISTENCE_DIR__"
export CLAUDE_ZEN_PROXY_PORT="__PROXY_PORT__"
export PATH="__NPM_GLOBAL_DIR__/bin:${PATH}"

unalias cz cz-new cz-cloud cz-continue ccz cz-danger 2>/dev/null || true
unset -f cz cz_new ccz claude_zen_launch claude_zen_launch_danger \
      claude_zen_cloud_launch \
      claude_zen_pick_model claude_zen_current_model \
      _claude_zen_pick _claude_zen_ensure_proxy \
      claude_zen_proxy_start claude_zen_proxy_stop claude_zen_proxy_status \
      claude_zen_uninstall_danger_rules 2>/dev/null || true

# ── Interactive model picker ──────────────────────────────────────────────────
_claude_zen_pick() {
    python3 - "$@" << 'PY'
import json, os, sys
f = os.environ.get("ZEN_BACKENDS") or "__BACKENDS_FILE__"
try:
    with open(f) as fh:
        cfg = json.load(fh)
except Exception as e:
    print(f"Error loading backends: {e}", file=sys.stderr); sys.exit(1)

# Provider registry with rich metadata for display
PROVIDER_META = {
    "zen": {
        "name": "OpenCode Zen",
        "free_tier_info": "Free models (big-pickle, deepseek-v4-flash-free) work without an API key. Paid models need ZEN_API_KEY.",
    },
    "groq": {
        "name": "Groq",
        "free_tier_info": "14,400 req/day free tier",
    },
    "google": {
        "name": "Google (Gemini)",
        "free_tier_info": "1,500 req/day free tier",
    },
    "openrouter": {
        "name": "OpenRouter",
        "free_tier_info": "Some models free - see openrouter.ai",
    },
    "together": {
        "name": "Together AI",
        "free_tier_info": "Free tier with rate limits",
    },
    "deepinfra": {
        "name": "DeepInfra",
        "free_tier_info": "Free tier with rate limits",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "free_tier_info": "Free tier with rate limits",
    },
}

def key_status(env_name):
    val = os.environ.get(env_name, "") or ""
    if val:
        return "\u2601", "configured"
    return "\u2717", "not set"

# Build model entries: (label, provider_id, model_name, is_free, desc, free_info, key_char, key_text)
entries = []
for pid, bc in cfg.items():
    if not isinstance(bc, dict): continue
    pname = bc.get("provider_name", pid)
    meta = PROVIDER_META.get(pid, {})
    desc = meta.get("name", pname)
    free_info = meta.get("free_tier_info", "")
    key_char, key_text = key_status(bc.get("api_key_env", ""))

    models_dict = bc.get("models")
    if models_dict and isinstance(models_dict, dict):
        for family in sorted(models_dict.keys()):
            is_free = family.startswith("Free")
            for m in models_dict[family]:
                entries.append((f"{family} > {m}", pid, m, is_free, desc, free_info, key_char, key_text))
        continue

    model = bc.get("model", "")
    entries.append((f"{model} ({desc})", pid, model or "", False, desc, free_info, key_char, key_text))

# Sort within categories
zen_paid = sorted([e for e in entries if e[1] == "zen" and not e[3]], key=lambda x: x[0].lower())
zen_free = sorted([e for e in entries if e[1] == "zen" and e[3]], key=lambda x: x[0].lower())
other_be = sorted([e for e in entries if e[1] != "zen"], key=lambda x: x[0].lower())

# ── MAIN MENU ─────────────────────────────────────────────────────────────
all_display = []   # (label, pid, model) for main menu
paid_submenu = []  # (label, pid, model) for paid submenu
idx = 1

# Entry 1: Paid Zen models (submenu)
if zen_paid:
    paid_count = len(zen_paid)
    print(f"  {idx:>3}) Paid Zen models (Claude, GPT, Gemini, etc. - requires ZEN_API_KEY)", file=sys.stderr)
    all_display.append(("Paid Zen models (submenu)", "zen", "__submenu__"))
    paid_submenu = [(label, pid, model) for label, pid, model, is_free, desc, free_info, kc, kt in zen_paid]
    idx += 1

# Other Providers: each model shown with free tier info
if other_be:
    print(f"\n{' Models with free tier access ':-^65}", file=sys.stderr)
    for label, pid, model, is_free, desc, free_info, kc, kt in other_be:
        free_tag = f" [{free_info}]" if free_info else " [free tier]"
        print(f"  {idx:>3}) {desc} > {model}  {free_tag}", file=sys.stderr)
        all_display.append((label, pid, model))
        idx += 1

# Free Zen models at the very bottom
if zen_free:
    print(f"{' Free Models (anonymous, no API key needed) ':-^65}", file=sys.stderr)
    for label, pid, model, is_free, desc, free_info, kc, kt in zen_free:
        has_expired = "expired" in label.lower()
        suffix = "  (promotion ended)" if has_expired else "  [no API key needed]"
        print(f"  {idx:>3}) {label}{suffix}", file=sys.stderr)
        all_display.append((label, pid, model))
        idx += 1

# ── SELECTION LOOP (menu + submenu) ────────────────────────────────────
entry = None
while True:
    print("\nSelect model:", file=sys.stderr)
    with open("/dev/tty", "r", encoding="utf-8") as tty:
        c = tty.readline().strip()
    if not c.isdigit(): print("Invalid.", file=sys.stderr); sys.exit(1)
    p = int(c) - 1
    if p < 0 or p >= len(all_display): print("Out of range.", file=sys.stderr); sys.exit(1)

    entry = all_display[p]

    # If submenu selected, show paid Zen models and collect sub-selection
    if entry[2] == "__submenu__":
        print(f"\n{' Paid Zen Models (set ZEN_API_KEY env var) ':-^65}", file=sys.stderr)
        for i, (label, pid, model) in enumerate(paid_submenu, 1):
            print(f"  {i:>3}) {label}", file=sys.stderr)
        if not os.environ.get("ZEN_API_KEY", ""):
            print(f"\n      * Set ZEN_API_KEY to access paid models", file=sys.stderr)
            print(f"      * Get one at https://opencode.ai/keys", file=sys.stderr)
        print("\nSelect model (or 0 for main menu):", file=sys.stderr)
        with open("/dev/tty", "r", encoding="utf-8") as tty:
            c = tty.readline().strip()
        if c == "0" or c.lower() == "b":
            continue  # Back to main menu
        if not c.isdigit(): print("Invalid.", file=sys.stderr); sys.exit(1)
        sp = int(c) - 1
        if sp < 0 or sp >= len(paid_submenu): print("Out of range.", file=sys.stderr); sys.exit(1)
        entry = paid_submenu[sp]

    break  # Exit loop with valid entry

print(f"{entry[1]}|{entry[2]}")
PY
}
# ── Proxy lifecycle ───────────────────────────────────────────────────────────
_claude_zen_ensure_proxy() {
    local dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    local pidf="${dir}/proxy.pid"
    local logf="${dir}/proxy.log"
    local proxy_script="${dir}/proxy.py"
    local backends_file="${ZEN_BACKENDS:-${dir}/backends.json}"
    local port="${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}"
    mkdir -p "$dir"
    if [ -f "$pidf" ]; then
        local pid; pid=$(cat "$pidf")
        kill -0 "$pid" 2>/dev/null && return 0
        rm -f "$pidf"
    fi
    if [ ! -f "$proxy_script" ]; then
        printf '\nProxy script not found at %s\n' "$proxy_script" >&2
        return 1
    fi
    # Determine Python: prefer dedicated proxy venv, fall back to system
    local proxy_python
    if [ -x "${dir}/proxy-venv/bin/python3" ]; then
        proxy_python="${dir}/proxy-venv/bin/python3"
    elif [ -f "${dir}/.USE_SYSTEM_PY" ]; then
        proxy_python="$(head -1 "${dir}/.USE_SYSTEM_PY" 2>/dev/null)"
        if [ ! -x "$proxy_python" ]; then
            proxy_python="$(command -v python3)"
        fi
else
        printf '\nProxy Python not found. Re-run setup_claude_zen_devcontainer.sh\n' >&2
        return 1
    fi

    # ── Kill any stale proxy process on our port ──────────────────────────
    # Orphaned proxies with outdated backends.json (e.g. be.model = "big-pickle"
    # from a previous script version) would hardcode the upstream model name,
    # silently breaking model switching.  Kill them so the new proxy loads the
    # current backends.json where model = "" (pass-through mode).
    local stale_pids
    stale_pids="$(pgrep -f "proxy\.py.*--port ${port}" 2>/dev/null || true)"
    if [ -n "$stale_pids" ]; then
        printf '  Stopping stale proxy process(es): %s\n' "$stale_pids"
        kill $stale_pids 2>/dev/null || true
        sleep 1
        local remaining
        remaining="$(pgrep -f "proxy\.py.*--port ${port}" 2>/dev/null || true)"
        if [ -n "$remaining" ]; then
            printf '  Force-killing stalled proxy(es): %s\n' "$remaining"
            kill -9 $remaining 2>/dev/null || true
            sleep 1
        fi
        # Clear stale PID file (ours or orphaned)
        rm -f "$pidf"
    fi

    "${proxy_python}" "$proxy_script" \
        --backends "$backends_file" \
        --port "$port" \
        >> "$logf" 2>&1 &
    echo $! > "$pidf"
    sleep 2
    if kill -0 $! 2>/dev/null; then
        printf '\nProxy started (PID %s), port %s\n' "$!" "$port"
        return 0
    fi
    printf '\nWarning: proxy may not have started. Check %s\n' "$logf" >&2
    return 1
}



# ── API Key resolution ──────────────────────────────────────────────────
# Called before launching Claude to ensure the provider's API key is set.
# Checks env var -> vault file -> prompts user interactively.
_claude_zen_resolve_key() {
    local pid="$1"
    local dir="$2"
    local model_name="$3"
    local key_vault="${dir}/key_vault.py"

    if [ ! -f "$key_vault" ]; then
        return 0
    fi

    local key_value
    key_value="$(python3 "$key_vault" resolve "$pid" \
        "${ZEN_BACKENDS:-${dir}/backends.json}" \
        "${dir}/api_keys.json" "$model_name")"

    if [ -n "$key_value" ]; then
        local key_env
        key_env="$(python3 -c "
import json
with open('${ZEN_BACKENDS:-${dir}/backends.json}') as f:
    cfg = json.load(f)
print(cfg.get('$pid', {}).get('api_key_env', ''))
" 2>/dev/null)"
        if [ -n "$key_env" ]; then
            export "$key_env=$key_value"
        fi
    fi
}
# ── Launch Claude via the proxy ──────────────────────────────────────────────
_claude_zen_find_claude() {
    local cmd
    cmd="$(command -v claude 2>/dev/null)" && { echo "$cmd"; return 0; }
    # Common locations in devcontainers
    for p in \
        /home/vscode/.npm-global/bin/claude \
        /root/.npm-global/bin/claude \
        /usr/local/bin/claude \
        /usr/bin/claude; do
        [ -x "$p" ] && { echo "$p"; return 0; }
    done
    # VS Code extension bundled binary (common in devcontainers)
    cmd="$(find /home/vscode/.vscode-server/extensions -maxdepth 4 -path '*/anthropic.claude-code-*/resources/native-binary/claude' -type f -executable 2>/dev/null | head -1)"
    [ -n "$cmd" ] && { echo "$cmd"; return 0; }
    # Broader search within node_modules
    cmd="$(find /home/vscode /root /usr/local -maxdepth 8 -name claude -type f -executable 2>/dev/null | head -1)"
    [ -n "$cmd" ] && { echo "$cmd"; return 0; }
    printf '\nError: claude binary not found. Install it with:\n  npm install -g @anthropic-ai/claude-code\n\n' >&2
    return 1
}

claude_zen_launch() {
    local sel provider_id model_name claude_bin dir
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    # Parse provider_id|model_name format
    if [[ "$sel" == *"|"* ]]; then
        provider_id="${sel%%|*}"
        model_name="${sel#*|}"
else
        # Legacy format: just provider_id
        provider_id="$sel"
        model_name=$(python3 -c "
import json
with open('${ZEN_BACKENDS:-${dir}/backends.json}') as f:
    cfg = json.load(f)
bc = cfg.get('$sel', {})
print(bc.get('model', '') or bc.get('provider_name', '$sel'))
" 2>/dev/null)
    fi
    printf 'Provider: %s  Model: %s\n' "$provider_id" "$model_name"
    _claude_zen_ensure_proxy || true
    claude_bin="$(_claude_zen_find_claude)"
    # Resolve API key for this provider (prompts if needed, checks vault)
    _claude_zen_resolve_key "$provider_id" "$dir" "$model_name"
    ZEN_DEFAULT_PROVIDER="$provider_id" \
    ANTHROPIC_API_KEY="freecc" \
    ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
    "$claude_bin" --model "$model_name" "$@"
}

# ── Danger mode: auto-accept permissions with git guardrails ────────────────
claude_zen_launch_danger() {
    local sel provider_id model_name claude_bin dir workspace_root danger_rules_file
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    # Derive workspace root: __PERSISTENCE_DIR__ = <root>/.claude_config
    workspace_root="${dir%/.claude_config}"
    [ -z "$workspace_root" ] && workspace_root="${dir%/*}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    # Parse provider_id|model_name format
    if [[ "$sel" == *"|"* ]]; then
        provider_id="${sel%%|*}"
        model_name="${sel#*|}"
else
        provider_id="$sel"
        model_name=$(python3 -c "
import json
with open('${ZEN_BACKENDS:-${dir}/backends.json}') as f:
    cfg = json.load(f)
bc = cfg.get('$sel', {})
print(bc.get('model', '') or bc.get('provider_name', '$sel'))
" 2>/dev/null)
    fi

    printf '\n'
    printf '  ⚠️  DANGER MODE\n'
    printf '  ────────────\n'
    printf '  Auto-accepting ALL permissions.\n'
    printf '  Provider: %s  Model: %s\n' "$provider_id" "$model_name"
    printf '\n'

    _claude_zen_ensure_proxy || true
    claude_bin="$(_claude_zen_find_claude)"
    # Resolve API key for this provider (prompts if needed, checks vault)
    _claude_zen_resolve_key "$provider_id" "$dir" "$model_name"

    # ── Install danger guardrails via helper ──────────────────────────────
    local backup_file
    backup_file="$(_claude_zen_install_danger_guardrails "$workspace_root" "$dir")"
    printf '\n'

    # ── Launch with auto-accept ────────────────────────────────────────────
    ZEN_DEFAULT_PROVIDER="$provider_id" \
    ANTHROPIC_API_KEY="freecc" \
    ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
    "$claude_bin" --model "$model_name" --dangerously-skip-permissions "$@"

    # ── Cleanup via helper ────────────────────────────────────────────────
    _claude_zen_cleanup_danger_guardrails "$workspace_root" "$dir" "$backup_file"
}

# Remove danger guardrails from CLAUDE.md without launching (cleanup utility)
claude_zen_uninstall_danger_rules() {
    local dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    local workspace_root="${dir%/.claude_config}"
    [ -z "$workspace_root" ] && workspace_root="${dir%/*}"
    local claude_md="${workspace_root}/CLAUDE.md"
    local backup_file="${dir}/danger/CLAUDE.md.bak"

    if [ -f "$backup_file" ] && [ -s "$backup_file" ]; then
        cp "$backup_file" "$claude_md"
        printf 'Restored original CLAUDE.md\n'
        rm -f "$backup_file"
    elif [ -f "$claude_md" ]; then
        # Check if it's our danger rules file
        if grep -q 'DANGER MODE GUARDRAILS' "$claude_md" 2>/dev/null; then
            rm -f "$claude_md"
            printf 'Removed danger CLAUDE.md\n'
        else
            printf 'CLAUDE.md is not a danger rules file — leaving untouched\n'
        fi
else
        printf 'No CLAUDE.md found\n'
    fi
}

claude_zen_cloud_launch() {
    local b; b="$(_claude_zen_find_claude)"
    "$b" "$@"
}

claude_zen_pick_model() {
    local sel provider_id model_name dir
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    if [[ "$sel" == *"|"* ]]; then
        provider_id="${sel%%|*}"
        model_name="${sel#*|}"
        printf 'Provider: %s  Model: %s\n' "$provider_id" "$model_name"
else
        printf 'Backend: %s\n' "$sel"
    fi
}

claude_zen_current_model() {
    local f provider_id model_name
    f="${CLAUDE_ZEN_MODEL_FILE:-${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}/selected-model}"
    if [ -f "$f" ]; then
        read -r sel < "$f"
        if [[ "$sel" == *"|"* ]]; then
            provider_id="${sel%%|*}"
            model_name="${sel#*|}"
            printf 'Provider: %s  Model: %s\n' "$provider_id" "$model_name"
        else
            printf 'Backend: %s\n' "$sel"
        fi
else
        echo "No model selected (run cz-model)"
    fi
}

claude_zen_proxy_start() { _claude_zen_ensure_proxy; }

claude_zen_proxy_stop() {
    local dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    local pidf="${dir}/proxy.pid"
    [ ! -f "$pidf" ] && echo "Proxy not running." && return 0
    local pid; pid=$(cat "$pidf")
    kill "$pid" 2>/dev/null && echo "Stopped PID $pid" || echo "Not running."
    rm -f "$pidf"
}

claude_zen_proxy_status() {
    local dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    local pidf="${dir}/proxy.pid"
    if [ -f "$pidf" ]; then
        local pid; pid=$(cat "$pidf")
        kill -0 "$pid" 2>/dev/null && echo "Proxy running: PID $pid, port ${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" && return 0
        rm -f "$pidf"
    fi
    echo "Proxy not running."; return 1
}

# ── Danger guardrail helpers (shared by launch and session-resume) ──────────
# Installs a temporary CLAUDE.md with git-restriction guardrails, backed up
# from the original so it can be restored after Claude exits.
_claude_zen_install_danger_guardrails() {
    local workspace_root="$1" dir="$2" claude_md danger_dir backup_file
    claude_md="${workspace_root}/CLAUDE.md"
    danger_dir="${dir}/danger"
    mkdir -p "$danger_dir"
    backup_file="${danger_dir}/CLAUDE.md.bak"
    if [ -f "$claude_md" ]; then
        cp "$claude_md" "$backup_file"
else
        rm -f "$backup_file"
        touch "$backup_file"
    fi
    local rules_file="${danger_dir}/danger_rules.md"
    if [ ! -f "$rules_file" ]; then
    cat > "$rules_file" << 'DANGEREOF'
# ⚠️ DANGER MODE GUARDRAILS — Do Not Remove

You are running with **automatic permission approval**. Every tool call you
make is executed WITHOUT confirmation. This is a safety-critical mode.

## MANDATORY RESTRICTIONS — Git write operations

Only the following **Staging & Read** operations are allowed:

### ✅ ALLOWED Git Operations
| Command | Purpose |
|---------|---------|
| `git add <file>` | Stage a file (fine-grained) |
| `git add -p` | Stage interactively by hunk |
| `git add -A` | Stage all changes |
| `git status` | View working tree state |
| `git diff` | View unstaged changes |
| `git diff --cached` | View staged changes |
| `git log` | View commit history |
| `git show` | View a commit |
| `git blame` | Annotate a file |
| `git restore <file>` | Discard unstaged local changes |
| `git stash push` | Save WIP temporarily |
| `git stash list` | View stashes |
| `git stash show` | View stash contents |

### ❌ FORBIDDEN Git Operations
| Operation | Reason |
|-----------|--------|
| `git commit` | Would record changes permanently |
| `git push` / `git push --force` | Would publish to remote |
| `git branch` / `git checkout -b` | Would create branches |
| `git merge` / `git rebase` | Would alter history |
| `git tag` | Would tag releases |
| `git fetch` / `git pull` | Would contact remote |
| `git reset --hard` / `git reset --mixed` | Destructive history reset |
| `git revert` / `git cherry-pick` | Would create new commits |
| `git rm` / `git mv` | Would remove/rename tracked files |
| `git submodule` | Complex git mutation |
| `git worktree` | Would create worktrees |
| `git gc` / `git prune` / `git repack` | Repository maintenance |
| `git clean -fd` / `-fdX` | Aggressive file removal |
| `git stash drop` / `git stash pop` / `git stash clear` | Destructive stash ops |
| `git config` (with global/system) | Would change git settings |

### File System Cautions
- You can read, write, and edit files normally.
- **Do not delete files** without the user explicitly asking — even though
  you auto-accept permissions, ask for verbal confirmation on deletes.
- **Do not run shell commands** that modify the system (install packages,
  change system config) without asking first.

### Enforcement
- If you are asked to do a forbidden git operation, say:
  "⛔ This operation is blocked by Danger Mode guardrails."
- If in doubt, err on the side of refusing. The user can always switch to
  normal mode (`cz`) for git-write operations.

DANGEREOF
    fi
    cp "$rules_file" "$claude_md"
    printf '  🔒 Danger guardrails installed (CLAUDE.md)\n'
    printf '%s' "$backup_file"
}

_claude_zen_cleanup_danger_guardrails() {
    local workspace_root="$1" dir="$2" backup_file="$3" exit_code="${4:-$?}"
    local claude_md="${workspace_root}/CLAUDE.md"
    if [ -f "$backup_file" ] && [ -s "$backup_file" ]; then
        cp "$backup_file" "$claude_md"
        printf '\n  ✅ Restored original CLAUDE.md\n'
    elif [ -f "$backup_file" ]; then
        rm -f "$claude_md"
        printf '\n  ✅ Removed danger CLAUDE.md (no original to restore)\n'
    fi
    rm -f "$backup_file"
    return "$exit_code"
}

# ── Session management: list & resume recent conversations ─────────────────
# These rely on the claude_persist (which survives devcontainer rebuilds),
# so past sessions are always findable even after fresh-install `cz`.
_claude_zen_derive_workspace_root() {
    local dir="${1:-${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}}"
    local root="${dir%/.claude_config}"
    [ -z "$root" ] && root="${dir%/*}"
    printf '%s' "$root"
}
_claude_zen_persist_dir() {
    local dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    # The persist dir is one level up from config: <workspace>/.claude_config => <workspace>
    local workspace_root="${dir%/.claude_config}"
    [ -z "$workspace_root" ] && workspace_root="${dir%/*}"
    # The real persist target that ~/.claude points to
    readlink -f "${HOME}/.claude" 2>/dev/null || echo "${workspace_root}/.claude_persist"
}

claude_zen_list_recent() {
    local persist claude_bin danger_mode dir workspace_root backup_file sel provider_id model_name
    danger_mode=0
    if [ "${1:-}" = "--danger" ]; then
        danger_mode=1
        shift
    fi
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    if [[ "$sel" == *"|"* ]]; then
        provider_id="${sel%%|*}"
        model_name="${sel#*|}"
else
        provider_id="$sel"
        model_name=$(python3 -c "
    import json
    with open('${ZEN_BACKENDS:-${dir}/backends.json}') as f:
        cfg = json.load(f)
    bc = cfg.get('$sel', {})
    print(bc.get('model', '') or bc.get('provider_name', '$sel'))
    " 2>/dev/null)
    fi
    persist="$(_claude_zen_persist_dir)"
    if [ ! -f "${persist}/history.jsonl" ]; then
        printf 'No session history found.\n' >&2
        return 1
    fi
    printf '\n  Recent sessions:\n'
    printf '  %s\n' '────────────────────────────────────────────────'
    # Show the most recent 10 sessions from history.jsonl with index numbers
    python3 - "${persist}/history.jsonl" << 'PY'
import json, sys
try:
    with open(sys.argv[1]) as f:
        entries = [json.loads(line) for line in f if line.strip()]
except FileNotFoundError:
    print("No history found.")
    sys.exit(0)
# Deduplicate: keep the most recent entry per session
seen = {}
for e in entries:
    sid = e.get("sessionId", "")
    seen[sid] = e  # last wins = most recent
unique = list(seen.values())
# Show last 10 (most recent first)
for i, e in enumerate(reversed(unique[-10:]), 1):
    disp = e.get("display", "")[:90]
    sid = e.get("sessionId", "")[:12]
    ts = e.get("timestamp", 0)
    print(f'  {i:>2}) [{sid}...] {disp}')
PY
    printf '\n  Enter number to resume, or 0 to start fresh: ' >&2
    read -r choice
    case "${choice}" in
        0|"") return 1 ;;
        *)
            # Pick the Nth recent unique session
            local sid
            claude_bin="$(_claude_zen_find_claude)" || return 1
            sid=$(python3 - "${persist}/history.jsonl" "${choice}" 2>/dev/null << 'PY'
import json, sys
with open(sys.argv[1]) as f:
    entries = [json.loads(line) for line in f if line.strip()]
seen = {}
for e in entries:
    seen[e.get("sessionId", "")] = e
unique = list(seen.values())
idx = len(unique) - int(sys.argv[2])
if 0 <= idx < len(unique):
    print(unique[idx]["sessionId"])
else:
    sys.exit(1)
PY
) || { printf 'Invalid choice.\n' >&2; return 1; }
            _claude_zen_ensure_proxy || true
            printf 'Resuming session %s...\n' "${sid}"
            if [ "$danger_mode" -eq 1 ]; then
                dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
                workspace_root="$(_claude_zen_derive_workspace_root "$dir")"
                backup_file="$(_claude_zen_install_danger_guardrails "$workspace_root" "$dir")"
                printf '  ⚠️  DANGER MODE — auto-accepting permissions\n\n' >&2
                env ZEN_DEFAULT_PROVIDER="${provider_id}" \
                    ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
                    ANTHROPIC_API_KEY="freecc" \
                    "${claude_bin}" --model "${model_name}" --resume "${sid}" --dangerously-skip-permissions "$@"
                _claude_zen_cleanup_danger_guardrails "$workspace_root" "$dir" "$backup_file"
            else
                exec env ZEN_DEFAULT_PROVIDER="${provider_id}" \
                    ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
                    ANTHROPIC_API_KEY="freecc" \
                    "${claude_bin}" --model "${model_name}" --resume "${sid}" "$@"
            fi
            ;;
    esac
}

claude_zen_resume_last() {
    local claude_bin danger_mode dir workspace_root backup_file
    danger_mode=0
    if [ "${1:-}" = "--danger" ]; then
        danger_mode=1
        shift
    fi
    local sel provider_id model_name
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    if [[ "$sel" == *"|"* ]]; then
        provider_id="${sel%%|*}"
        model_name="${sel#*|}"
else
        provider_id="$sel"
        model_name=$(python3 -c "
    import json
    with open('${ZEN_BACKENDS:-${dir}/backends.json}') as f:
        cfg = json.load(f)
    bc = cfg.get('$sel', {})
    print(bc.get('model', '') or bc.get('provider_name', '$sel'))
    " 2>/dev/null)
    fi
    claude_bin="$(_claude_zen_find_claude)" || return 1
    _claude_zen_ensure_proxy || true
    if [ "$danger_mode" -eq 1 ]; then
        dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
        workspace_root="$(_claude_zen_derive_workspace_root "$dir")"
        backup_file="$(_claude_zen_install_danger_guardrails "$workspace_root" "$dir")"
        printf '  ⚠️  DANGER MODE — auto-accepting permissions\n\n' >&2
        env ZEN_DEFAULT_PROVIDER="${provider_id}" \
            ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
            ANTHROPIC_API_KEY="freecc" \
            "${claude_bin}" --model "${model_name}" --continue --dangerously-skip-permissions "$@"
        _claude_zen_cleanup_danger_guardrails "$workspace_root" "$dir" "$backup_file"
else
        exec env ZEN_DEFAULT_PROVIDER="${provider_id}" \
            ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
            ANTHROPIC_API_KEY="freecc" \
            "${claude_bin}" --model "${model_name}" --continue "$@"
    fi
}

claude_zen_quick_resume() {
    # Resume by session ID prefix or full ID
    # Usage: claude_zen_quick_resume [--danger] <session-id-prefix> [extra-claude-args...]
    local claude_bin sid target session_id danger_mode dir workspace_root backup_file
    danger_mode=0
    if [ "${1:-}" = "--danger" ]; then
        danger_mode=1
        shift
    fi
    sid="$1"; shift
    local sel provider_id model_name
    claude_bin="$(_claude_zen_find_claude)" || return 1
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    if [[ "$sel" == *"|"* ]]; then
        provider_id="${sel%%|*}"
        model_name="${sel#*|}"
else
        provider_id="$sel"
        model_name=$(python3 -c "
    import json
    with open('${ZEN_BACKENDS:-${dir}/backends.json}') as f:
        cfg = json.load(f)
    bc = cfg.get('$sel', {})
    print(bc.get('model', '') or bc.get('provider_name', '$sel'))
    " 2>/dev/null)
    fi
    _claude_zen_ensure_proxy || true
    # Find the session file and extract the sessionId from its first line
    target=""
    for f in "$(_claude_zen_persist_dir)/projects/-workspace/"*.jsonl; do
        if [[ "$(basename "$f")" == "${sid}"* ]]; then
            target="$f"; break
        fi
    done
    if [ -z "$target" ] || [ ! -f "$target" ]; then
        printf 'Session not found: %s\n' "$sid" >&2
        return 1
    fi
    # Extract sessionId from first JSON line
    session_id=$(head -1 "$target" | python3 -c "import json,sys; print(json.load(sys.stdin).get('sessionId',''))" 2>/dev/null)
    if [ -z "$session_id" ]; then
        printf 'Could not read session ID from %s\n' "$target" >&2
        return 1
    fi
    printf 'Resuming session %s...\n' "$session_id"
    if [ "$danger_mode" -eq 1 ]; then
        dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
        workspace_root="$(_claude_zen_derive_workspace_root "$dir")"
        backup_file="$(_claude_zen_install_danger_guardrails "$workspace_root" "$dir")"
        printf '  ⚠️  DANGER MODE — auto-accepting permissions\n\n' >&2
        env ZEN_DEFAULT_PROVIDER="${provider_id}" \
            ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
            ANTHROPIC_API_KEY="freecc" \
            "${claude_bin}" --model "${model_name}" --resume "$session_id" --dangerously-skip-permissions "$@"
        _claude_zen_cleanup_danger_guardrails "$workspace_root" "$dir" "$backup_file"
else
        exec env ZEN_DEFAULT_PROVIDER="${provider_id}" \
            ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
            ANTHROPIC_API_KEY="freecc" \
            "${claude_bin}" --model "${model_name}" --resume "$session_id" "$@"
    fi
}

alias cz='claude_zen_launch'
alias cz-new='claude_zen_launch'
alias cz-danger='claude_zen_launch_danger'
alias cz-cloud='claude_zen_cloud_launch'
ccz() { local b; b="$(_claude_zen_find_claude)"; "$b" --continue "$@"; }
alias cz-model='claude_zen_pick_model'
alias cz-model-current='claude_zen_current_model'
alias cz-proxy-start='claude_zen_proxy_start'
alias cz-proxy-stop='claude_zen_proxy_stop'
alias cz-proxy-status='claude_zen_proxy_status'
alias cz-undo-danger='claude_zen_uninstall_danger_rules'
alias cz-recent='claude_zen_list_recent'
alias cz-last='claude_zen_resume_last'
alias cz-resume='claude_zen_quick_resume'
alias cz-danger-recent='claude_zen_list_recent --danger'
alias cz-danger-last='claude_zen_resume_last --danger'
alias cz-danger-resume='claude_zen_quick_resume --danger'
__MARKER_END__
WRAPEOF
}

_install_shell_wrappers() {
    local block; block="$(_wrapper_block)"
    for rc in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
        [ -f "$rc" ] || continue
        python3 - "$rc" "$MARKER_BEGIN" "$MARKER_END" "$block" << 'PY'
import pathlib, sys
p, start, end, repl = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
t = p.read_text()
si, ei = t.find(start), t.find(end)
if si != -1 and ei >= si:
    ei += len(end)
    if ei < len(t) and t[ei:ei+1] == "\n": ei += 1
    t = t[:si] + repl + "\n" + t[ei:]
else:
    t = t + ("\n" if t and not t.endswith("\n") else "") + repl + "\n"
p.write_text(t)
PY
        printf '  Updated %s\n' "$rc"
    done
}

_install_shell_wrappers

# ─── 7. Claude Code persistence (survives devcontainer rebuild) ─────────────
printf '\n%s\n' "=== Step 7: Claude Code persistence ==="
# The home directory (overlay) is wiped on devcontainer rebuild, but /workspace
# (host-mounted volume) persists. We migrate Claude Code's entire ~/.claude/
# config directory to the workspace and symlink it back, then also link the
# .ai_memory/ research files into the per-project memory slot.

CLAUDE_PERSIST_DIR="${SCRIPT_DIR}/.claude_persist"
CLAUDE_MEMORY_DIR="${SCRIPT_DIR}/.ai_memory"
# Always create both target dirs before ANY symlink or path-through-symlink
# mkdir calls. A dangling symlink (target dir missing) causes two failures:
#   1. Claude Code: ENOENT when creating jobs/sessions dirs under ~/.claude
#   2. mkdir -p on step 7b: "File exists" (can't traverse dangling symlink)
mkdir -p "${CLAUDE_PERSIST_DIR}"
mkdir -p "${CLAUDE_MEMORY_DIR}"

# ── 7a. Migrate ~/.claude → workspace ──────────────────────────────────────
if [ -L "${HOME}/.claude" ]; then
    CURRENT_TARGET="$(readlink "${HOME}/.claude")"
    if [ "${CURRENT_TARGET}" = "${CLAUDE_PERSIST_DIR}" ]; then
        printf '  ~/.claude already symlinked to workspace: %s\n' "${CLAUDE_PERSIST_DIR}"
else
        # Symlink points somewhere else (e.g. .claude_config/dot-claude from
        # an older ollama setup). Migrate data if the old target has content
        # and our persist dir is empty, then re-point to .claude_persist.
        printf '  ~/.claude was pointing to: %s — re-linking to %s\n' "${CURRENT_TARGET}" "${CLAUDE_PERSIST_DIR}"
        if [ -d "${CURRENT_TARGET}" ] && [ -z "$(ls -A "${CLAUDE_PERSIST_DIR}" 2>/dev/null)" ]; then
            printf '  Migrating Claude data...\n'
            cp -a "${CURRENT_TARGET}/." "${CLAUDE_PERSIST_DIR}/"
        fi
        rm -f "${HOME}/.claude"
        ln -sfn "${CLAUDE_PERSIST_DIR}" "${HOME}/.claude"
        printf '  Done: ~/.claude -> %s\n' "${CLAUDE_PERSIST_DIR}"
    fi
elif [ -d "${HOME}/.claude" ]; then
    if [ -z "$(ls -A "${HOME}/.claude" 2>/dev/null)" ]; then
        rm -rf "${HOME}/.claude"
else
        printf '  Migrating ~/.claude to %s ...\n' "${CLAUDE_PERSIST_DIR}"
        cp -a "${HOME}/.claude/." "${CLAUDE_PERSIST_DIR}/" && rm -rf "${HOME}/.claude"
        printf '  Done.\n'
    fi
    ln -sfn "${CLAUDE_PERSIST_DIR}" "${HOME}/.claude"
    printf '  Done: ~/.claude -> %s\n' "${CLAUDE_PERSIST_DIR}"
else
    # ~/.claude doesn't exist yet; create the symlink so future runs store data
    # in the workspace. Claude Code will create the directory structure on first use.
    ln -sfn "${CLAUDE_PERSIST_DIR}" "${HOME}/.claude"
    printf '  Created: ~/.claude -> %s (empty, populated on first Claude launch)\n' "${CLAUDE_PERSIST_DIR}"
fi

# ── 7b. Symlink per-project memory into .ai_memory ──────────────────────────
# Claude Code stores memory at $HOME/.claude/projects/<workspace-slug>/memory/
# The slug is the absolute workspace path with '/' replaced by '-'
# e.g. /workspace -> -workspace
WORKSPACE_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || realpath "${SCRIPT_DIR}")"
WORKSPACE_SLUG="$(echo "${WORKSPACE_ROOT}" | tr '/' '-')"
CLAUDE_MEMORY_LINK="${HOME}/.claude/projects/${WORKSPACE_SLUG}/memory"

mkdir -p "$(dirname "${CLAUDE_MEMORY_LINK}")"
if [ -e "${CLAUDE_MEMORY_LINK}" ] && [ ! -L "${CLAUDE_MEMORY_LINK}" ]; then
    printf '  WARNING: %s exists and is not a symlink. Skipping.\n' "${CLAUDE_MEMORY_LINK}"
elif [ -L "${CLAUDE_MEMORY_LINK}" ]; then
    ln -sfn "${CLAUDE_MEMORY_DIR}" "${CLAUDE_MEMORY_LINK}"
    printf '  Updated memory symlink: %s -> %s\n' "${CLAUDE_MEMORY_LINK}" "${CLAUDE_MEMORY_DIR}"
else
    ln -s "${CLAUDE_MEMORY_DIR}" "${CLAUDE_MEMORY_LINK}"
    printf '  Created memory symlink: %s -> %s\n' "${CLAUDE_MEMORY_LINK}" "${CLAUDE_MEMORY_DIR}"
fi

# ─── 8. Verify ────────────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 8: Smoke test ==="
if "${PROXY_VENV}/bin/python3" -c "
import sys; sys.path.insert(0, '${PERSISTENCE_DIR}')
from proxy import app
print('  Proxy module: OK')
" 2>&1; then
    printf '  Proxy module loaded OK\n'
else
    printf '  Warning: smoke test failed (proxy module may have syntax errors)\n'
fi

# ─── 9. Summary ───────────────────────────────────────────────────────────────
SHELL_RC=".bashrc"; case "${SHELL:-}" in *zsh) SHELL_RC=".zshrc" ;; esac

cat << SUMMARY

 Setup complete

  Persistence:  ${PERSISTENCE_DIR}
  Backends:     ${BACKENDS_FILE}
  Proxy port:   ${PROXY_PORT}
  Claude home:  ${CLAUDE_PERSIST_DIR} (symlinked to ~/.claude)
  Memory files: ${CLAUDE_MEMORY_DIR}
  Memory link:  ${CLAUDE_MEMORY_LINK}

  Activate:     source ~/${SHELL_RC}

  Commands:
    cz                  Pick a model -> launch Claude CLI (fresh session)
    cz-last             Continue the most recent conversation (quick resume)
    cz-danger-last      Same as cz-last but with auto-accept permissions
    cz-recent           List all recent sessions -> pick any to resume
    cz-danger-recent    Same as cz-recent but with auto-accept permissions
    cz-resume <id>      Resume a specific session by ID prefix
    cz-danger-resume <id>  Same as cz-resume but with auto-accept permissions
    cz-danger           Pick a model -> launch (auto-accept permissions)
    cz-model            Pick a model (no launch)
    cz-model-current    Show current model (provider + model name)
    cz-proxy-start      Start the proxy daemon
    cz-proxy-stop       Stop it
    cz-proxy-status     Check if running
    cz-undo-danger      Remove danger guardrails from workspace CLAUDE.md

  Coexistence with ollama setup (setup_claude_ollama_local_in_devcontainer.sh):
    cz and 'c' share the same ~/.claude -> .claule_persist/ for session history.
    Run them in separate windows to use different models, same sessions.
    Do NOT run them concurrently — shared state files can corrupt under simultaneous writes.

  Session persistence is handled automatically by the script.  All chats
  are stored in .claude_persist/ which survives devcontainer rebuilds.
  Use cz-last, cz-recent, or their -danger counterparts to pick up where
  you left off.

  Models dynamically discovered from: OpenCode Zen (free+paid), Groq, Google (Gemini), OpenRouter
  API keys are auto-prompted and stored in api_keys.json (one-time setup per provider)
  Edit backends.json to customize:
    ${BACKENDS_FILE}

SUMMARY
