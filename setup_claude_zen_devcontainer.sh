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
#         "Free":       ["deepseek-v4-flash-free", "big-pickle", ...]   # free — no key needed
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

# ─── 5. Backends config ───────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 5: Backends config ==="
if [ ! -f "${BACKENDS_FILE}" ]; then
    cat > "${BACKENDS_FILE}" << JSONEOF
{
    "zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "api_key": "",
        "model": "",
        "provider_name": "ZEN",
        "models": {
            "Claude": [
                "claude-fable-5",
                "claude-opus-4-8",
                "claude-opus-4-7",
                "claude-opus-4-6",
                "claude-opus-4-5",
                "claude-opus-4-1",
                "claude-sonnet-4-6",
                "claude-sonnet-4-5",
                "claude-sonnet-4",
                "claude-haiku-4-5"
            ],
            "GPT": [
                "gpt-5.5",
                "gpt-5.5-pro",
                "gpt-5.4",
                "gpt-5.4-pro",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
                "gpt-5.3-codex-spark",
                "gpt-5.3-codex",
                "gpt-5.2",
                "gpt-5.2-codex",
                "gpt-5.1",
                "gpt-5.1-codex-max",
                "gpt-5.1-codex",
                "gpt-5.1-codex-mini",
                "gpt-5",
                "gpt-5-codex",
                "gpt-5-nano"
            ],
            "Gemini": [
                "gemini-3.5-flash",
                "gemini-3.1-pro",
                "gemini-3-flash"
            ],
            "DeepSeek": [
                "deepseek-v4-pro",
                "deepseek-v4-flash"
            ],
            "xAI": [
                "grok-build-0.1"
            ],
            "Other": [
                "glm-5.1",
                "glm-5",
                "minimax-m2.7",
                "minimax-m2.5",
                "kimi-k2.6",
                "kimi-k2.5",
                "qwen3.6-plus",
                "qwen3.5-plus"
            ],
            "Free": [
                "deepseek-v4-flash-free",
                "mimo-v2.5-free",
                "qwen3.6-plus-free",
                "minimax-m3-free",
                "nemotron-3-ultra-free",
                "north-mini-code-free",
                "big-pickle"
            ]
        }
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "api_key": "",
        "model": "gpt-4o",
        "provider_name": "OpenAI"
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
    printf '  Created %s\n' "${BACKENDS_FILE}"
else
    printf '  Already exists: %s\n' "${BACKENDS_FILE}"
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

# Build model entries: (label, provider_id, model_name, api_key_env, is_free)
entries = []
for pid, bc in cfg.items():
    if not isinstance(bc, dict): continue
    pname = bc.get("provider_name", pid)
    api_key_env = bc.get("api_key_env", "")

    # Backend with multiple models grouped by family (e.g. Zen)
    models_dict = bc.get("models")
    if models_dict and isinstance(models_dict, dict):
        for family in sorted(models_dict.keys()):
            is_free = (family == "Free")
            for m in models_dict[family]:
                entries.append((f"{family} > {m}", pid, m, api_key_env, is_free))
        continue

    # Traditional single-model backend
    model = bc.get("model", "")
    label = f"{model} ({pname})" if model else pname
    entries.append((label, pid, model or "", api_key_env, False))

# Sort entries within each category
zen_paid = sorted([e for e in entries if e[1] == "zen" and not e[4]], key=lambda x: x[0].lower())
zen_free = sorted([e for e in entries if e[1] == "zen" and e[4]], key=lambda x: x[0].lower())
other_be = sorted([e for e in entries if e[1] != "zen"], key=lambda x: x[0].lower())

# Display in order: Paid -> Free -> Other, with continuous numbering
all_display = []
idx = 1

if zen_paid:
    print(f"\n{' Paid Models (set ZEN_API_KEY env var) ':-^65}", file=sys.stderr)
    for label, pid, model, key_env, is_free in zen_paid:
        print(f"  {idx:>3}) {label}", file=sys.stderr)
        all_display.append((label, pid, model))
        idx += 1
    if not os.environ.get("ZEN_API_KEY", ""):
        print(f"\n      * Set ZEN_API_KEY to access paid models", file=sys.stderr)
        print(f"      * Get one at https://opencode.ai/keys", file=sys.stderr)

if zen_free:
    print(f"{' Free Models (no API key needed) ':-^65}", file=sys.stderr)
    for label, pid, model, key_env, is_free in zen_free:
        print(f"  {idx:>3}) {label}", file=sys.stderr)
        all_display.append((label, pid, model))
        idx += 1

if other_be:
    print(f"{' Other Providers ':-^65}", file=sys.stderr)
    for label, pid, model, key_env, is_free in other_be:
        suff = f" (set ${key_env})" if key_env else ""
        print(f"  {idx:>3}) {label}{suff}", file=sys.stderr)
        all_display.append((label, pid, model))
        idx += 1

print("\nSelect model:", file=sys.stderr)
with open("/dev/tty", "r", encoding="utf-8") as tty:
    c = tty.readline().strip()
if not c.isdigit(): print("Invalid.", file=sys.stderr); sys.exit(1)
p = int(c) - 1
if p < 0 or p >= len(all_display): print("Out of range.", file=sys.stderr); sys.exit(1)

# Output: provider_id|model_name
print(f"{all_display[p][1]}|{all_display[p][2]}")
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

  Models are organized by family (Claude, GPT, Gemini, DeepSeek, etc.)
  Edit backends.json to add/remove models:
    ${BACKENDS_FILE}

SUMMARY
