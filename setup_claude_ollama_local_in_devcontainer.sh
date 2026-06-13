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
# NOTE: Claude Code hard-clamps output tokens to 128000 internally; no env var can
# exceed that. The "output token maximum" error is almost always a runaway model,
# not a real limit. The actual fix is NUM_CTX below (see comment), which lets the
# model fit Claude Code's large prompt so it stops naturally instead of rambling.
# This value is still exported for the direct `claude`/`c-cloud` path.
MAX_OUTPUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-128000}"
# Context window (num_ctx) baked into a derived Ollama model variant at launch.
# Ollama serves models at a small default context (~4096) regardless of the
# model's architectural maximum, which truncates Claude Code's large system
# prompt and makes models ramble until they hit the output-token ceiling. Baking
# a large num_ctx into a `<model>-ctx<N>` variant fixes this. Override by
# exporting CLAUDE_OLLAMA_NUM_CTX before running this script. Set to 0 to disable.
# With 48+ GB VRAM, 262144 (gemma4's max) is safe; tune down for lower VRAM.
# NOTE: intentionally NOT reading from CLAUDE_OLLAMA_NUM_CTX here — the setup
# script bakes the default unconditionally so re-running the script always
# upgrades the wrapper to the latest defaults. Per-launch overrides like
# 'CLAUDE_OLLAMA_NUM_CTX=131072 c' still work inside the shell.
NUM_CTX="${CLAUDE_OLLAMA_NUM_CTX_SETUP:-262144}"
# Thinking token budget and effort defaults. Same baking strategy as NUM_CTX.
MAX_THINKING_TOKENS_DEFAULT="${CLAUDE_OLLAMA_MAX_THINKING_TOKENS_SETUP:-16384}"
EFFORT_DEFAULT="${CLAUDE_OLLAMA_EFFORT_SETUP:-low}"
DANGEROUSLY_SKIP_PERMISSIONS="${CLAUDE_OLLAMA_DANGEROUSLY_SKIP_PERMISSIONS:-false}"
MARKER_BEGIN="# >>> claude-ollama-devcontainer >>>"
MARKER_END="# <<< claude-ollama-devcontainer <<<"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_PERSIST_DIR="${CLAUDE_OLLAMA_PERSIST_DIR:-${SCRIPT_DIR}/.claude_persist}"

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

    # Both scripts share one persist dir so `c` and `cz` see the same sessions/history.
    local claude_persist="${CLAUDE_PERSIST_DIR:-${SCRIPT_DIR}/.claude_persist}"
    # Always ensure the target directory exists — a dangling symlink causes
    # Claude Code to fail with ENOENT when trying to create jobs/sessions dirs.
    mkdir -p "${claude_persist}"
    if [ -L "${HOME}/.claude" ]; then
        local current_target
        current_target="$(readlink "${HOME}/.claude")"
        if [ "${current_target}" != "${claude_persist}" ]; then
            # Old symlink pointed elsewhere (e.g. .claude_config/dot-claude).
            # Migrate data if the old target had content and the new one is empty.
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
        -e "s|__MODEL_FILE__|${MODEL_FILE}|g"
__MARKER_BEGIN__
export OLLAMA_HOST="__LLM_HOST__"
export CLAUDE_OLLAMA_MODEL_FILE="__MODEL_FILE__"
# Default num_ctx baked in unconditionally so re-running the script upgrades it.
# Per-launch override still works: CLAUDE_OLLAMA_NUM_CTX=131072 c
export CLAUDE_OLLAMA_NUM_CTX="__NUM_CTX__"
# Local model thinking defaults. Keep thinking enabled by default, but bound the
# thinking budget to prevent runaway long traces that can trigger output cap
# errors in Claude Code for broad prompts like "analyze this repo".
# With 48+ GB VRAM, 16384 thinking tokens is safe; tune down for lower VRAM.
# Baked unconditionally so re-running the script always upgrades the defaults.
# Per-launch: CLAUDE_OLLAMA_MAX_THINKING_TOKENS=32768 c
export CLAUDE_OLLAMA_THINKING="enabled"
export CLAUDE_OLLAMA_MAX_THINKING_TOKENS="__MAX_THINKING_TOKENS__"
# Default Claude effort level for local Ollama launches. Lower effort keeps
# thinking enabled but curbs runaway long responses on broad prompts.
# Baked unconditionally; override per-launch: CLAUDE_OLLAMA_EFFORT=medium c
export CLAUDE_OLLAMA_EFFORT="__EFFORT_DEFAULT__"
# Claude Code clamps this to 128000 internally; it only matters for the direct
# `claude`/`c-cloud` path since `ollama launch claude` sets its own value.
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-__MAX_OUTPUT_TOKENS__}"
export PATH="__NPM_GLOBAL_DIR__/bin:${PATH}"

unalias c c-new c-danger c-cloud c-continue 2>/dev/null || true
unset -f c c_new cc c-danger c-continue claude_launch claude_local_launch claude_cloud_launch claude_pick_ollama_model claude_current_ollama_model _claude_pick_ollama_model_impl _claude_ensure_ctx_variant _claude_has_effort_flag \
      _claude_ollama_install_danger_guardrails _claude_ollama_cleanup_danger_guardrails claude_ollama_launch_danger claude_ollama_uninstall_danger_rules 2>/dev/null || true

_claude_pick_ollama_model_impl() {
    python3 - <<'PY'
import json
import os
import re
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

# Hide auto-generated "<base>-ctx<N>" variants from the chooser when their base
# model is also present. The launch logic recreates/uses the ctx variant
# automatically, so showing both just clutters the list. A "-ctx<N>" model whose
# base is missing is kept so it stays selectable.
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
    # Ensure a large-context variant of the given model exists on the host Ollama,
    # then echo the model name to actually launch. This is the real fix for the
    # "output token maximum" error: a roomy num_ctx lets the model fit Claude
    # Code's prompt and stop naturally instead of rambling until it is truncated.
    local base="$1"
    local num_ctx="${CLAUDE_OLLAMA_NUM_CTX:-__NUM_CTX__}"
    local host="${OLLAMA_HOST:-http://host.docker.internal:11434}"

    # Disabled, or the selected model is already a ctx-tuned variant: use as-is.
    if [ -z "${num_ctx}" ] || [ "${num_ctx}" = "0" ] || printf '%s' "${base}" | grep -qiE 'ctx[0-9]+'; then
        printf '%s' "${base}"
        return 0
    fi

    local variant="${base}-ctx${num_ctx}"
    # Create the variant once (idempotent); reuses the base model's blobs.
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
    local launch_model
    launch_model="$(_claude_ensure_ctx_variant "${selected_model}")"
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

    printf 'Launching Claude on %s\n' "${launch_model}" >&2
    OLLAMA_HOST="${OLLAMA_HOST}" \
    MAX_THINKING_TOKENS="${CLAUDE_OLLAMA_MAX_THINKING_TOKENS:-16384}" \
    ollama launch claude --model "${launch_model}" -- "${extra_args[@]}" "${effort_args[@]}" "$@"
}

claude_cloud_launch() {
    claude "$@"
}

# ── Danger mode: auto-accept permissions with git guardrails ────────────────
# Installs a temporary CLAUDE.md with git-restriction guardrails, backed up
# from the original so it can be restored after Claude exits.

_claude_ollama_install_danger_guardrails() {
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
  normal mode (`c`) for git-write operations.

DANGEREOF
    fi
    cp "$rules_file" "$claude_md"
    printf '%s' "$backup_file"
}

_claude_ollama_cleanup_danger_guardrails() {
    local workspace_root="$1" dir="$2" backup_file="$3" exit_code="${4:-$?}"
    local claude_md="${workspace_root}/CLAUDE.md"
    if [ -f "$backup_file" ] && [ -s "$backup_file" ]; then
        cp "$backup_file" "$claude_md"
    elif [ -f "$backup_file" ]; then
        rm -f "$claude_md"
    fi
    rm -f "$backup_file"
    return "$exit_code"
}

claude_ollama_launch_danger() {
    if ! command -v ollama >/dev/null 2>&1; then
        printf '%s\n' "ollama CLI is not installed in this container." >&2
        return 1
    fi

    local selected_model
    if ! selected_model="$(_claude_pick_ollama_model_impl)"; then
        return 1
    fi

    printf '%s\n' "${selected_model}" > "${CLAUDE_OLLAMA_MODEL_FILE}"
    local launch_model
    launch_model="$(_claude_ensure_ctx_variant "${selected_model}")"

    # Derive workspace root from PERSISTENCE_DIR
    local workspace_root="${PERSISTENCE_DIR%/*}"
    [ -z "$workspace_root" ] && workspace_root="$(pwd)"

    printf '\n'
    printf '  ⚠️  DANGER MODE\n'
    printf '  ────────────\n'
    printf '  Auto-accepting ALL permissions.\n'
    printf '  Model: %s\n' "${launch_model}"
    printf '\n'

    local backup_file
    backup_file="$(_claude_ollama_install_danger_guardrails "$workspace_root" "${PERSISTENCE_DIR}")"

    printf 'Launching Claude on %s (danger mode)\n' "${launch_model}" >&2
    OLLAMA_HOST="${OLLAMA_HOST}" \
    MAX_THINKING_TOKENS="${CLAUDE_OLLAMA_MAX_THINKING_TOKENS:-16384}" \
    CLAUDE_OLLAMA_DANGEROUSLY_SKIP_PERMISSIONS=true \
    ollama launch claude --model "${launch_model}" -- "$@"

    _claude_ollama_cleanup_danger_guardrails "$workspace_root" "${PERSISTENCE_DIR}" "$backup_file"
}

claude_ollama_uninstall_danger_rules() {
    local workspace_root="${PERSISTENCE_DIR%/*}"
    [ -z "$workspace_root" ] && workspace_root="$(pwd)"
    local claude_md="${workspace_root}/CLAUDE.md"
    local backup_file="${PERSISTENCE_DIR}/danger/CLAUDE.md.bak"

    if [ -f "$backup_file" ] && [ -s "$backup_file" ]; then
        cp "$backup_file" "$claude_md"
        printf 'Restored original CLAUDE.md\n'
        rm -f "$backup_file"
    elif [ -f "$claude_md" ]; then
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
alias c-danger='claude_ollama_launch_danger'
alias c-undo-danger='claude_ollama_uninstall_danger_rules'
alias c-cloud='claude_cloud_launch'
alias c-continue='cc'
alias c-med='CLAUDE_OLLAMA_EFFORT=medium c'
alias c-hi='CLAUDE_OLLAMA_EFFORT=high c'
alias c-max='CLAUDE_OLLAMA_EFFORT=high CLAUDE_OLLAMA_NUM_CTX=262144 CLAUDE_OLLAMA_MAX_THINKING_TOKENS=32768 c'
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
ensure_ollama_cli
ensure_claude_cli
ensure_persistence_link
check_ollama_host
install_shell_wrappers
print_summary
  