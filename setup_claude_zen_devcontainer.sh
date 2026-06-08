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
#  5. All state lives in .claude_config_zen/ — does NOT touch .claude_config/
#
# ── How the proxy works ───────────────────────────────────────────────────────
#   Claude CLI speaks the Anthropic Messages API (POST /v1/messages with SSE).
#   OpenAI-compatible models speak the Chat Completions API (POST /v1/chat/completions).
#   This proxy translates between the two protocols:
#
#     claude  ──ANTHROPIC_BASE_URL──►  zen-proxy (:8083)  ──Chat Completions──►  upstream
#       (Anthropic Messages SSE)         ↕ translation          (OpenAI SSE)
#
# ── Independence from ollama+claude setup ─────────────────────────────────────
#   ┌──────────────────────┬───────────────────────────┬──────────────────────────┐
#   │                      │  ollama+claude setup      │  zen+claude setup (this) │
#   ├──────────────────────┼───────────────────────────┼──────────────────────────┤
#   │ Persistence dir      │ .claude_config/           │ .claude_config_zen/       │
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
#   cz         # pick a backend model → Claude CLI launches with Big Pickle
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
#          -d '{"model":"big-pickle","max_tokens":50,"messages":[{"role":"user","content":"Say hi in one word"}],"stream":false}' \
#          | python3 -m json.tool
#      Expected: response with content[].text like "Hi"
#
#   4. Test streaming chat via proxy (SSE events):
#        curl -s -N -X POST http://127.0.0.1:8083/v1/messages \
#          -H "Content-Type: application/json" -H "x-api-key: test" \
#          -d '{"model":"big-pickle","max_tokens":100,"messages":[{"role":"user","content":"Say hi in one word"}],"stream":true}'
#      Expected: SSE events: message_start → content_block_start → content_block_delta* → content_block_stop → message_delta → message_stop
#
#   5. Test end-to-end with Claude Code CLI print mode:
#        echo "Say hi" | ANTHROPIC_BASE_URL=http://127.0.0.1:8083 ANTHROPIC_API_KEY=test \
#          /path/to/claude --print --model big-pickle
#      Expected: Claude responds via the proxy (exit 0, prints response)
#
#   6. Use the shell wrapper (recommended):
#        source ~/.zshrc
#        echo "What model are you?" | cz -p
#      Expected: Claude responds mentioning Big Pickle / zen backend
#
# ── After setup: shell aliases ────────────────────────────────────────────────
#   cz              Pick a backend model and launch Claude CLI through the proxy
#   cz-new          Same as cz
#   cz-cloud        Launch Claude CLI directly (cloud, no proxy)
#   ccz             Continue most recent Claude cloud session
#   cz-model        Pick/change the default model
#   cz-model-current  Show currently selected model
#   cz-proxy-start  Start the proxy daemon (auto-started on first use)
#   cz-proxy-stop   Stop the proxy daemon
#   cz-proxy-status Check proxy daemon status
#
# ── Backends configuration ────────────────────────────────────────────────────
# Edit the JSON file at .claude_config_zen/backends.json:
#
#   {
#     "zen": {
#       "base_url": "https://opencode.ai/zen/v1",
#       "api_key_env": "ZEN_API_KEY",
#       "model": "big-pickle",
#       "provider_name": "ZEN"
#     },
#     "openai": {
#       "base_url": "https://api.openai.com/v1",
#       "api_key_env": "OPENAI_API_KEY",
#       "model": "gpt-4o",
#       "provider_name": "OpenAI"
#     }
#   }
#
# ── Environment variables ─────────────────────────────────────────────────────
#   CLAUDE_ZEN_CONFIG_DIR   Override persistence dir (default: .claude_config_zen)
#   CLAUDE_ZEN_PROXY_PORT   Override proxy port (default: 8083)
#   ZEN_API_KEY             API key for OpenCode Zen (optional for free models)
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
#   .claude_config_zen/
#   ├── backends.json        Backend provider definitions (edit to add more)
#   ├── proxy.py             Standalone translation proxy
#   ├── selected-model       Last selected MODEL= string
#   ├── proxy.log            Proxy daemon log
#   └── proxy.pid            Proxy daemon PID
#
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
PERSISTENCE_DIR="${CLAUDE_ZEN_CONFIG_DIR:-${SCRIPT_DIR}/.claude_config_zen}"
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

# ─── 1. Prerequisites: pip packages ───────────────────────────────────────────
printf '\n%s\n' "=== Step 1: Prerequisites ==="
pip3 install --break-system-packages -q fastapi uvicorn httpx tiktoken 2>&1 | tail -3 || {
    printf '  pip install failed. Trying apt packages...\n'
    sudo apt-get update -qq && sudo apt-get install -y -qq python3-pip
    pip3 install --break-system-packages -q fastapi uvicorn httpx tiktoken
}

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

# ─── 3. Proxy script ──────────────────────────────────────────────────────────
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
        model_id = be.model or f"{pid}/default"
        display = be.provider_name or pid
        models.append({
            "id": model_id,
            "display_name": f"{display} ({model_id})",
            "created_at": "2025-01-01T00:00:00Z",
            "type": "model",
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

# ─── 4. Backends config ───────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 5: Backends config ==="
if [ ! -f "${BACKENDS_FILE}" ]; then
    cat > "${BACKENDS_FILE}" << JSONEOF
{
    "zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "api_key": "",
        "model": "big-pickle",
        "provider_name": "ZEN"
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

# ─── 5. Shell wrappers ────────────────────────────────────────────────────────
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

unalias cz cz-new cz-cloud cz-continue ccz 2>/dev/null || true
unset -f cz cz_new ccz claude_zen_launch claude_zen_cloud_launch \
      claude_zen_pick_model claude_zen_current_model \
      _claude_zen_pick _claude_zen_ensure_proxy \
      claude_zen_proxy_start claude_zen_proxy_stop claude_zen_proxy_status 2>/dev/null || true

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
entries = []
for pid, bc in cfg.items():
    if not isinstance(bc, dict): continue
    pname = bc.get("provider_name", pid)
    model = bc.get("model", "")
    label = f"{model} ({pname})" if model else pname
    entries.append((label, pid))
entries.sort(key=lambda x: x[0].lower())
for i, (label, ref) in enumerate(entries, 1):
    print(f"  {i}) {label}", file=sys.stderr)
print("Select backend:", file=sys.stderr)
with open("/dev/tty", "r", encoding="utf-8") as tty:
    c = tty.readline().strip()
if not c.isdigit(): print("Invalid.", file=sys.stderr); sys.exit(1)
p = int(c) - 1
if p < 0 or p >= len(entries): print("Out of range.", file=sys.stderr); sys.exit(1)
print(entries[p][1])
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
    python3 "$proxy_script" \
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
    local sel model_name claude_bin dir
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    model_name=$(python3 -c "
import json
with open('${ZEN_BACKENDS:-${dir}/backends.json}') as f:
    cfg = json.load(f)
bc = cfg.get('$sel', {})
print(bc.get('model', '') or bc.get('provider_name', '$sel'))
" 2>/dev/null)
    printf 'Backend: %s  (%s)\n' "$sel" "$model_name"
    _claude_zen_ensure_proxy || true
    claude_bin="$(_claude_zen_find_claude)"
    ZEN_DEFAULT_PROVIDER="$sel" \
    ANTHROPIC_API_KEY="freecc" \
    ANTHROPIC_BASE_URL="http://127.0.0.1:${CLAUDE_ZEN_PROXY_PORT:-__PROXY_PORT__}" \
    "$claude_bin" --model "$model_name" "$@"
}

claude_zen_cloud_launch() {
    local b; b="$(_claude_zen_find_claude)"
    "$b" "$@"
}

claude_zen_pick_model() {
    local sel dir
    sel="$(_claude_zen_pick)" || return 1
    dir="${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}"
    mkdir -p "$dir"
    printf '%s\n' "$sel" > "${CLAUDE_ZEN_MODEL_FILE:-${dir}/selected-model}"
    printf 'Backend: %s\n' "$sel"
}

claude_zen_current_model() {
    local f="${CLAUDE_ZEN_MODEL_FILE:-${CLAUDE_ZEN_CONFIG_DIR:-__PERSISTENCE_DIR__}/selected-model}"
    [ -f "$f" ] && cat "$f" || echo "No model selected (run cz-model)"
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

alias cz='claude_zen_launch'
alias cz-new='claude_zen_launch'
alias cz-cloud='claude_zen_cloud_launch'
ccz() { local b; b="$(_claude_zen_find_claude)"; "$b" --continue "$@"; }
alias cz-model='claude_zen_pick_model'
alias cz-model-current='claude_zen_current_model'
alias cz-proxy-start='claude_zen_proxy_start'
alias cz-proxy-stop='claude_zen_proxy_stop'
alias cz-proxy-status='claude_zen_proxy_status'
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

# ─── 6. Verify ────────────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 7: Smoke test ==="
if python3 -c "
import sys; sys.path.insert(0, '${PERSISTENCE_DIR}')
from proxy import app
print('  Proxy module: OK')
" 2>&1; then
    printf '  Proxy module loaded OK\n'
else
    printf '  Warning: smoke test failed (proxy module may have syntax errors)\n'
fi

# ─── 7. Summary ───────────────────────────────────────────────────────────────
SHELL_RC=".bashrc"; case "${SHELL:-}" in *zsh) SHELL_RC=".zshrc" ;; esac

cat << SUMMARY

 Setup complete

  Persistence:  ${PERSISTENCE_DIR}
  Backends:     ${BACKENDS_FILE}
  Proxy port:   ${PROXY_PORT}

  Activate:     source ~/${SHELL_RC}

  Commands:
    cz              Pick a backend -> launch Claude CLI
    cz-model        Pick a backend (no launch)
    cz-model-current  Show current backend
    cz-proxy-start  Start the proxy daemon
    cz-proxy-stop   Stop it
    cz-proxy-status Check if running

  To add more backends (OpenAI, Groq, Together, etc.):
    ${BACKENDS_FILE}

SUMMARY
