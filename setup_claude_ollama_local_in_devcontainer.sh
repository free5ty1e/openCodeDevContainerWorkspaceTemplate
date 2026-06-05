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
    log "No LLM host argument provided. Select an option:"
    printf '\n'
    printf '  1) Devcontainer (default - host.docker.internal)\n'
    printf '  2) Localhost\n'
    printf '  3) Enter custom IP/hostname\n'
    printf '\n'
    printf 'Enter choice [1-3]: '
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
        *)
            printf '\n%s\n' "Invalid choice, defaulting to devcontainer host."
            LLM_HOST="http://host.docker.internal:11434"
            log "ℹ️ Using default devcontainer host: ${LLM_HOST}"
            ;;
    esac
fi
MODEL_FILE="${PERSISTENCE_DIR}/selected-ollama-model"
NPM_GLOBAL_DIR="${HOME}/.npm-global"
MARKER_BEGIN="# >>> claude-ollama-devcontainer >>>"
MARKER_END="# <<< claude-ollama-devcontainer <<<"

ensure_ollama_prereqs() {
    if have_command zstd; then
        return 0
    fi

    log "📦 Installing Ollama prerequisites..."
    sudo apt-get update
    sudo apt-get install -y zstd
}

ensure_ollama_cli() {
    if have_command ollama; then
        log "✅ Ollama found at $(command -v ollama)"
        return 0
    fi

    if ! have_command curl; then
        log "❌ curl is required to install Ollama."
        exit 1
    fi

    ensure_ollama_prereqs
    log "🛠️  Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sudo sh
    log "✅ Ollama found at $(command -v ollama)"
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

    # Ensure base persistence dir exists
    mkdir -p "${PERSISTENCE_DIR}"

    # Link ~/.config/claude-cli -> PERSISTENCE_DIR
    mkdir -p "$(dirname "${CONFIG_PATH}")"
    if [ -L "${CONFIG_PATH}" ] || [ -f "${CONFIG_PATH}" ]; then
        rm -f "${CONFIG_PATH}"
    elif [ -d "${CONFIG_PATH}" ]; then
        rm -rf "${CONFIG_PATH}"
    fi
    ln -s "${PERSISTENCE_DIR}" "${CONFIG_PATH}"
    log "✅ Config linked: ${CONFIG_PATH} -> ${PERSISTENCE_DIR}"

    # Persist ~/.claude/ (sessions, history, cache, plugins, daemon state)
    local persisted_claude_dir="${PERSISTENCE_DIR}/dot-claude"
    if [ -L "${HOME}/.claude" ]; then
        local current_target
        current_target="$(readlink "${HOME}/.claude")"
        if [ "${current_target}" != "${persisted_claude_dir}" ]; then
            rm -f "${HOME}/.claude"
            ln -s "${persisted_claude_dir}" "${HOME}/.claude"
        fi
    elif [ -d "${HOME}/.claude" ]; then
        if [ -d "${persisted_claude_dir}" ]; then
            rm -rf "${HOME}/.claude"
        else
            mv "${HOME}/.claude" "${persisted_claude_dir}"
        fi
        ln -s "${persisted_claude_dir}" "${HOME}/.claude"
    else
        mkdir -p "${persisted_claude_dir}"
        ln -sfn "${persisted_claude_dir}" "${HOME}/.claude"
    fi
    log "✅ ~/.claude linked -> ${persisted_claude_dir}"

    # Persist ~/.claude.json (user ID, project settings, onboarding state)
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
        touch "${persisted_claude_json}"
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
        -e "s|__MODEL_FILE__|${MODEL_FILE}|g"
__MARKER_BEGIN__
export OLLAMA_HOST="__LLM_HOST__"
export CLAUDE_OLLAMA_MODEL_FILE="__MODEL_FILE__"
export PATH="__NPM_GLOBAL_DIR__/bin:${PATH}"

unalias c c-new c-cloud c-continue 2>/dev/null || true
unset -f c c_new cc claude_launch claude_local_launch claude_cloud_launch claude_pick_ollama_model claude_current_ollama_model _claude_pick_ollama_model_impl 2>/dev/null || true

_claude_pick_ollama_model_impl() {
    python3 - <<'PY'
import json
import os
import sys
from urllib.request import urlopen

host = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
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

for index, model in enumerate(models, start=1):
    print(f"{index}) {model.get('name', 'unknown')}", file=sys.stderr)

print("Select model number:", file=sys.stderr)
with open("/dev/tty", "r", encoding="utf-8") as tty:
    choice = tty.readline().strip()
if not choice.isdigit():
    print("Selection must be a number.", file=sys.stderr)
    sys.exit(1)

position = int(choice) - 1
if position < 0 or position >= len(models):
    print("Selection out of range.", file=sys.stderr)
    sys.exit(1)

print(models[position].get("name", "unknown"))
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

claude_local_launch() {
    if ! command -v ollama >/dev/null 2>&1; then
        printf '%s\n' "ollama CLI is not installed in this container." >&2
        return 1
    fi

    local selected_model
    if ! selected_model="$(_claude_pick_ollama_model_impl)"; then
        return 1
    fi

    printf '%s\n' "${selected_model}" > "${CLAUDE_OLLAMA_MODEL_FILE}"
    OLLAMA_HOST="${OLLAMA_HOST}" ollama launch claude --model "${selected_model}" "$@"
}

claude_cloud_launch() {
    claude "$@"
}

c() {
    claude_local_launch "$@"
}

c_new() {
    claude_local_launch "$@"
}

cc() {
    claude --continue "$@"
}

alias c-new='c_new'
alias c-cloud='claude_cloud_launch'
alias c-continue='cc'
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
    log "------------------------------------------------"
}

log "🚀 Starting Claude environment setup..."
ensure_ollama_cli
ensure_claude_cli
ensure_persistence_link
check_ollama_host
install_shell_wrappers
print_summary
  