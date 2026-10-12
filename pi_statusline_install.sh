#!/usr/bin/env bash
# ==============================================================================
# pi_statusline_install.sh — install the Claude Code statusline into Pi
#
# Turns a stock devcontainer into a Pi setup that uses your Claude Code
# statusline (claude_statusline.sh) as Pi's footer.
#
# Usage:
#   ./pi_statusline_install.sh                 # install / update (idempotent)
#   ./pi_statusline_install.sh --install-deps  # also apt/apk/dnf install jq+git
#   ./pi_statusline_install.sh --uninstall     # remove the installed files
#
# What it does:
#   1. Copies claude_statusline.sh (found next to this installer) to
#        $PI_CODING_AGENT_DIR/claude_statusline.sh   (default ~/.pi/agent/)
#   2. Installs the pi extension that pipes Pi session data into the script
#        $PI_CODING_AGENT_DIR/extensions/claude-statusline.ts
#   3. Runs a self-test with a sample payload and prints a preview.
#
# Notes:
#   - Safe to re-run; it overwrites previous installs (existing extension is
#     backed up to <name>.ts.bak if it differs).
#   - If a claude-statusline.ts sits next to this installer, it is preferred
#     over the embedded copy, so you can carry a customized variant.
#   - Works whether or not Pi is installed yet; the extension is picked up the
#     next time Pi starts.
#   - Optional env overrides:
#       PI_CODING_AGENT_DIR          Pi config dir   (default ~/.pi/agent)
#       CLAUDE_CODE_STATUSLINE_MODE  full|compact    (passed to the script)
# ==============================================================================

set -euo pipefail

# --- Output helpers -----------------------------------------------------------
if [ -t 1 ]; then
	B=$'\033[1m'; DIM=$'\033[2m'; CY=$'\033[36m'; GR=$'\033[32m'; YE=$'\033[33m'; RE=$'\033[31m'; R=$'\033[0m'
else
	B=""; DIM=""; CY=""; GR=""; YE=""; RE=""; R=""
fi
info() { printf '%s\n' "${CY}▸${R} $*"; }
ok()   { printf '%s\n' "${GR}✓${R} $*"; }
warn() { printf '%s\n' "${YE}!${R} $*" >&2; }
die()  { printf '%s\n' "${RE}✗ $*${R}" >&2; exit 1; }

usage() {
	sed -n '2,30p' "${BASH_SOURCE[0]}" | grep -E '^#( |$)' | sed 's/^# \{0,1\}//'
}

# --- Paths --------------------------------------------------------------------
: "${HOME:=/root}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
EXT_DIR="$AGENT_DIR/extensions"
SCRIPT_SRC="$SELF_DIR/claude_statusline.sh"
SCRIPT_DST="$AGENT_DIR/claude_statusline.sh"
EXT_DST="$EXT_DIR/claude-statusline.ts"
EXT_SIBLING="$SELF_DIR/claude-statusline.ts"

# --- Flags --------------------------------------------------------------------
MODE="install"
INSTALL_DEPS=0
for arg in "$@"; do
	case "$arg" in
		--uninstall)    MODE="uninstall" ;;
		--install-deps) INSTALL_DEPS=1 ;;
		-h|--help)      usage; exit 0 ;;
		*)              die "Unknown option: $arg (try --help)" ;;
	esac
done

# --- Dependency helpers -------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

install_deps() {
	info "Installing missing dependencies (jq, git)..."
	local sudo=""
	if [ "$(id -u)" -ne 0 ] && have sudo; then sudo="sudo"; fi
	if have apt-get; then
		$sudo apt-get update -y && $sudo apt-get install -y --no-install-recommends jq git
	elif have apk; then
		$sudo apk add --no-cache jq git
	elif have dnf; then
		$sudo dnf install -y jq git
	else
		die "No supported package manager (apt-get/apk/dnf) found. Install jq and git manually."
	fi
}

check_deps() {
	local missing=()
	have jq  || missing+=("jq")
	have git || missing+=("git")
	if [ "${#missing[@]}" -gt 0 ]; then
		if [ "$INSTALL_DEPS" -eq 1 ]; then
			install_deps
		else
			warn "Missing: ${missing[*]} — the statusline degrades without these."
			warn "Re-run with --install-deps to install them automatically."
			have jq || warn "Without jq the statusline only shows user@host."
		fi
	fi
}

# --- Uninstall ----------------------------------------------------------------
if [ "$MODE" = "uninstall" ]; then
	removed=0
	if [ -f "$SCRIPT_DST" ]; then rm -f "$SCRIPT_DST"; ok "Removed $SCRIPT_DST"; removed=1; fi
	if [ -f "$EXT_DST" ]; then rm -f "$EXT_DST"; ok "Removed $EXT_DST"; removed=1; fi
	[ -f "$EXT_DST.bak" ] && rm -f "$EXT_DST.bak" && ok "Removed $EXT_DST.bak"
	[ "$removed" -eq 1 ] || warn "Nothing to uninstall (no installed files found)."
	ok "Uninstall complete. Restart Pi if it was running."
	exit 0
fi

# --- Install ------------------------------------------------------------------
printf '%s\n' "${B}Pi statusline installer${R}"

[ -f "$SCRIPT_SRC" ] || die "claude_statusline.sh not found next to this installer (looked in $SELF_DIR)."
check_deps

info "Installing statusline script..."
mkdir -p "$AGENT_DIR" "$EXT_DIR"
cp -f "$SCRIPT_SRC" "$SCRIPT_DST"
chmod 755 "$SCRIPT_DST"
ok "Copied to $SCRIPT_DST"

info "Installing Pi extension..."
mkdir -p "$EXT_DIR"

# Source for the extension content: a sibling claude-statusline.ts wins over
# the copy embedded below, so you can carry a customized variant.
EXT_TMP=""
if [ -f "$EXT_SIBLING" ]; then
	NEW_EXT="$EXT_SIBLING"
	SRC_DESC="sibling claude-statusline.ts (custom variant)"
else
	EXT_TMP="$(mktemp)"
	NEW_EXT="$EXT_TMP"
	SRC_DESC="embedded extension"
	cat > "$EXT_TMP" <<'PI_STATUSLINE_EXTENSION_EOF'
/**
 * Claude statusline for Pi
 *
 * Reuses an existing Claude Code statusline script as Pi's footer. The script
 * expects the Claude Code statusLine JSON payload on stdin and prints one or
 * two ANSI colored lines, which are rendered verbatim.
 *
 * Script resolution order (first existing wins):
 *   1. $PI_CLAUDE_STATUSLINE_SCRIPT (explicit override)
 *   2. $PI_CODING_AGENT_DIR/claude_statusline.sh   (canonical, set by installer)
 *   3. ~/.pi/agent/claude_statusline.sh
 *   4. /workspace/claude_statusline.sh             (legacy location)
 *   5. ./claude_statusline.sh                      (current dir)
 *
 * Configuration (env):
 *   PI_CLAUDE_STATUSLINE_SCRIPT  path to the statusline script
 *   PI_STATUSLINE_VERSION        value passed as .version (default "pi")
 *   CLAUDE_CODE_STATUSLINE_MODE  passed through to the script (full|compact)
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import type { AssistantMessage } from "@earendil-works/pi-ai";
import { truncateToWidth } from "@earendil-works/pi-tui";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const VERSION = process.env.PI_STATUSLINE_VERSION ?? "pi";
const TOOL_REFRESH_DEBOUNCE_MS = 750;
const SCRIPT_TIMEOUT_MS = 4000;

interface Stats {
	input: number;
	output: number;
	cost: number;
	cacheRead: number;
	cacheWrite: number;
}

export default function (pi: ExtensionAPI) {
	let cachedLines: string[] = [];
	let requestRender: (() => void) | null = null;
	let refreshTimer: ReturnType<typeof setTimeout> | null = null;
	let inFlight = false;
	let trailingRefresh = false;
	let scriptPath: string | null = null;

	/** Resolve the statusline script location (cached after first call). */
	function getScriptPath(): string {
		if (scriptPath !== null) return scriptPath;
		const agentDir = process.env.PI_CODING_AGENT_DIR ?? join(homedir(), ".pi", "agent");
		const candidates = [
			process.env.PI_CLAUDE_STATUSLINE_SCRIPT,
			join(agentDir, "claude_statusline.sh"),
			"/workspace/claude_statusline.sh",
			join(process.cwd(), "claude_statusline.sh"),
		].filter((p): p is string => typeof p === "string" && p.length > 0);
		scriptPath = candidates.find((p) => existsSync(p)) ?? "";
		return scriptPath;
	}

	/** Sum token/cost totals over the current branch; keep last message's cache split. */
	function collectStats(ctx: ExtensionContext): Stats {
		const stats: Stats = { input: 0, output: 0, cost: 0, cacheRead: 0, cacheWrite: 0 };
		for (const entry of ctx.sessionManager.getBranch()) {
			if (entry.type === "message" && entry.message.role === "assistant") {
				const m = entry.message as AssistantMessage;
				if (m.usage) {
					stats.input += m.usage.input ?? 0;
					stats.output += m.usage.output ?? 0;
					stats.cost += m.usage.cost?.total ?? 0;
					stats.cacheRead = m.usage.cacheRead ?? 0;
					stats.cacheWrite = m.usage.cacheWrite ?? 0;
				}
			}
		}
		return stats;
	}

	/** Build the Claude Code statusLine JSON payload from pi's session state. */
	function buildPayload(ctx: ExtensionContext): string {
		const usage = ctx.getContextUsage();
		const stats = collectStats(ctx);
		const windowSize = usage?.contextWindow ?? ctx.model?.contextWindow ?? 0;
		const usedPct = usage?.percent;

		return JSON.stringify({
			model: { display_name: ctx.model?.name ?? ctx.model?.id ?? "" },
			effort: { level: ctx.thinkingLevel ?? "" },
			thinking: { enabled: (ctx.thinkingLevel ?? "off") !== "off" },
			output_style: { name: "" },
			fast_mode: false,
			exceeds_200k_tokens: false,
			workspace: { repo: null, current_dir: ctx.cwd },
			version: VERSION,
			context_window: {
				total_input_tokens: stats.input,
				total_output_tokens: stats.output,
				context_window_size: windowSize,
				used_percentage: usedPct ?? "",
				remaining_percentage: usedPct != null ? Math.max(0, Math.round((100 - usedPct) * 10) / 10) : "",
				current_usage: {
					cache_read_input_tokens: stats.cacheRead,
					cache_creation_input_tokens: stats.cacheWrite,
				},
			},
			cost: { total_cost_usd: stats.cost },
			provider: ctx.model?.provider ?? "",
		});
	}

	/** Run the statusline script with the payload on stdin; resolve with stdout. */
	function runScript(script: string, payload: string): Promise<string> {
		return new Promise((resolve) => {
			let out = "";
			let settled = false;
			const done = (result: string) => {
				if (settled) return;
				settled = true;
				clearTimeout(timer);
				resolve(result);
			};
			const child = spawn("/bin/bash", [script], {
				env: process.env,
				stdio: ["pipe", "pipe", "ignore"],
			});
			const timer = setTimeout(() => child.kill("SIGKILL"), SCRIPT_TIMEOUT_MS);
			child.stdout.on("data", (chunk: Buffer) => {
				out += chunk.toString();
			});
			child.on("error", () => done(""));
			child.on("close", () => done(out));
			child.stdin.on("error", () => {});
			child.stdin.end(`${payload}\n`);
		});
	}

	/** Refresh the cached statusline output (single-flight with trailing run). */
	function refresh(ctx: ExtensionContext): void {
		if (ctx.mode !== "tui" || !ctx.hasUI) return;
		if (inFlight) {
			trailingRefresh = true;
			return;
		}
		inFlight = true;
		trailingRefresh = false;
		const script = getScriptPath();
		if (!script) {
			inFlight = false;
			cachedLines = [];
			return;
		}
		runScript(script, buildPayload(ctx)).then((out) => {
			inFlight = false;
			cachedLines = out
				? out
						.replace(/\n+$/, "")
						.split("\n")
						.filter((line) => line.trim().length > 0)
				: [];
			requestRender?.();
			if (trailingRefresh) refresh(ctx);
		});
	}

	/** Debounced refresh for high-frequency events (tool executions). */
	function scheduleRefresh(ctx: ExtensionContext): void {
		if (ctx.mode !== "tui" || !ctx.hasUI) return;
		if (refreshTimer) clearTimeout(refreshTimer);
		refreshTimer = setTimeout(() => {
			refreshTimer = null;
			refresh(ctx);
		}, TOOL_REFRESH_DEBOUNCE_MS);
	}

	// Replace the built-in footer with the statusline output.
	pi.on("session_start", (_event, ctx) => {
		if (ctx.mode !== "tui" || !ctx.hasUI) return;
		ctx.ui.setFooter((tui, _theme, footerData) => {
			requestRender = () => tui.requestRender();
			const unsubBranch = footerData.onBranchChange(() => scheduleRefresh(ctx));
			return {
				dispose() {
					unsubBranch();
					requestRender = null;
				},
				invalidate() {},
				render(width: number): string[] {
					if (cachedLines.length === 0) return [];
					return cachedLines.map((line) => truncateToWidth(line, width));
				},
			};
		});
		refresh(ctx);
	});

	// Refresh whenever the underlying data changes.
	pi.on("turn_end", (_event, ctx) => refresh(ctx));
	pi.on("agent_end", (_event, ctx) => refresh(ctx));
	pi.on("model_select", (_event, ctx) => refresh(ctx));
	pi.on("session_compact", (_event, ctx) => refresh(ctx));
	pi.on("tool_execution_end", (_event, ctx) => scheduleRefresh(ctx));
}
PI_STATUSLINE_EXTENSION_EOF
fi

# Back up an existing extension when its content changes (preserve custom edits).
if [ -f "$EXT_DST" ] && ! cmp -s "$EXT_DST" "$NEW_EXT"; then
	cp -f "$EXT_DST" "$EXT_DST.bak"
	ok "Backed up previous extension to $EXT_DST.bak"
fi

cp -f "$NEW_EXT" "$EXT_DST"
chmod 644 "$EXT_DST"
[ -n "$EXT_TMP" ] && rm -f "$EXT_TMP"
ok "Installed $SRC_DESC to $EXT_DST"

# --- Self-test ----------------------------------------------------------------
info "Running self-test with a sample payload..."
SAMPLE='{"model":{"display_name":"pi-statusline"},"effort":{"level":"medium"},"thinking":{"enabled":true},"output_style":{"name":""},"fast_mode":false,"exceeds_200k_tokens":false,"workspace":{"repo":null,"current_dir":"'"$PWD"'"},"version":"pi","context_window":{"total_input_tokens":48200,"total_output_tokens":3120,"context_window_size":200000,"used_percentage":25.7,"remaining_percentage":74.3,"current_usage":{"cache_read_input_tokens":41000,"cache_creation_input_tokens":2100}},"cost":{"total_cost_usd":0.42},"provider":"sample"}'
if OUT="$(printf '%s\n' "$SAMPLE" | "$SCRIPT_DST" 2>/dev/null)" && [ -n "$OUT" ]; then
	ok "Self-test passed. Footer preview:"
	printf '%s\n' "$OUT" | sed 's/^/    /'
else
	warn "Self-test produced no output — check that jq is installed: $SCRIPT_DST"
fi

# --- Pi presence hint -----------------------------------------------------------
if ! have pi; then
	warn "Pi doesn't seem to be installed yet. Install it with:"
	printf '    %s\n' "curl -fsSL https://pi.dev/install.sh | sh"
	printf '    %s\n' "or: npm install -g --ignore-scripts @earendil-works/pi-coding-agent"
fi

printf '\n'
ok "${B}Install complete.${R}"
info "Start (or restart) Pi and your Claude statusline appears as the footer."
info "Tips: CLAUDE_CODE_STATUSLINE_MODE=compact pi   → one-line footer"
info "      PI_CLAUDE_STATUSLINE_SCRIPT=/path pi     → use a different script"
