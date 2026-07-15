#!/usr/bin/env bash

set -euo pipefail

if [ "${EUID}" -eq 0 ]; then
    if [ -n "${SUDO_USER:-}" ]; then
        target_home="$(getent passwd "${SUDO_USER}" | cut -d: -f6)"
        exec sudo -u "${SUDO_USER}" env HOME="${target_home}" PATH="${PATH}" bash "$0" "$@"
    fi

    printf '%s\n' "Run this script as your normal devcontainer user, not as root." >&2
    exit 1
fi

log() {
    printf '%s\n' "$1"
}

have_command() {
    command -v "$1" >/dev/null 2>&1
}

PERSISTENCE_DIR="$(pwd)/.claude_config"
CONFIG_PATH="${HOME}/.config/claude-cli"
LLM_HOST_FILE="${PERSISTENCE_DIR}/ollama-host"

# Determine LLM_HOST based on arguments or interactive menu
if [ -n "${1:-}" ]; then
    if [ "${1}" == "localhost" ]; then
        LLM_HOST="http://localhost:11434"
        log "ℹ️ Using specified localhost host: ${LLM_HOST}"
    else
        LLM_HOST="http://${1}:11434"
        log "ℹ️ Using custom host/IP: ${LLM_HOST}"
    fi
else
    # Check for existing host setting
    EXISTING_HOST=""
    if [ -f "${LLM_HOST_FILE}" ]; then
        EXISTING_HOST="$(cat "${LLM_HOST_FILE}")"
    fi

    log "No LLM host argument provided. Select an option:"
    printf '\n'
    printf '  1) Devcontainer (default - host.docker.internal)\n'
    printf '  2) Localhost\n'
    printf '  3) Enter custom IP/hostname\n'
    if [ -n "${EXISTING_HOST}" ]; then
        printf '  4) Use existing host: %s\n' "${EXISTING_HOST}"
    fi
    printf '\n'
    printf 'Enter choice [1-4]: '
    read -r choice
    case "${choice}" in
        1)
            LLM_HOST="http://host.docker.internal:11434"
            log "ℹ️ Selected devcontainer host: ${LLM_HOST}"
            ;;
        2)
            LLM_HOST="http://localhost:11434"
            log "ℹ️ Selected localhost host: ${LLM_HOST}"
            ;;
        3)
            printf 'Enter IP or hostname: '
            read -r custom_host
            LLM_HOST="http://${custom_host}:11434"
            log "ℹ️ Using custom host: ${LLM_HOST}"
            ;;
        4)
            if [ -n "${EXISTING_HOST}" ]; then
                LLM_HOST="${EXISTING_HOST}"
                log "ℹ️ Using existing host: ${LLM_HOST}"
            else
                printf '\n%s\n' "No existing host found, defaulting to devcontainer host."
                LLM_HOST="http://host.docker.internal:11434"
                log "ℹ️ Using default devcontainer host: ${LLM_HOST}"
            fi
            ;;
        *)
            printf '\n%s\n' "Invalid choice, defaulting to devcontainer host."
            LLM_HOST="http://host.docker.internal:11434"
            log "ℹ️ Using default devcontainer host: ${LLM_HOST}"
            ;;
    esac
fi
# Persist the selected host
mkdir -p "${PERSISTENCE_DIR}"
printf '%s\n' "${LLM_HOST}" > "${LLM_HOST_FILE}"

MODEL_FILE="${PERSISTENCE_DIR}/selected-ollama-model"
NPM_GLOBAL_DIR="${HOME}/.npm-global"
MAX_OUTPUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-128000}"
NUM_CTX="${CLAUDE_OLLAMA_NUM_CTX_SETUP:-262144}"
MAX_THINKING_TOKENS_DEFAULT="${CLAUDE_OLLAMA_MAX_THINKING_TOKENS_SETUP:-16384}"
EFFORT_DEFAULT="${CLAUDE_OLLAMA_EFFORT_SETUP:-low}"
DANGEROUSLY_SKIP_PERMISSIONS="${CLAUDE_OLLAMA_DANGEROUSLY_SKIP_PERMISSIONS:-false}"
MARKER_BEGIN="# >>> claude-ollama-devcontainer >>>"
MARKER_END="# <<< claude-ollama-devcontainer <<<"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_PERSIST_DIR="${CLAUDE_OLLAMA_PERSIST_DIR:-${SCRIPT_DIR}/.claude_persist}"

ensure_ollama_removed() {
    # The 'ollama' binary is NOT needed inside the devcontainer — all API calls to
    # the host Ollama server go through curl/urllib over HTTP (OLLAMA_HOST).
    # Installing via ollama.com/install.sh also starts a server that binds port
    # 11434 inside the container, which conflicts with the host's Ollama instance.
    #
    # This step prompts the user to optionally remove any in-container ollama
    # installation found on $PATH.

    if ! have_command ollama; then
        log "✅ Ollama not found inside devcontainer (no installation to remove)"
        return 0
    fi

    printf '\n⚠️  Ollama is installed inside this devcontainer (binary at %s)\n' "$(command -v ollama)"
    printf '   This can conflict with the host machine'\''s Ollama server on port 11434.\n'
    printf '   Do you want to remove the in-container Ollama installation? [y/N]: '
    read -r remove_ollama
    case "${remove_ollama}" in
        y|Y|yes|YES)
            log "🛠️  Removing in-container Ollama..."

            # Stop ollama server if running
            if have_command systemctl; then
                sudo systemctl stop ollama 2>/dev/null || true
                sudo systemctl disable ollama 2>/dev/null || true
            fi
            sudo pkill -x ollama 2>/dev/null || true

            # Remove systemd service files
            sudo rm -f /etc/systemd/system/ollama.service
            sudo rm -rf /etc/systemd/system/ollama.service.d 2>/dev/null || true

            # Remove the binary
            local binary
            binary="$(command -v ollama)"
            sudo rm -f "${binary}"
            log "✅ Removed ollama binary (${binary}) — port 11434 is now free"
            ;;
        *)
            log "⏭️  Skipping ollama removal (existing binary kept)"
            ;;
    esac
}

ensure_node_cli() {
    if have_command npm && have_command node; then
        log "✅ Node.js found at $(command -v node) ($(node --version))"
        return 0
    fi

    if ! have_command curl; then
        log "❌ curl is required to install Node.js/npm."
        exit 1
    fi

    log "🛠️  Installing Node.js/npm via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
    log "✅ Node.js found at $(command -v node) ($(node --version))"
}

ensure_claude_cli() {
    if have_command claude; then
        log "✅ Claude found at $(command -v claude)"
        return 0
    fi

    ensure_node_cli

    log "🛠️  Installing Claude CLI..."
    mkdir -p "${NPM_GLOBAL_DIR}"
    npm config set prefix "${NPM_GLOBAL_DIR}"
    PATH="${NPM_GLOBAL_DIR}/bin:${PATH}"
    npm install -g @anthropic-ai/claude-code
    log "✅ Claude found at $(command -v claude)"
}

ensure_persistence_link() {
    log "💾 Configuring persistence..."

    mkdir -p "${PERSISTENCE_DIR}"

    mkdir -p "$(dirname "${CONFIG_PATH}")"
    if [ -L "${CONFIG_PATH}" ] || [ -f "${CONFIG_PATH}" ]; then
        rm -f "${CONFIG_PATH}"
    elif [ -d "${CONFIG_PATH}" ]; then
        rm -rf "${CONFIG_PATH}"
    fi
    ln -s "${PERSISTENCE_DIR}" "${CONFIG_PATH}"
    log "✅ Config linked: ${CONFIG_PATH} -> ${PERSISTENCE_DIR}"

    local claude_persist="${CLAUDE_PERSIST_DIR:-${SCRIPT_DIR}/.claude_persist}"
    mkdir -p "${claude_persist}"
    if [ -L "${HOME}/.claude" ]; then
        local current_target
        current_target="$(readlink "${HOME}/.claude")"
        if [ "${current_target}" != "${claude_persist}" ]; then
            if [ -d "${current_target}" ] && [ -z "$(ls -A "${claude_persist}" 2>/dev/null)" ]; then
                log "📦 Migrating Claude data from ${current_target} to ${claude_persist}..."
                cp -a "${current_target}/." "${claude_persist}/"
            fi
            rm -f "${HOME}/.claude"
            ln -s "${claude_persist}" "${HOME}/.claude"
        fi
    elif [ -d "${HOME}/.claude" ]; then
        if [ -z "$(ls -A "${HOME}/.claude" 2>/dev/null)" ]; then
            rm -rf "${HOME}/.claude"
        else
            cp -a "${HOME}/.claude/." "${claude_persist}/"
            rm -rf "${HOME}/.claude"
        fi
        ln -sfn "${claude_persist}" "${HOME}/.claude"
    else
        ln -sfn "${claude_persist}" "${HOME}/.claude"
    fi
    log "✅ ~/.claude linked -> ${claude_persist}"

    local persisted_claude_json="${PERSISTENCE_DIR}/dot-claude.json"
    if [ -L "${HOME}/.claude.json" ]; then
        local current_target
        current_target="$(readlink "${HOME}/.claude.json")"
        if [ "${current_target}" != "${persisted_claude_json}" ]; then
            rm -f "${HOME}/.claude.json"
            ln -s "${persisted_claude_json}" "${HOME}/.claude.json"
        fi
    elif [ -f "${HOME}/.claude.json" ]; then
        mv "${HOME}/.claude.json" "${persisted_claude_json}"
        ln -s "${persisted_claude_json}" "${HOME}/.claude.json"
    else
        printf '%s\n' '{}' > "${persisted_claude_json}"
        ln -sfn "${persisted_claude_json}" "${HOME}/.claude.json"
    fi
    log "✅ ~/.claude.json linked -> ${persisted_claude_json}"
}

check_ollama_host() {
    if ! have_command curl; then
        log "⚠️ curl is missing, so host Ollama connectivity was not checked."
        return 0
    fi

    log "🔎 Checking host Ollama API..."
    if curl -fsS "${LLM_HOST}/api/tags" >"${PERSISTENCE_DIR}/ollama-tags.json"; then
        log "✅ Host Ollama API reachable at ${LLM_HOST}"
    else
        log "⚠️ Host Ollama API not reachable at ${LLM_HOST}"
    fi
}

build_wrapper_block() {
    cat <<'EOF' | sed \
        -e "s|__MARKER_BEGIN__|${MARKER_BEGIN}|g" \
        -e "s|__MARKER_END__|${MARKER_END}|g" \
        -e "s|__LLM_HOST__|${LLM_HOST}|g" \
        -e "s|__NPM_GLOBAL_DIR__|${NPM_GLOBAL_DIR}|g" \
        -e "s|__MAX_OUTPUT_TOKENS__|${MAX_OUTPUT_TOKENS}|g" \
        -e "s|__NUM_CTX__|${NUM_CTX}|g" \
        -e "s|__MAX_THINKING_TOKENS__|${MAX_THINKING_TOKENS_DEFAULT}|g" \
        -e "s|__EFFORT_DEFAULT__|${EFFORT_DEFAULT}|g" \
        -e "s|__DANGEROUSLY_SKIP__|${DANGEROUSLY_SKIP_PERMISSIONS}|g" \
        -e "s|__MODEL_FILE__|${MODEL_FILE}|g" \
        -e "s|__SCRIPT_DIR__|${SCRIPT_DIR}|g" \
        -e "s|__PERSISTENCE_DIR__|${PERSISTENCE_DIR}|g" \
        -e "s|__CLAUDE_OLLAMA_PERSISTENCE_DIR__|${CLAUDE_PERSIST_DIR}|g"
__MARKER_BEGIN__
# Environment configuration
SCRIPT_DIR="__SCRIPT_DIR__"
PERSISTENCE_DIR="__PERSISTENCE_DIR__"
CLAUDE_OLLAMA_PERSISTENCE_DIR="__CLAUDE_OLLAMA_PERSISTENCE_DIR__"
export ANTHROPIC_BASE_URL="__LLM_HOST__"
export CLAUDE_OLLAMA_MODEL_FILE="__MODEL_FILE__"
export CLAUDE_OLLAMA_NUM_CTX="__NUM_CTX__"
export CLAUDE_OLLAMA_THINKING="enabled"
export CLAUDE_OLLAMA_MAX_THINKING_TOKENS="__MAX_THINKING_TOKENS__"
export CLAUDE_OLLAMA_EFFORT="__EFFORT_DEFAULT__"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-__MAX_OUTPUT_TOKENS__}"
export CLAUDE_OLLAMA_TIMEOUT_MS="${CLAUDE_OLLAMA_TIMEOUT_MS:-3600000}"
export API_TIMEOUT_MS="${CLAUDE_OLLAMA_TIMEOUT_MS:-3600000}"
export ANTHROPIC_TIMEOUT="${CLAUDE_OLLAMA_TIMEOUT_MS:-3600000}"
export CLAUDE_OLLAMA_CTX_STRATEGY="${CLAUDE_OLLAMA_CTX_STRATEGY:-}"
export PATH="__NPM_GLOBAL_DIR__/bin:${PATH}"

unalias c c-new c-danger c-cloud c-continue 2>/dev/null || true
unset -f c c_new cc c-danger c-continue claude_launch claude_local_launch claude_cloud_launch claude_pick_ollama_model claude_current_ollama_model _claude_pick_ollama_model_impl _claude_ensure_ctx_variant _claude_has_effort_flag \
      _claude_ollama_install_danger_guardrails claude_ollama_launch_danger claude_ollama_uninstall_danger_rules 2>/dev/null || true

_claude_pick_ollama_model_impl() {
    python3 - <<'PY'
import json
import os
import re
import sys
from urllib.request import urlopen

host = os.environ.get("ANTHROPIC_BASE_URL", "http://host.docker.internal:11434")
try:
    with urlopen(f"{host}/api/tags", timeout=5) as response:
        payload = json.load(response)
except Exception as exc:
    print(f"Unable to reach Ollama host at {host}: {exc}", file=sys.stderr)
    sys.exit(1)

models = payload.get("models", [])
if not models:
    print("No Ollama models returned by host API.", file=sys.stderr)
    sys.exit(1)

ctx_suffix = re.compile(r"-ctx\d+$")
names = {m.get("name", "") for m in models}
visible = [
    m
    for m in models
    if not (ctx_suffix.search(m.get("name", "")) and ctx_suffix.sub("", m.get("name", "")) in names)
]
if not visible:
    visible = models

for index, model in enumerate(visible, start=1):
    print(f"{index}) {model.get('name', 'unknown')}", file=sys.stderr)

print("Select model number:", file=sys.stderr)
with open("/dev/tty", "r", encoding="utf-8") as tty:
    choice = tty.readline().strip()
if not choice.isdigit():
    print("Selection must be a number.", file=sys.stderr)
    sys.exit(1)

position = int(choice) - 1
if position < 0 or position >= len(visible):
    print("Selection out of range.", file=sys.stderr)
    sys.exit(1)

print(visible[position].get("name", "unknown"))
PY
}

claude_pick_ollama_model() {
    local selected_model
    if ! selected_model="$(_claude_pick_ollama_model_impl)"; then
        return 1
    fi

    mkdir -p "$(dirname "${CLAUDE_OLLAMA_MODEL_FILE}")"
    printf '%s\n' "${selected_model}" > "${CLAUDE_OLLAMA_MODEL_FILE}"
    printf '%s\n' "Selected Ollama model: ${selected_model}"
}

claude_current_ollama_model() {
    if [ -f "${CLAUDE_OLLAMA_MODEL_FILE}" ]; then
        cat "${CLAUDE_OLLAMA_MODEL_FILE}"
    else
        printf '%s\n' "No Ollama model selected yet."
    fi
}

_claude_ensure_ctx_variant() {
    local base="$1"
    local strategy="${2:-${CLAUDE_OLLAMA_CTX_STRATEGY:-standard}}"
    local host="${ANTHROPIC_BASE_URL:-http://host.docker.internal:11434}"

    # "default" → no ctx override, return base model name as-is
    if [ "${strategy}" = "default" ]; then
        printf '%s' "${base}"
        return 0
    fi

    local num_ctx=""
    case "${strategy}" in
        standard)
            num_ctx="${CLAUDE_OLLAMA_NUM_CTX:-__NUM_CTX__}"
            ;;
        custom=*)
            num_ctx="${strategy#custom=}"
            ;;
        *)
            if printf '%s' "${strategy}" | grep -qE '^[0-9]+$'; then
                num_ctx="${strategy}"
            else
                num_ctx="${CLAUDE_OLLAMA_NUM_CTX:-__NUM_CTX__}"
            fi
            ;;
    esac

    if [ -z "${num_ctx}" ] || [ "${num_ctx}" = "0" ]; then
        printf '%s' "${base}"
        return 0
    fi

    local variant="${base}-ctx${num_ctx}"
    if ! curl -fsS "${host}/api/tags" 2>/dev/null | grep -q "\"${variant}\""; then
        printf 'Creating Ollama variant %s (num_ctx=%s)...\n' "${variant}" "${num_ctx}" >&2
        if ! curl -fsS "${host}/api/create" \
            -d "{\"model\":\"${variant}\",\"from\":\"${base}\",\"parameters\":{\"num_ctx\":${num_ctx}},\"stream\":false}" \
            >/dev/null 2>&1; then
            printf 'Warning: could not create %s; launching base model %s instead.\n' "${variant}" "${base}" >&2
            printf '%s' "${base}"
            return 0
        fi
    fi
    printf '%s' "${variant}"
}

_claude_has_effort_flag() {
    local arg
    for arg in "$@"; do
        case "${arg}" in
            --effort|--effort=*)
                return 0
                ;;
        esac
    done
    return 1
}

_claude_get_model_max_ctx() {
    local model="$1"
    local host="${ANTHROPIC_BASE_URL:-http://host.docker.internal:11434}"
    curl -fsS "${host}/api/show" -d "{\"model\":\"${model}\"}" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    mi = data.get("model_info", {})
    for key in mi:
        kl = key.lower()
        if "context_length" in kl:
            print(int(mi[key]))
            sys.exit(0)
except Exception:
    pass
print("0")
' 2>/dev/null || printf '0'
}

_claude_find_binary() {
    local cmd
    cmd="$(command -v claude 2>/dev/null)" && { echo "$cmd"; return 0; }
    for p in \
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
    cmd="$(find /home/vscode /root /usr/local -maxdepth 8 -name claude -type f -executable 2>/dev/null | head -1)"
    [ -n "$cmd" ] && { echo "$cmd"; return 0; }
    printf '\nError: claude binary not found. Install it with:\n  npm install -g @anthropic-ai/claude-code\n\n' >&2
    return 1
}

_claude_ctx_strategy_select() {
    local model="$1"

    if [ -n "${CLAUDE_OLLAMA_CTX_STRATEGY:-}" ]; then
        printf "  Using env ctx strategy: %s\n" "${CLAUDE_OLLAMA_CTX_STRATEGY}" >&2
        printf '%s' "${CLAUDE_OLLAMA_CTX_STRATEGY}"
        return 0
    fi

    printf '\n' >&2
    printf "  Context strategy for %s:\n" "${model}" >&2
    printf "  1) Model default (no ctx override)\n" >&2
    printf "  2) Standard override (__NUM_CTX__ tokens, recommended)\n" >&2
    printf "  3) Maximum (auto-detect from model)\n" >&2
    printf "  4) Custom value (type integer)\n" >&2
    printf '\n' >&2
    printf "Enter choice [1-4] (default: 2): " >&2
    read -r choice </dev/tty
    choice="${choice:-2}"

    case "${choice}" in
        1) strategy="default" ;;
        2) strategy="standard" ;;
        3)
            local max_ctx
            max_ctx="$(_claude_get_model_max_ctx "${model}")"
            if [ -n "${max_ctx}" ] && printf '%s' "${max_ctx}" | grep -qE '^[0-9]+$' && [ "${max_ctx}" != "0" ]; then
                strategy="custom=${max_ctx}"
                printf "  ✓ Auto-detected max context: %s tokens\n" "${max_ctx}" >&2
            else
                printf "  ⚠️ Could not auto-detect; using standard (__NUM_CTX__ tokens).\n" >&2
                strategy="standard"
            fi
            ;;
        4)
            printf "  📝 Enter custom context size in tokens (e.g., 488576, 262144): " >&2
            read -r custom_ctx </dev/tty
            if printf '%s' "${custom_ctx}" | grep -qE '^[0-9]+$' && [ -n "${custom_ctx}" ]; then
                strategy="custom=${custom_ctx}"
                printf "  ✓ Using custom context size: %s tokens\n" "${custom_ctx}" >&2
            else
                printf "  ⚠️ Invalid value; using standard (__NUM_CTX__ tokens).\n" >&2
                strategy="standard"
            fi
            ;;
        *) strategy="standard" ;;
    esac

    printf "  Using context strategy: %s\n" "${strategy}" >&2
    printf '%s' "${strategy}"
}

_claude_ollama_install_danger_guardrails() {
    local workspace_root="$1"
    local persist_dir="$2"
    local claude_md="${workspace_root}/CLAUDE.md"
    local backup_file="${persist_dir}/CLAUDE.md.bak"

    local start_marker="# --- DANGER GUARDRAILS START ---"
    local end_marker="# --- DANGER GUARDRAHILS END ---"
    local guardrails="
# --- DANGER GUARDRAILS START ---
- Prohibit all write operations with 'az' azure CLI (e.g., az resource create, az vm start, az group delete). Read operations are permitted.
- Prohibit all write operations with 'gh' (GitHub CLI) except for 'gh edit' when updating a PR description. All other mutations (create, delete, merge, etc.) are prohibited.
# --- DANGER GUARDRAHILS END ---"

    # If CLAUDE.md doesn't exist, create it
    if [ ! -f "$claude_md" ]; then
        printf '%s\n' "# Project Instructions" > "$claude_md"
    fi

    # Backup existing file
    cp "$claude_md" "$backup_file"

    # Idempotent update: replace existing block or append to end
    if grep -q "$start_marker" "$claude_md"; then
        # Replace existing block
        # Use a temporary file to avoid issues with sed -i on some systems
        sed "/$start_marker/,/$end_marker/d" "$claude_md" > "${claude_md}.tmp"
        # We need to re-insert the guardrails at the same spot or just append
        # For simplicity, we'll remove the old ones and append the new ones
        # But the user wants to preserve other rules, so we just append
        cat "${claude_md}.tmp" > "$claude_md"
        printf '\n%s\n' "$guardrails" >> "$claude_md"
        rm -f "${claude_md}.tmp"
    else
        printf '\n%s\n' "$guardrails" >> "$claude_md"
    fi

    log "✅ Danger guardrails installed in CLAUDE.md"
    echo "$backup_file"
}

_claude_ollama_uninstall_danger_guardrails() {
    local workspace_root="$1"
    local persist_dir="$2"
    local claude_md="${workspace_root}/CLAUDE.md"

    if [ -f "$claude_md" ]; then
        local start_marker="# --- DANGER GUARDRAILS START ---"
        local end_marker="# --- DANGER GUARDRAHILS END ---"
        # Remove everything between markers including markers
        sed -i "/$start_marker/,/$end_marker/d" "$claude_md"
        log "✅ Danger guardrails removed from CLAUDE.md"
    fi
}


claude_ollama_install_danger_rules() {
    local workspace_root
    workspace_root=$(pwd)
    local persist_dir="${CLAUDE_OLLAMA_PERSISTENCE_DIR:-${SCRIPT_DIR}/.claude_persist}"
    _claude_ollama_install_danger_guardrails "$workspace_root" "$persist_dir" >/dev/null
    printf '  \u2705 Danger guardrails installed in CLAUDE.md\n'
}

claude_ollama_uninstall_danger_rules() {
    # This is called via the c-undo-danger alias
    local workspace_root
    workspace_root=$(pwd)
    local persist_dir="${CLAUDE_OLLAMA_PERSISTENCE_DIR:-${SCRIPT_DIR}/.claude_persist}"
    _claude_ollama_uninstall_danger_guardrails "$workspace_root" "$persist_dir"
}

claude_local_launch() {
    local selected_model
    if ! selected_model="$(_claude_pick_ollama_model_impl)"; then
        return 1
    fi

    printf '%s\n' "${selected_model}" > "${CLAUDE_OLLAMA_MODEL_FILE}"

    local ctx_strategy
    ctx_strategy="$(_claude_ctx_strategy_select "${selected_model}")"
    local launch_model
    launch_model="$(_claude_ensure_ctx_variant "${selected_model}" "${ctx_strategy}")"

    local -a effort_args=()
    if ! _claude_has_effort_flag "$@"; then
        case "${CLAUDE_OLLAMA_EFFORT:-low}" in
            low|medium|high)
                effort_args=(--effort "${CLAUDE_OLLAMA_EFFORT}")
                ;;
        esac
    fi

    local -a extra_args=()
    if [ "${CLAUDE_OLLAMA_DANGEROUSLY_SKIP_PERMISSIONS:-__DANGEROUSLY_SKIP__}" = "true" ] ||
       printf '%s' "$@" | grep -qw -- '--dangerously-skip-permissions'; then
        extra_args+=(--dangerously-skip-permissions)
    fi
    if [ -n "${CLAUDE_OLLAMA_DEBUG:-}" ]; then
        extra_args+=(--debug)
    fi

    local timeout_ms="${CLAUDE_OLLAMA_TIMEOUT_MS:-3600000}"
    printf 'Launching Claude on %s (request timeout: %sms)...\n' "${launch_model}" "${timeout_ms}" >&2

    local _claude_bin
    _claude_bin="$(_claude_find_binary)" || return $?

    ANTHROPIC_API_KEY="" \
    ANTHROPIC_AUTH_TOKEN="ollama" \
    API_TIMEOUT_MS="${timeout_ms}" \
    ANTHROPIC_TIMEOUT="${timeout_ms}" \
    MAX_THINKING_TOKENS="${CLAUDE_OLLAMA_MAX_THINKING_TOKENS:-16384}" \
    "${_claude_bin}" --model "${launch_model}" "${extra_args[@]}" "${effort_args[@]}" "$@"
    local _claude_exit=$?
    if [ "${_claude_exit}" -ne 0 ]; then
        printf '\n⚠️  Claude exited with code %s. ' "${_claude_exit}" >&2
        printf 'Try CLAUDE_OLLAMA_DEBUG=1 c to see detailed errors, ' >&2
        printf 'or check the Ollama server logs at the host.\n' >&2
        return "${_claude_exit}"
    fi
}

claude_ollama_launch_danger() {
    local selected_model
    if ! selected_model="$(_claude_pick_ollama_model_impl)"; then
        return 1
    fi

    printf '%s\n' "${selected_model}" > "${CLAUDE_OLLAMA_MODEL_FILE}"

    local ctx_strategy
    ctx_strategy="$(_claude_ctx_strategy_select "${selected_model}")"
    local launch_model
    launch_model="$(_claude_ensure_ctx_variant "${selected_model}" "${ctx_strategy}")"

    local workspace_root="${PERSISTENCE_DIR%/*}"
    [ -z "$workspace_root" ] && workspace_root="$(pwd)"

    printf '\n'
    printf '  ⚠️  DANGER MODE\n'
    printf '  ────────────\n'
    printf '  Auto-accepting ALL permissions.\n'
    printf '  Model: %s\n' "${launch_model}"
    printf '\n'

    local backup_file
    backup_file="$(_claude_ollama_install_danger_guardrails "$workspace_root" "${CLAUDE_OLLAMA_PERSISTENCE_DIR}")"

    local -a extra_args=()
    if [ -n "${CLAUDE_OLLAMA_DEBUG:-}" ]; then
        extra_args+=(--debug)
    fi

    local timeout_ms="${CLAUDE_OLLAMA_TIMEOUT_MS:-3600000}"
    printf 'Launching Claude on %s (request timeout: %sms, danger mode)...\n' "${launch_model}" "${timeout_ms}" >&2

    local _claude_bin
    _claude_bin="$(_claude_find_binary)" || return $?

    ANTHROPIC_API_KEY="" \
    ANTHROPIC_AUTH_TOKEN="ollama" \
    API_TIMEOUT_MS="${timeout_ms}" \
    ANTHROPIC_TIMEOUT="${timeout_ms}" \
    MAX_THINKING_TOKENS="${CLAUDE_OLLAMA_MAX_THINKING_TOKENS:-16384}" \
    CLAUDE_OLLAMA_DANGEROUSLY_SKIP_PERMISSIONS=true \
    "${_claude_bin}" --model "${launch_model}" --dangerously-skip-permissions "${extra_args[@]}" "$@"
    local _claude_exit=$?


    if [ "${_claude_exit}" -ne 0 ]; then
        printf '\n⚠️  Claude exited with code %s. ' "${_claude_exit}" >&2
        printf 'Try CLAUDE_OLLAMA_DEBUG=1 c-danger to see detailed errors, ' >&2
        printf 'or check the Ollama server logs at the host.\n' >&2
        return "${_claude_exit}"
    fi
}

claude_cloud_launch() {
    claude "$@"
}

alias c='claude_local_launch'
alias c_new='claude_local_launch'
alias c-new='c_new'
alias cc='CLAUDE_OLLAMA_CTX_STRATEGY= claude --continue'
alias c-danger='claude_ollama_launch_danger'
alias c-undo-danger='claude_ollama_uninstall_danger_rules'
alias c-danger-guardrails-install='claude_ollama_install_danger_rules'
alias c-danger-guardrails-remove='claude_ollama_uninstall_danger_rules'
alias c-cloud='claude_cloud_launch'
alias c-continue='cc'
alias c-med='CLAUDE_OLLAMA_EFFORT=medium c'
alias c-hi='CLAUDE_OLLAMA_EFFORT=high c'
alias c-max='CLAUDE_OLLAMA_EFFORT=high CLAUDE_OLLAMA_NUM_CTX=262144 CLAUDE_OLLAMA_MAX_THINKING_TOKENS=32768 CLAUDE_OLLAMA_TIMEOUT_MS=3600000 c'
alias ollama-model='claude_pick_ollama_model'
alias ollama-model-current='claude_current_ollama_model'
__MARKER_END__
EOF
}

install_shell_wrappers() {
    log "⌨️  Installing shell wrappers..."
    local wrapper_block
    wrapper_block="$(build_wrapper_block)"

    local shell_rc
    for shell_rc in "${HOME}/.bashrc" "${HOME}/.zshrc"; do
        if [ ! -f "${shell_rc}" ]; then
            continue
        fi

        python3 - "${shell_rc}" "${MARKER_BEGIN}" "${MARKER_END}" "${wrapper_block}" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
start = sys.argv[2]
end = sys.argv[3]
replacement = sys.argv[4]
text = path.read_text()

start_idx = text.find(start)
end_idx = text.find(end)

if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
        end_idx += len(end)
        if end_idx < len(text) and text[end_idx:end_idx + 1] == "\n":
                end_idx += 1
        text = text[:start_idx] + replacement + "\n" + text[end_idx:]
else:
        if text and not text.endswith("\n"):
                text += "\n"
        text += replacement + "\n"

path.write_text(text)
PY
        log "✅ Updated ${shell_rc}"
    done
}

update_vscode_tasks() {
    log "📝 Updating VS Code tasks..."
    local tasks_file=".vscode/tasks.json"
    mkdir -p ".vscode"
    if [ ! -f "$tasks_file" ]; then
        printf '{"version": "2.0.0", "tasks": []}' > "$tasks_file"
    fi

    python3 - "$tasks_file" << 'PY'
import json, sys, os
tasks_file = sys.argv[1]
with open(tasks_file, 'r') as f:
    data = json.load(f)

new_tasks = [
    {"label": "Claude Local (c)", "type": "shell", "command": "bash -ic 'c'", "problemMatcher": [], "group": "build"},
    {"label": "Claude Local New (c-new)", "type": "shell", "command": "bash -ic 'c-new'", "problemMatcher": [], "group": "build"},
    {"label": "Claude Local Danger (c-danger)", "type": "shell", "command": "bash -ic 'c-danger'", "problemMatcher": [], "group": "build"},
    {"label": "Claude Cloud (c-cloud)", "type": "shell", "command": "bash -ic 'c-cloud'", "problemMatcher": [], "group": "build"},
    {"label": "Claude Continue (c-continue)", "type": "shell", "command": "bash -ic 'c-continue'", "problemMatcher": [], "group": "build"},
    {"label": "Claude Local Medium (c-med)", "type": "shell", "command": "bash -ic 'c-med'", "problemMatcher": [], "group": "build"},
    {"label": "Claude Local High (c-hi)", "type": "shell", "command": "bash -ic 'c-hi'", "problemMatcher": [], "group": "build"},
    {"label": "Claude Local Max (c-max)", "type": "shell", "command": "bash -ic 'c-max'", "problemMatcher": [], "group": "build"},
]

existing_tasks = {t["label"]: t for t in data.get("tasks", [])}
for nt in new_tasks:
    existing_tasks[nt["label"]] = nt

data["tasks"] = list(existing_tasks.values())
with open(tasks_file, 'w') as f:
    json.dump(data, f, indent=2)
PY
}

print_summary() {
    local shell_rc=".bashrc"
    case "${SHELL}" in
        *zsh) shell_rc=".zshrc" ;;
        *fish) shell_rc=".config/fish/config.fish" ;;
    esac

    log "------------------------------------------------"
    log "Setup complete"
    log "------------------------------------------------"
    log "1. Run: source ~/${shell_rc}"
    log "2. Use: c        to pick a host Ollama model and launch Claude through ollama"
    log "3. Use: c-new    same as c"
    log "4. Use: c-cloud  to launch the installed Claude CLI directly"
    log "5. Use: cc       to continue the most recent Claude cloud session"
    log "6. Config lives at: ${PERSISTENCE_DIR}"
    log "7. Use: c-danger  to pick a host Ollama model and launch Claude with auto-accept permissions + git guardrails"
    log "8. Use: c-undo-danger  remove danger guardrails from CLAUDE.md (without launching)"
    log "------------------------------------------------"
    log "Coexistence with zen setup (setup_claude_zen_devcontainer.sh):"
    log "  - Both scripts share ~/.claude -> .claude_persist/ for sessions/history."
    log "  - Config dir (.claude_config/) is shared — each script writes disjoint files into it."
    log "  - Aliases are separate: c* (ollama) vs cz* (zen)."
    log "  - Run 'c' and 'cz' in separate windows to use different models, same session history."
    log "  - Do NOT run them concurrently — shared state files can corrupt under simultaneous writes."
    log "------------------------------------------------"
    log "Effort levels (keep thinking enabled, adjust reasoning depth/breadth):"
    log "  c              (default: low effort, fast, safe for broad analysis)"
    log "  c-med          (medium effort, slower, deeper reasoning)"
    log "  c-hi           (high effort, slowest, maximum reasoning depth)"
    log "  c-max          (high effort + full context/thinking budget)"
    log "Context window (num_ctx): models launch via '<model>-ctx${NUM_CTX}' variant."
    log "Default (${NUM_CTX}) tokens; safe for 48+ GB VRAM. Lower VRAM: CLAUDE_OLLAMA_NUM_CTX_SETUP=131072 ./setup..."
    log "Thinking budget: ${MAX_THINKING_TOKENS_DEFAULT} tokens by default."
    log "Override: CLAUDE_OLLAMA_MAX_THINKING_TOKENS=32768 c (for even deeper reasoning)."
    log "Note: --effort is set at launch time, not a runtime command."
    log "Output ceiling: Claude Code hard-caps at 128000 tokens. Split work across"
    log "multiple turns for unlimited effective headroom."
    log "------------------------------------------------"
}

log "🚀 Starting Claude environment setup..."
ensure_ollama_removed
ensure_claude_cli
ensure_persistence_link
check_ollama_host
install_shell_wrappers
update_vscode_tasks
print_summary
