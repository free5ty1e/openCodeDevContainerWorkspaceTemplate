#!/usr/bin/env bash
# ==============================================================================
# setup_claude_zen_devcontainer.sh
#
# Sets up claude-code-zen-proxy so Claude Code CLI can run through OpenCode Zen
# (or any OpenAI-compatible upstream) instead of the Anthropic API.
#
# ── What it does ──────────────────────────────────────────────────────────────
#  1. Installs Node.js 20+ (if missing) and @anthropic-ai/claude-code CLI
#  2. Clones claude-code-zen-proxy into the workspace
#  3. Installs npm dependencies
#  4. Creates .env.zen with your API key and model choice
#  5. Installs shell aliases (cz, cz-danger, ccz, etc.)
#  6. Migrates ~/.claude/ to workspace for persistence across rebuilds
#  7. Symlinks .ai_memory/ into Claude Code's per-project memory slot
#
# ── Architecture ──────────────────────────────────────────────────────────────
#
#   claude CLI ──ANTHROPIC_BASE_URL──► zen-proxy (:4041) ──chat/completions──► upstream
#     (Anthropic Messages SSE)          (Node.js)           (OpenAI SSE)
#
# ── Quick start ───────────────────────────────────────────────────────────────
#   ./setup_claude_zen_devcontainer.sh
#   source ~/.zshrc
#   cz              # pick a model, launch Claude Code through the proxy
#
# ── Shell aliases ─────────────────────────────────────────────────────────────
#   cz              Launch Claude Code through the zen proxy (model picker)
#   cz-new          Same as cz
#   cz-danger       Same, with --dangerously-skip-permissions
#   cz-cloud        Launch Claude Code directly (no proxy, Anthropic cloud)
#   ccz             Continue most recent Claude Code session
#   cz-model        Change the upstream model (edits .env.zen)
#   cz-model-current Show current model
#   cz-test-free-models  Ping every free model to see which respond vs rate-limit
#   cz-proxy-start  Start the proxy as a background daemon
#   cz-proxy-stop   Stop the daemon
#   cz-proxy-status Check if the proxy is running
#   cz-undo-danger  Remove danger guardrails from CLAUDE.md
#
# ── Requirements ──────────────────────────────────────────────────────────────
#   - Node.js 20+  (installed by script if missing)
#   - git
#   - curl
#   - An OpenCode Zen API key (or set UPSTREAM_API_KEY for another provider)
#
# ── Files created ─────────────────────────────────────────────────────────────
#   .claude_zen/
#   ├── repo/                  Cloned claude-code-zen-proxy
#   ├── .env.zen               Your environment config (API key, model)
#   ├── statusline.sh          Claude Code statusline (copied from .claude/)
#   ├── zen-claude-settings.json  Claude Code settings (env + statusLine)
#   └── proxy.log              Proxy daemon log (when using cz-proxy-start)
#
#   .claude_persist/           Full ~/.claude copy (survives rebuilds)
#   .ai_memory/                Claude Code memory files (survives rebuilds)
#
# ── Statusline ────────────────────────────────────────────────────────────────
#   Every cz / cz-danger launch points Claude Code at zen-claude-settings.json,
#   which wires in the statusline script (model, context usage %, git branch
#   with dirty count, cumulative session tokens, cache hit rate, cost).
#   The script is copied from (in order): ./statusline.sh, <workspace>/.claude/
#   statusline.sh, or ~/.claude/statusline.sh. To make it portable, keep a copy
#   of statusline.sh next to this setup script.
#   Customize:  .claude_zen/statusline.sh
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
PERSISTENCE_DIR="${SCRIPT_DIR}/.claude_zen"
REPO_DIR="${PERSISTENCE_DIR}/repo"
ENV_FILE="${PERSISTENCE_DIR}/.env.zen"
LOG_FILE="${PERSISTENCE_DIR}/proxy.log"
PID_FILE="${PERSISTENCE_DIR}/proxy.pid"
NPM_GLOBAL_DIR="${HOME}/.npm-global"
PROXY_PORT="${ZEN_PORT:-4041}"
MARKER_BEGIN="# >>> claude-zen-devcontainer >>>"
MARKER_END="# <<< claude-zen-devcontainer <<<"

# Statusline integration
STATUSLINE_SOURCE="${SCRIPT_DIR}/.claude/statusline.sh"
STATUSLINE_SCRIPT="${PERSISTENCE_DIR}/statusline.sh"
SETTINGS_FILE="${PERSISTENCE_DIR}/zen-claude-settings.json"

PROXY_REPO_URL="https://github.com/chandan11248/claude-code-zen-proxy.git"

have() { command -v "$1" >/dev/null 2>&1; }
export PATH="${NPM_GLOBAL_DIR}/bin:${PATH}"

# ─── 0. Kill any running proxy from previous install ────────────────────────
if [ -f "${PID_FILE}" ]; then
    OLD_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${OLD_PID}" ]; then
        kill "${OLD_PID}" 2>/dev/null || true
        sleep 1
    fi
    rm -f "${PID_FILE}"
fi

# ─── 1. Node.js ───────────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 1: Node.js ==="
if have node; then
    NODE_VER="$(node --version)"
    printf '  Found: %s\n' "${NODE_VER}"
    # Check major version >= 20
    NODE_MAJOR="${NODE_VER#v}"
    NODE_MAJOR="${NODE_MAJOR%%.*}"
    if [ "${NODE_MAJOR}" -lt 20 ] 2>/dev/null; then
        printf '  Warning: Node.js %s found but 20+ is required. Attempting upgrade...\n' "${NODE_VER}" >&2
        if have nvm; then
            nvm install 20
        elif have apt-get; then
            curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null
            sudo apt-get install -y nodejs 2>/dev/null
        fi
    fi
else
    printf '  Installing Node.js 20...\n'
    if have curl; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null
        sudo apt-get install -y nodejs 2>/dev/null
    else
        printf '  Error: curl not found. Install Node.js 20+ manually.\n' >&2
        exit 1
    fi
    if ! have node; then
        printf '  Error: Node.js installation failed. Install manually:\n' >&2
        printf '    https://nodejs.org/\n' >&2
        exit 1
    fi
    printf '  Installed: %s\n' "$(node --version)"
fi

# ─── 2. Claude Code CLI ───────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 2: Claude Code CLI ==="
if have claude; then
    printf '  Found: %s\n' "$(command -v claude)"
else
    printf '  Installing @anthropic-ai/claude-code via npm...\n'
    mkdir -p "${NPM_GLOBAL_DIR}"
    npm config set prefix "${NPM_GLOBAL_DIR}" 2>/dev/null || true
    npm install -g @anthropic-ai/claude-code 2>&1 || {
        printf '  WARNING: npm install failed. Install manually:\n'
        printf '    npm install -g @anthropic-ai/claude-code\n' >&2
    }
    if have claude; then
        printf '  Installed: %s\n' "$(command -v claude)"
    else
        printf '  Warning: claude not in PATH yet. It may be at %s/bin/claude\n' "${NPM_GLOBAL_DIR}"
        printf '  Restart your shell or run: export PATH="%s/bin:\${PATH}"\n' "${NPM_GLOBAL_DIR}"
    fi
fi

# ─── 3. Clone / update claude-code-zen-proxy ──────────────────────────────────
printf '\n%s\n' "=== Step 3: claude-code-zen-proxy ==="
mkdir -p "${PERSISTENCE_DIR}"
if [ -d "${REPO_DIR}/.git" ]; then
    printf '  Repo exists, pulling latest...\n'
    git -C "${REPO_DIR}" pull --ff-only 2>/dev/null || {
        printf '  Warning: git pull failed. Using existing version.\n'
    }
else
    printf '  Cloning claude-code-zen-proxy...\n'
    git clone --depth 1 "${PROXY_REPO_URL}" "${REPO_DIR}" 2>&1 || {
        printf '  Error: git clone failed.\n' >&2
        exit 1
    }
fi

# ─── 4. Install npm dependencies ──────────────────────────────────────────────
printf '\n%s\n' "=== Step 4: npm install ==="
cd "${REPO_DIR}"
npm install 2>&1 || {
    printf '  Error: npm install failed.\n' >&2
    exit 1
}
printf '  Dependencies installed.\n'

# ─── 5. Create .env.zen (preserve existing API key) ───────────────────────────
printf '\n%s\n' "=== Step 5: Environment config ==="
if [ -f "${ENV_FILE}" ]; then
    # Preserve existing API key
    EXISTING_KEY="$(sed -n 's/^UPSTREAM_API_KEY=//p' "${ENV_FILE}" 2>/dev/null | head -1 || true)"
    EXISTING_MODEL="$(sed -n 's/^UPSTREAM_MODEL=//p' "${ENV_FILE}" 2>/dev/null | head -1 || true)"
    printf '  Existing .env.zen found.'
    if [ -n "${EXISTING_KEY}" ]; then
        printf ' API key preserved.'
    fi
    if [ -n "${EXISTING_MODEL}" ]; then
        printf ' Model: %s.' "${EXISTING_MODEL}"
    fi
    printf '\n'
else
    # First-time setup: prompt for API key or use free model
    printf '\n'
    printf '  OpenCode Zen API key setup\n'
    printf '  ─────────────────────────\n'
    printf '  Free models (big-pickle, deepseek-v4-flash-free, etc.) work without a key.\n'
    printf '  Paid models (Claude, GPT, Gemini, etc.) require a ZEN_API_KEY.\n'
    printf '  Get one at: https://opencode.ai\n'
    printf '\n'
    printf '  Set your API key (or press Enter to use free models only): '
    READ_KEY=""
    if [ -t 0 ]; then
        read -r READ_KEY
    elif [ -t 1 ]; then
        read -r READ_KEY </dev/tty || true
    fi

    cat > "${ENV_FILE}" << ENVEOF
# claude-code-zen-proxy configuration
# https://github.com/chandan11248/claude-code-zen-proxy

# API key for OpenCode Zen (leave empty for free models only)
UPSTREAM_API_KEY=${READ_KEY}

# Model to use (change this to switch models)
UPSTREAM_MODEL=hy3-free

# Upstream endpoint
UPSTREAM_CHAT_COMPLETIONS_URL=https://opencode.ai/zen/v1/chat/completions

# Local model alias (how Claude Code sees the model)
ANTHROPIC_MODEL_ALIAS=claude-code-proxy

# Local proxy auth key (any string, just for local access)
PROXY_API_KEY=claude-zen-local-key

# DeepSeek thinking mode
DEEPSEEK_THINKING_TYPE=enabled

# Reasoning effort: xhigh, high, medium, low, minimal, none
UPSTREAM_REASONING_EFFORT=xhigh

# Proxy listen address
HOST=127.0.0.1
PORT=${PROXY_PORT}
ENVEOF
    printf '  Created %s\n' "${ENV_FILE}"
fi

# ─── 6. Shell aliases ─────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 6: Shell aliases ==="

_install_shell_wrappers() {
    local block
    block="$(_wrapper_block)"
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

_wrapper_block() {
    cat << 'WRAPEOF' | sed \
        -e "s|__MARKER_BEGIN__|${MARKER_BEGIN}|g" \
        -e "s|__MARKER_END__|${MARKER_END}|g" \
        -e "s|__PERSISTENCE_DIR__|${PERSISTENCE_DIR}|g" \
        -e "s|__REPO_DIR__|${REPO_DIR}|g" \
        -e "s|__ENV_FILE__|${ENV_FILE}|g" \
        -e "s|__PROXY_PORT__|${PROXY_PORT}|g" \
        -e "s|__NPM_GLOBAL_DIR__|${NPM_GLOBAL_DIR}|g"
__MARKER_BEGIN__
export PATH="__NPM_GLOBAL_DIR__/bin:${PATH}"

unalias cz cz-new cz-cloud cz-danger ccz cz-model cz-model-current \
       cz-proxy-start cz-proxy-stop cz-proxy-status cz-test-free-models \
       cz-undo-danger 2>/dev/null || true
unset -f cz cz_new ccz _cz_find_claude _cz_ensure_proxy \
          _cz_launch _cz_launch_danger _cz_model_pick _cz_model_current \
          _cz_test_free_models \
          _cz_zen_models_endpoint _cz_fetch_zen_models _cz_fetch_free_models \
          cz_proxy_start cz_proxy_stop cz_proxy_status 2>/dev/null || true

# ── Find the claude binary ─────────────────────────────────────────────────
_cz_find_claude() {
    local cmd
    cmd="$(command -v claude 2>/dev/null)" && { echo "$cmd"; return 0; }
    for p in \
        "${__NPM_GLOBAL_DIR__}/bin/claude" \
        /home/vscode/.npm-global/bin/claude \
        /root/.npm-global/bin/claude \
        /usr/local/bin/claude \
        /usr/bin/claude; do
        [ -x "$p" ] && { echo "$p"; return 0; }
    done
    cmd="$(find /home/vscode/.vscode-server/extensions -maxdepth 4 \
        -path '*/anthropic.claude-code-*/resources/native-binary/claude' \
        -type f -executable 2>/dev/null | head -1)"
    [ -n "$cmd" ] && { echo "$cmd"; return 0; }
    printf '\nError: claude not found. Install with:\n  npm install -g @anthropic-ai/claude-code\n\n' >&2
    return 1
}

# ── Ensure proxy is running ────────────────────────────────────────────────
_cz_ensure_proxy() {
    local dir="__PERSISTENCE_DIR__"
    local repo="__REPO_DIR__"
    local env_file="__ENV_FILE__"
    local port="${ZEN_PORT:-__PROXY_PORT__}"
    local pidf="${dir}/proxy.pid"
    local logf="${dir}/proxy.log"

    # Check if already running
    if [ -f "$pidf" ]; then
        local pid; pid=$(cat "$pidf" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$pidf"
    fi

    # Check if something is already on the port
    if command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
            printf '\n  Proxy already running on port %s (not tracked by us).\n' "$port" >&2
            return 0
        fi
    fi

    # Start the proxy
    if [ ! -d "$repo" ]; then
        printf '\n  Proxy repo not found at %s. Re-run setup.\n' "$repo" >&2
        return 1
    fi
    if [ ! -f "$env_file" ]; then
        printf '\n  .env.zen not found at %s. Re-run setup.\n' "$env_file" >&2
        return 1
    fi

    printf '\n  Starting zen proxy on port %s...\n' "$port"
    cd "$repo"
    set -a
    source "$env_file"
    set +a
    setsid nohup node src/server.js >> "$logf" 2>&1 < /dev/null &
    echo $! > "$pidf"
    disown 2>/dev/null || true
    sleep 2

    # Verify it started
    if kill -0 "$(cat "$pidf" 2>/dev/null)" 2>/dev/null; then
        printf '  Proxy started (PID %s)\n' "$(cat "$pidf")"
        return 0
    fi
    rm -f "$pidf"
    printf '\n  Warning: proxy may not have started. Check %s\n' "$logf" >&2
    return 1
}

# ── Launch Claude Code through the proxy ───────────────────────────────────
_cz_launch() {
    local dir="__PERSISTENCE_DIR__"
    local env_file="__ENV_FILE__"
    local port="${ZEN_PORT:-__PROXY_PORT__}"

    if [ ! -f "$env_file" ]; then
        printf 'Error: .env.zen not found. Re-run setup_claude_zen_devcontainer.sh\n' >&2
        return 1
    fi

    local settings_file="${dir}/zen-claude-settings.json"
    if [ ! -f "$settings_file" ]; then
        printf 'Error: settings file not found. Re-run setup_claude_zen_devcontainer.sh\n' >&2
        return 1
    fi

    # Read config from .env.zen
    set -a
    source "$env_file"
    set +a
    local api_key="${PROXY_API_KEY:-claude-zen-local-key}"
    local model_alias="${ANTHROPIC_MODEL_ALIAS:-claude-code-proxy}"

    _cz_ensure_proxy || true
    local claude_bin
    claude_bin="$(_cz_find_claude)" || return 1

    ANTHROPIC_BASE_URL="http://127.0.0.1:${port}" \
    ANTHROPIC_API_KEY="${api_key}" \
    ANTHROPIC_MODEL="${model_alias}" \
    "$claude_bin" --settings "$settings_file" "$@"
}

# ── Launch with danger mode (auto-accept) ─────────────────────────────────
_cz_launch_danger() {
    local dir="__PERSISTENCE_DIR__"
    local env_file="__ENV_FILE__"
    local workspace_root="${dir%/.claude_zen}"
    [ -z "$workspace_root" ] && workspace_root="${dir%/*}"

    if [ ! -f "$env_file" ]; then
        printf 'Error: .env.zen not found. Re-run setup_claude_zen_devcontainer.sh\n' >&2
        return 1
    fi

    local settings_file="${dir}/zen-claude-settings.json"
    if [ ! -f "$settings_file" ]; then
        printf 'Error: settings file not found. Re-run setup_claude_zen_devcontainer.sh\n' >&2
        return 1
    fi

    set -a
    source "$env_file"
    set +a
    local api_key="${PROXY_API_KEY:-claude-zen-local-key}"
    local model_alias="${ANTHROPIC_MODEL_ALIAS:-claude-code-proxy}"
    local port="${ZEN_PORT:-__PROXY_PORT__}"

    _cz_ensure_proxy || true
    local claude_bin
    claude_bin="$(_cz_find_claude)" || return 1

    # Install danger guardrails
    _cz_install_danger_guardrails "$workspace_root" "$dir"

    printf '\n'
    printf '  DANGER MODE\n'
    printf '  Auto-accepting ALL permissions.\n'
    printf '\n'

    ANTHROPIC_BASE_URL="http://127.0.0.1:${port}" \
    ANTHROPIC_API_KEY="${api_key}" \
    ANTHROPIC_MODEL="${model_alias}" \
    "$claude_bin" --settings "$settings_file" --dangerously-skip-permissions "$@"
}

# ── Cloud launch (direct Anthropic, no proxy) ─────────────────────────────
_cz_cloud_launch() {
    local b; b="$(_cz_find_claude)" || return 1
    "$b" "$@"
}

# ── Dynamic model discovery (free models change frequently) ───────────────
_cz_zen_models_endpoint() {
    local env_file="__ENV_FILE__"
    local url="https://opencode.ai/zen/v1/chat/completions"
    local eu
    if [ -f "$env_file" ]; then
        eu="$(sed -n 's/^UPSTREAM_CHAT_COMPLETIONS_URL=//p' "$env_file" 2>/dev/null | head -1)"
        [ -n "$eu" ] && url="$eu"
    fi
    case "$url" in
        */models)         printf '%s\n' "$url" ;;
        */chat/completions) printf '%s/models\n' "${url%/chat/completions}" ;;
        *)                printf '%s/models\n' "${url%/}" ;;
    esac
}

_cz_fetch_zen_models() {
    # Fetch the live model id list from the upstream /models endpoint.
    # Prints one model id per line. Uses a short-lived cache with offline fallback.
    local cache="__PERSISTENCE_DIR__/zen_models_cache.txt"

    # serve from cache if fetched within the last 10 minutes
    if [ -f "$cache" ] && [ -n "$(find "$cache" -mmin -10 -print -quit 2>/dev/null)" ]; then
        sed '/^[[:space:]]*$/d;/^#/d' "$cache"
        return 0
    fi

    local murl fetched
    murl="$(_cz_zen_models_endpoint)"
    fetched="$(curl -s --connect-timeout 5 --max-time 12 "$murl" 2>/dev/null | python3 -c '
import json, sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

ids = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
print("\n".join(ids))
' 2>/dev/null)"

    if [ -n "$fetched" ]; then
        printf '# cached %s from %s\n' "$(date +%Y-%m-%dT%H:%M%S)" "$murl" > "$cache"
        printf '%s\n' "$fetched" >> "$cache"
        printf '%s\n' "$fetched"
    else
        printf '%s\n' \
            "big-pickle" "hy3-free" "laguna-s-2.1-free" "ling-3.0-flash-fin-free" \
            "deepseek-v4-flash-free" "nemotron-3-ultra-free" "muse-spark-1.2-contributor-free" \
            "mimo-v2.5-free" "nemotron-3.5-lightning-free" \
            "claude-sonnet-4-6" "gpt-5.5" "gemini-3.5-flash" "deepseek-v4-flash" \
            "glm-5.1" "kimi-k2.6" "minimax-m2.7" "qwen3.5-plus"
    fi
}

_cz_fetch_free_models() {
    _cz_fetch_zen_models | grep -xE 'big-pickle|[a-zA-Z0-9._+-]+-free' || true
}

# ── Model picker (dynamic: mirrors the live upstream model list) ──────────
_cz_model_pick() {
    local env_file="__ENV_FILE__"

    if [ ! -f "$env_file" ]; then
        printf 'Error: .env.zen not found. Re-run setup.\n' >&2
        return 1
    fi

    local current_model
    current_model="$(sed -n 's/^UPSTREAM_MODEL=//p' "$env_file" 2>/dev/null | head -1)"
    [ -z "$current_model" ] && current_model="hy3-free"

    local all_list free_list paid_list
    all_list="$(mktemp 2>/dev/null || printf '/tmp/cz-all-%s' "$$")"
    free_list="$(mktemp 2>/dev/null || printf '/tmp/cz-free-%s' "$$")"
    paid_list="$(mktemp 2>/dev/null || printf '/tmp/cz-paid-%s' "$$")"

    _cz_fetch_zen_models > "$all_list"
    grep -xE 'big-pickle|[a-zA-Z0-9._+-]+-free' "$all_list" > "$free_list"
    grep -vxE 'big-pickle|[a-zA-Z0-9._+-]+-free' "$all_list" > "$paid_list"

    local free_count paid_count n paid_start custom_num choice new_model idx pid pidf m
    free_count="$(wc -l < "$free_list" | tr -d ' ')"
    paid_count="$(wc -l < "$paid_list" | tr -d ' ')"
    paid_start=$((free_count + 1))
    custom_num=$((free_count + paid_count + 1))

    printf '\n'
    printf '  Current model: %s\n' "$current_model"
    printf '  Tip: run cz-test-free-models to see which free models respond right now.\n'
    printf '\n'
    if [ "$free_count" -gt 0 ]; then
        printf '  Free models (no API key needed):\n'
        n=0
        while IFS= read -r m; do
            n=$((n + 1))
            printf '  %2d) %s\n' "$n" "$m"
        done < "$free_list"
    else
        printf '  (No free models found upstream)\n'
    fi
    printf '\n'
    if [ "$paid_count" -gt 0 ]; then
        printf '  Paid models (requires UPSTREAM_API_KEY in .env.zen):\n'
        n=$paid_start
        while IFS= read -r m; do
            printf '  %2d) %s\n' "$n" "$m"
            n=$((n + 1))
        done < "$paid_list"
    else
        printf '  (No paid models found upstream)\n'
    fi
    printf '\n'
    printf '  %2d) Enter custom model name\n' "$custom_num"
    printf '\n'
    printf '  Select model (1-%d, or Enter to keep current): ' "$custom_num"

    if [ -t 0 ]; then
        read -r choice
    elif [ -t 1 ]; then
        read -r choice </dev/tty || true
    fi

    new_model=""
    if [ -z "$choice" ]; then
        printf '  Keeping current model: %s\n' "$current_model"
        rm -f "$all_list" "$free_list" "$paid_list"
        return 0
    fi

    if printf '%s' "$choice" | grep -qE '^[0-9]+$'; then
        if [ "$choice" -ge 1 ] && [ "$choice" -le "$free_count" ]; then
            new_model="$(sed -n "${choice}p" "$free_list")"
        elif [ "$choice" -ge "$paid_start" ] && [ "$choice" -lt "$custom_num" ]; then
            idx=$((choice - paid_start + 1))
            new_model="$(sed -n "${idx}p" "$paid_list")"
        elif [ "$choice" -eq "$custom_num" ]; then
            printf '  Enter model name: '
            if [ -t 0 ]; then
                read -r new_model
            elif [ -t 1 ]; then
                read -r new_model </dev/tty || true
            fi
        else
            printf '  Invalid choice.\n' >&2
            rm -f "$all_list" "$free_list" "$paid_list"
            return 1
        fi
    else
        printf '  Invalid choice.\n' >&2
        rm -f "$all_list" "$free_list" "$paid_list"
        return 1
    fi

    if [ -n "$new_model" ]; then
        sed -i "s|^UPSTREAM_MODEL=.*|UPSTREAM_MODEL=${new_model}|" "$env_file"
        printf '  Model set to: %s\n' "$new_model"

        # Restart proxy if running to pick up the change
        pidf="__PERSISTENCE_DIR__/proxy.pid"
        if [ -f "$pidf" ]; then
            pid=$(cat "$pidf" 2>/dev/null || true)
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                printf '  Restarting proxy to apply model change...\n'
                kill "$pid" 2>/dev/null || true
                sleep 1
                rm -f "$pidf"
                _cz_ensure_proxy || true
            else
                rm -f "$pidf"
                _cz_ensure_proxy || true
            fi
        fi
    fi
    rm -f "$all_list" "$free_list" "$paid_list"
}

# ── Show current model ────────────────────────────────────────────────────
_cz_model_current() {
    local env_file="__ENV_FILE__"
    if [ -f "$env_file" ]; then
        local model
        model="$(sed -n 's/^UPSTREAM_MODEL=//p' "$env_file" 2>/dev/null | head -1)"
        [ -z "$model" ] && model="(not set)"
        printf 'Model: %s\n' "$model"
    else
        printf 'No .env.zen found. Run setup first.\n'
    fi
}

# ── Test free models (which respond vs which are rate-limited) ───────────
_cz_test_free_models() {
    local env_file="__ENV_FILE__"
    local upstream_url="https://opencode.ai/zen/v1/chat/completions"

    if [ -f "$env_file" ]; then
        local env_upstream
        env_upstream="$(sed -n 's/^UPSTREAM_CHAT_COMPLETIONS_URL=//p' "$env_file" 2>/dev/null | head -1)"
        [ -n "$env_upstream" ] && upstream_url="$env_upstream"
    fi

    local free_list
    free_list="$(mktemp 2>/dev/null || printf '/tmp/cz-test-free-%s' "$$")"
    _cz_fetch_free_models > "$free_list" 2>/dev/null

    local ok_models="" limited_models="" dead_models=""
    local model tmp payload code msg result

    printf '\n'
    printf '  Testing free models -> %s\n' "$upstream_url"
    printf '  List is fetched live from the upstream /models endpoint.\n'
    printf '  Each sends one short prompt. 200 = responding, 429 = rate limited.\n'
    printf '\n'
    printf '  %-26s %6s  %s\n' "MODEL" "CODE" "RESULT"
    printf '  %-26s %6s  %s\n' "-----" "----" "------"

    while IFS= read -r model; do
        [ -z "$model" ] && continue
        tmp="$(mktemp 2>/dev/null || printf '/tmp/cz-probe-%s' "$$")"
        payload="{\"model\":\"${model}\",\"stream\":false,\"max_completion_tokens\":15,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OK\"}]}"

        code="$(curl -s -m 60 -o "$tmp" -w '%{http_code}' \
            -H "content-type: application/json" \
            -d "$payload" \
            "$upstream_url" 2>/dev/null || printf '000')"

        msg="$(python3 - "$tmp" << 'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    err = d.get("error") or {}
    if isinstance(err, dict):
        m = err.get("message", "")
    else:
        m = d.get("message", "")
    if not m:
        m = ""
    print(m[:110])
except Exception:
    exit(1)
PY
)"
        [ -z "$msg" ] && msg="<no response body>"

        case "$code" in
            200) result="OK - responding";      ok_models="${ok_models} ${model}" ;;
            429) result="rate limited";          limited_models="${limited_models} ${model}" ;;
            *)   result="$msg";                  dead_models="${dead_models} ${model}" ;;
        esac

        printf '  %-26s %6s  %s\n' "$model" "$code" "$result"
        rm -f "$tmp"
    done < "$free_list"
    rm -f "$free_list"

    printf '\n'
    if [ -n "$ok_models" ]; then
        printf '  Working: %s\n' "$ok_models"
    fi
    if [ -n "$limited_models" ]; then
        printf '  Rate limited right now: %s\n' "$limited_models"
    fi
    if [ -n "$dead_models" ]; then
        printf '  Unavailable/erroring: %s\n' "$dead_models"
    fi
    printf '\n'
    printf '  Switch to a working model with: cz-model\n'
    printf '\n'
}

# ── Proxy daemon management ───────────────────────────────────────────────
cz_proxy_start() {
    local dir="__PERSISTENCE_DIR__"
    local pidf="${dir}/proxy.pid"
    if [ -f "$pidf" ]; then
        local pid; pid=$(cat "$pidf" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            printf 'Proxy already running (PID %s)\n' "$pid"
            return 0
        fi
        rm -f "$pidf"
    fi
    _cz_ensure_proxy
}

cz_proxy_stop() {
    local dir="__PERSISTENCE_DIR__"
    local pidf="${dir}/proxy.pid"
    if [ -f "$pidf" ]; then
        local pid; pid=$(cat "$pidf" 2>/dev/null || true)
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null && printf 'Stopped proxy (PID %s)\n' "$pid" || printf 'Proxy not running.\n'
        fi
        rm -f "$pidf"
    else
        printf 'Proxy not running.\n'
    fi
}

cz_proxy_status() {
    local dir="__PERSISTENCE_DIR__"
    local pidf="${dir}/proxy.pid"
    if [ -f "$pidf" ]; then
        local pid; pid=$(cat "$pidf" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            printf 'Proxy running: PID %s, port %s\n' "$pid" "${ZEN_PORT:-__PROXY_PORT__}"
            return 0
        fi
        rm -f "$pidf"
    fi
    printf 'Proxy not running.\n'
    return 1
}

# ── Danger guardrails ─────────────────────────────────────────────────────
_cz_install_danger_guardrails() {
    local workspace_root="$1" dir="$2"
    local claude_md="${workspace_root}/CLAUDE.md"
    local danger_dir="${dir}/danger"
    mkdir -p "$danger_dir"
    local backup_file="${danger_dir}/CLAUDE.md.bak"
    local rules_file="${danger_dir}/danger_rules.md"

    if [ -f "$claude_md" ]; then
        cp "$claude_md" "$backup_file"
    else
        rm -f "$backup_file"
        touch "$backup_file"
    fi

    cat > "$rules_file" << 'DANGEREOF'
# --- DANGER GUARDRAILS START ---
# DANGER MODE GUARDRAILS — Do Not Remove

You are running with **automatic permission approval**. Every tool call you
make is executed WITHOUT confirmation. This is a safety-critical mode.

## MANDATORY RESTRICTIONS — Git write operations

Only the following **Staging & Read** operations are allowed:

### ALLOWED Git Operations
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

### FORBIDDEN Git Operations
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

### File System Cautions
- You can read, write, and edit files normally.
- **Do not delete files** without the user explicitly asking.

### Enforcement
- If you are asked to do a forbidden git operation, refuse.
- If in doubt, err on the side of refusing.

## MANDATORY RESTRICTIONS — gh (GitHub CLI)

Only read operations and updating PR descriptions via `gh edit` are permitted.

### FORBIDDEN gh Operations
| Operation | Reason |
|-----------|--------|
| `gh pr create` / `gh pr merge` / `gh pr close` | Would create or modify pull requests |
| `gh issue create` / `gh issue close` | Would modify issues |
| `gh release create` | Would create releases |
| `gh repo fork` / `gh repo create` / `gh repo delete` | Would create or delete repositories |

# --- DANGER GUARDRAILS END ---
DANGEREOF

    local start_marker="# --- DANGER GUARDRAILS START ---"
    local end_marker="# --- DANGER GUARDRAILS END ---"
    local guardrails
    guardrails="$(cat "$rules_file")"

    if [ ! -f "$claude_md" ]; then
        printf '# Project Instructions\n\n%s\n' "$guardrails" > "$claude_md"
    elif grep -qF "$start_marker" "$claude_md"; then
        sed "/$start_marker/,/$end_marker/d" "$claude_md" > "${claude_md}.tmp"
        cat "${claude_md}.tmp" > "$claude_md"
        printf '\n%s\n' "$guardrails" >> "$claude_md"
        rm -f "${claude_md}.tmp"
    else
        printf '\n%s\n' "$guardrails" >> "$claude_md"
    fi
}

_cz_uninstall_danger_rules() {
    local dir="__PERSISTENCE_DIR__"
    local workspace_root="${dir%/.claude_zen}"
    [ -z "$workspace_root" ] && workspace_root="${dir%/*}"
    local claude_md="${workspace_root}/CLAUDE.md"
    local backup_file="${dir}/danger/CLAUDE.md.bak"

    if [ -f "$backup_file" ] && [ -s "$backup_file" ]; then
        cp "$backup_file" "$claude_md"
        printf 'Restored original CLAUDE.md\n'
        rm -f "$backup_file"
    elif [ -f "$claude_md" ]; then
        if grep -q '# --- DANGER GUARDRAILS START ---' "$claude_md" 2>/dev/null; then
            local start_marker="# --- DANGER GUARDRAILS START ---"
            local end_marker="# --- DANGER GUARDRAILS END ---"
            sed "/$start_marker/,/$end_marker/d" "$claude_md" > "${claude_md}.tmp"
            if [ -s "${claude_md}.tmp" ]; then
                mv "${claude_md}.tmp" "$claude_md"
            else
                rm -f "$claude_md" "${claude_md}.tmp"
            fi
            printf 'Removed danger guardrails from CLAUDE.md\n'
        else
            printf 'CLAUDE.md has no danger guardrails — leaving untouched\n'
        fi
    else
        printf 'No CLAUDE.md found\n'
    fi
}

# ── Aliases ────────────────────────────────────────────────────────────────
cz()             { _cz_launch "$@"; }
cz-new()         { _cz_launch "$@"; }
cz-danger()      { _cz_launch_danger "$@"; }
cz-cloud()       { _cz_cloud_launch "$@"; }
ccz()            { local b; b="$(_cz_find_claude)" || return 1; "$b" --continue "$@"; }
cz-model()       { _cz_model_pick "$@"; }
cz-model-current() { _cz_model_current "$@"; }
cz-test-free-models() { _cz_test_free_models "$@"; }
cz-proxy-start() { cz_proxy_start "$@"; }
cz-proxy-stop()  { cz_proxy_stop "$@"; }
cz-proxy-status(){ cz_proxy_status "$@"; }
cz-undo-danger() { _cz_uninstall_danger_rules "$@"; }

cz-help() {
    cat << 'HELPEOF'
Claude Zen — Claude Code through OpenCode Zen (or any OpenAI-compatible API)

USAGE:
  cz / cz-new           Pick a model, launch Claude Code through the proxy
  cz-danger             Same, with auto-accept permissions (--dangerously-skip-permissions)
  cz-cloud              Launch Claude Code directly (Anthropic cloud, no proxy)
  ccz                   Resume most recent Claude Code session

MODEL:
  cz-model              Change the upstream model (interactive picker)
  cz-model-current      Show currently selected model
  cz-test-free-models   Ping every free model - see which respond vs rate-limit

PROXY:
  cz-proxy-start        Start the translation proxy as a background daemon
  cz-proxy-stop         Stop the proxy daemon
  cz-proxy-status       Check if the proxy is running

OTHER:
  cz-undo-danger        Remove danger-mode guardrails from CLAUDE.md
  cz-help               Show this help

HOW IT WORKS:
  claude CLI ──Anthropic API──► zen-proxy (:4041) ──chat/completions──► OpenCode Zen

  The proxy translates between Claude Code's Anthropic Messages API and
  OpenAI-compatible chat completions. You can use free models without an
  API key, or set UPSTREAM_API_KEY in .env.zen for paid models.

  Config file: .claude_zen/.env.zen
  Proxy repo:  .claude_zen/repo/
  Statusline:  .claude_zen/statusline.sh (wired via zen-claude-settings.json)

HELPEOF
}

__MARKER_END__
WRAPEOF
}

_install_shell_wrappers

# ─── 6.5. Statusline integration ────────────────────────────────────────────
printf '\n%s\n' "=== Step 6.5: Statusline integration ==="
# The workspace ships with a rich statusline script (model, context usage,
# git branch, cost, cache hit rate). Wire it into Claude Code's settings so
# it shows in the terminal status panel for every zen session.

# jq (JSON parsing) and bc (arithmetic) are required by statusline.sh
for _tool in jq bc; do
    if have "$_tool"; then
        printf '  %s: found\n' "$_tool"
    else
        printf '  Installing %s (required by statusline)...\n' "$_tool"
        if have apt-get; then
            if { sudo apt-get install -y "$_tool" || apt-get install -y "$_tool"; } >/dev/null 2>&1; then
                :
            elif { sudo apt-get update || apt-get update; } >/dev/null 2>&1; then
                printf '    (updated package lists, retrying)\n'
                { sudo apt-get install -y "$_tool" || apt-get install -y "$_tool"; } >/dev/null 2>&1 || {
                    printf '  Warning: could not install %s. Statusline will not render fully.\n' "$_tool" >&2
                }
            else
                printf '  Warning: could not update apt. Statusline will not render fully.\n' >&2
            fi
        else
            printf '  Warning: %s not found. Statusline will not render fully.\n' "$_tool" >&2
        fi
    fi
done

# Copy statusline.sh into the persistence dir so it survives rebuilds.
# Source lookup order:
#   1. .claude_zen/statusline.sh        (already installed — idempotent)
#   2. ./statusline.sh                  (kept next to this script — portable)
#   3. <workspace>/.claude/statusline.sh  (this devcontainer's copy)
#   4. ~/.claude/statusline.sh          (home copy)
if [ -f "${STATUSLINE_SCRIPT}" ]; then
    chmod +x "${STATUSLINE_SCRIPT}" 2>/dev/null || true
    printf '  Statusline already installed: %s\n' "${STATUSLINE_SCRIPT}"
elif [ -f "${SCRIPT_DIR}/statusline.sh" ]; then
    cp "${SCRIPT_DIR}/statusline.sh" "${STATUSLINE_SCRIPT}"
    chmod +x "${STATUSLINE_SCRIPT}"
    printf '  Copied statusline: %s -> %s\n' "${SCRIPT_DIR}/statusline.sh" "${STATUSLINE_SCRIPT}"
elif [ -f "${STATUSLINE_SOURCE}" ]; then
    cp "${STATUSLINE_SOURCE}" "${STATUSLINE_SCRIPT}"
    chmod +x "${STATUSLINE_SCRIPT}"
    printf '  Copied statusline: %s -> %s\n' "${STATUSLINE_SOURCE}" "${STATUSLINE_SCRIPT}"
elif [ -f "${HOME}/.claude/statusline.sh" ]; then
    cp "${HOME}/.claude/statusline.sh" "${STATUSLINE_SCRIPT}"
    chmod +x "${STATUSLINE_SCRIPT}"
    printf '  Copied statusline from %s\n' "${HOME}/.claude/statusline.sh"
else
    printf '  Warning: no statusline.sh found. Creating placeholder.\n' >&2
    cat > "${STATUSLINE_SCRIPT}" << 'SL'
#!/bin/bash
# Placeholder statusline - drop your own at .claude_zen/statusline.sh
input=$(cat)
model=$(echo "$input" | jq -r '.model.display_name // "claude"' 2>/dev/null)
printf "🤖 %s\n" "$model"
SL
    chmod +x "${STATUSLINE_SCRIPT}"
fi

# Build a workspace copy of the settings file (repo's zen-claude-settings.json
# + statusLine). Do NOT edit the repo copy - a git pull would wipe it.
if [ -f "${REPO_DIR}/zen-claude-settings.json" ]; then
    python3 - "${REPO_DIR}/zen-claude-settings.json" "${SETTINGS_FILE}" "${STATUSLINE_SCRIPT}" << 'PY'
import json, sys
src, dst, statusline = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src) as f:
    data = json.load(f)
data.setdefault("statusLine", {})["type"] = "command"
data["statusLine"]["command"] = f"bash {statusline}"
data.setdefault("env", {})["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
# Bump context_window to match large upstream models (e.g. DeepSeek V4)
with open(dst, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
print(f"  Created {dst} (with statusLine)")
PY
else
    cat > "${SETTINGS_FILE}" << JSONEOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:${PROXY_PORT}",
    "ANTHROPIC_MODEL": "claude-code-proxy",
    "ANTHROPIC_API_KEY": "claude-zen-local-key",
    "ENABLE_TOOL_SEARCH": "true",
    "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1"
  },
  "model": "claude-code-proxy",
  "modelSettings": {
    "thinking": { "enabled": true, "budgetTokens": 8192 },
    "max_tokens": 32000,
    "context_window": 1000000,
    "temperature": 0.2,
    "top_p": 0.95
  },
  "statusLine": {
    "type": "command",
    "command": "bash ${STATUSLINE_SCRIPT}"
  }
}
JSONEOF
    printf '  Created %s (fallback, with statusLine)\n' "${SETTINGS_FILE}"
fi

# ─── 7. Claude Code persistence (survives devcontainer rebuild) ─────────────
printf '\n%s\n' "=== Step 7: Claude Code persistence ==="
CLAUDE_PERSIST_DIR="${SCRIPT_DIR}/.claude_persist"
CLAUDE_MEMORY_DIR="${SCRIPT_DIR}/.ai_memory"
mkdir -p "${CLAUDE_PERSIST_DIR}"
mkdir -p "${CLAUDE_MEMORY_DIR}"

# Migrate ~/.claude -> workspace
if [ -L "${HOME}/.claude" ]; then
    CURRENT_TARGET="$(readlink "${HOME}/.claude")"
    if [ "${CURRENT_TARGET}" = "${CLAUDE_PERSIST_DIR}" ]; then
        printf '  ~/.claude already symlinked to workspace\n'
    else
        printf '  Re-linking ~/.claude from %s to %s\n' "${CURRENT_TARGET}" "${CLAUDE_PERSIST_DIR}"
        if [ -d "${CURRENT_TARGET}" ] && [ -z "$(ls -A "${CLAUDE_PERSIST_DIR}" 2>/dev/null)" ]; then
            cp -a "${CURRENT_TARGET}/." "${CLAUDE_PERSIST_DIR}/"
        fi
        rm -f "${HOME}/.claude"
        ln -sfn "${CLAUDE_PERSIST_DIR}" "${HOME}/.claude"
    fi
elif [ -d "${HOME}/.claude" ]; then
    if [ -z "$(ls -A "${HOME}/.claude" 2>/dev/null)" ]; then
        rm -rf "${HOME}/.claude"
    else
        printf '  Migrating ~/.claude to workspace...\n'
        cp -a "${HOME}/.claude/." "${CLAUDE_PERSIST_DIR}/" && rm -rf "${HOME}/.claude"
    fi
    ln -sfn "${CLAUDE_PERSIST_DIR}" "${HOME}/.claude"
else
    ln -sfn "${CLAUDE_PERSIST_DIR}" "${HOME}/.claude"
    printf '  Created: ~/.claude -> %s (populated on first launch)\n' "${CLAUDE_PERSIST_DIR}"
fi

# Symlink per-project memory
WORKSPACE_ROOT="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || realpath "${SCRIPT_DIR}")"
WORKSPACE_SLUG="$(echo "${WORKSPACE_ROOT}" | tr '/' '-')"
CLAUDE_MEMORY_LINK="${HOME}/.claude/projects/${WORKSPACE_SLUG}/memory"
mkdir -p "$(dirname "${CLAUDE_MEMORY_LINK}")"
if [ -e "${CLAUDE_MEMORY_LINK}" ] && [ ! -L "${CLAUDE_MEMORY_LINK}" ]; then
    printf '  WARNING: %s exists and is not a symlink. Skipping.\n' "${CLAUDE_MEMORY_LINK}"
elif [ -L "${CLAUDE_MEMORY_LINK}" ]; then
    ln -sfn "${CLAUDE_MEMORY_DIR}" "${CLAUDE_MEMORY_LINK}"
else
    ln -s "${CLAUDE_MEMORY_DIR}" "${CLAUDE_MEMORY_LINK}"
    printf '  Created memory symlink\n'
fi

# ─── 8. Smoke test ──────────────────────────────────────────────────────────
printf '\n%s\n' "=== Step 8: Verification ==="
if [ -f "${REPO_DIR}/src/server.js" ]; then
    printf '  Proxy source: OK\n'
else
    printf '  Warning: proxy source missing at %s/src/server.js\n' "${REPO_DIR}"
fi

if grep -E '"dependencies"[[:space:]]*:' "${REPO_DIR}/package.json" >/dev/null 2>&1 \
   && ! { [ -f "${REPO_DIR}/node_modules/.package-lock.json" ] || [ -d "${REPO_DIR}/node_modules" ]; } ; then
    printf '  Warning: npm dependencies may not be installed (cd %s && npm install)\n' "${REPO_DIR}"
else
    printf '  npm dependencies: installed\n'
fi

# Quick proxy start/stop test
printf '  Starting proxy for smoke test...\n'
(
    set -a
    source "${ENV_FILE}" 2>/dev/null || true
    set +a
    cd "${REPO_DIR}"
    node src/server.js >> "${LOG_FILE}" 2>&1 &
    _smoke_pid=$!
    sleep 3
    if kill -0 "$_smoke_pid" 2>/dev/null; then
        _health="$(curl -s -H "x-api-key: ${PROXY_API_KEY:-claude-zen-local-key}" "http://127.0.0.1:${PROXY_PORT}/health" 2>/dev/null || true)"
        if echo "$_health" | grep -q "healthy\|ok"; then
            printf '  Smoke test: PASSED (%s)\n' "$_health"
        else
            printf '  Smoke test: proxy started, health: %s\n' "$_health"
        fi
    else
        printf '  Smoke test: proxy failed to start. Check %s\n' "${LOG_FILE}"
    fi
    kill "$_smoke_pid" 2>/dev/null || true
    wait "$_smoke_pid" 2>/dev/null || true
)

# ─── 9. Summary ──────────────────────────────────────────────────────────────
SHELL_RC=".bashrc"; case "${SHELL:-}" in *zsh) SHELL_RC=".zshrc" ;; esac

cat << SUMMARY

  Setup complete!

  Config:       ${ENV_FILE}
  Proxy repo:   ${REPO_DIR}
  Proxy port:   ${PROXY_PORT}
  Settings:     ${SETTINGS_FILE} (wired to statusline)
  Statusline:   ${STATUSLINE_SCRIPT}
  Claude home:  ${CLAUDE_PERSIST_DIR} (symlinked to ~/.claude)
  Memory:       ${CLAUDE_MEMORY_DIR}

  Activate:     source ~/${SHELL_RC}

  Quick start:
    cz                  Pick a model -> launch Claude Code through proxy
    cz-danger           Same, with auto-accept permissions
    cz-model            Change the model

  Other commands:
    ccz                 Resume most recent session
    cz-cloud            Use Anthropic cloud directly (no proxy)
    cz-test-free-models Test which free models respond vs are rate-limited
    cz-proxy-start      Start proxy daemon (auto-started on cz launch)
    cz-proxy-stop       Stop proxy daemon
    cz-proxy-status     Check proxy status
    cz-model-current    Show current model
    cz-undo-danger      Remove danger guardrails from CLAUDE.md
    cz-help             Show all commands

  Model:         $(sed -n 's/^UPSTREAM_MODEL=//p' "${ENV_FILE}" 2>/dev/null | head -1)
  API key:       $(if grep -q '^UPSTREAM_API_KEY=.' "${ENV_FILE}" 2>/dev/null; then echo "configured"; else echo "not set (free models only)"; fi)

  To change models:
    cz-model             Interactive picker
    Edit: ${ENV_FILE}

  Free models require no API key. Set UPSTREAM_API_KEY in .env.zen for
  paid models (Claude, GPT, Gemini, etc.). Get a key at https://opencode.ai

  Statusline (model / context % / git / cost) is wired automatically into
  every cz / cz-danger session via ${SETTINGS_FILE}.
  Customize it:  ${STATUSLINE_SCRIPT}
  For portability, keep a copy of statusline.sh next to this setup script.

SUMMARY
