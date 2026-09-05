#!/usr/bin/env bash
# ==============================================================================
# statusline.sh — Claude Code status line for the zen-proxy setup
#
# Claude Code pipes a JSON payload on stdin on every refresh. We print a status
# line packed with live session stats.
#
# MODE (set at install time via the setup script, or override with env):
#   ZEN_STATUSLINE_MODE=full     two lines: identity + resource stats (default)
#   ZEN_STATUSLINE_MODE=compact  one condensed line
#
# WHY null-safe / "n/a": when Claude Code runs THROUGH the zen translation
# proxy, the CLI receives no upstream token usage, so context_window.used_percentage
# and the token/cost counts come back null or 0. Every field is guarded and stats
# show only when present; context shows "n/a" (never a fake "100% free") when the
# proxy didn't report real usage.
#
# Field names verified against the Claude Code 2.1.251 statusLine payload.
# ==============================================================================

input="$(cat)"
[ -z "$input" ] && exit 0

if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' "$(whoami 2>/dev/null)@$(hostname -s 2>/dev/null)"
  exit 0
fi

# Mode: full (default) or compact
MODE="${ZEN_STATUSLINE_MODE:-full}"
case "$MODE" in compact|one|1|single) MODE=compact ;; *) MODE=full ;; esac

# ---- Single jq pass -> SOH-delimited, null-safe values ------------------------
# We use \x01 (SOH) as the delimiter instead of tab because bash `read`
# collapses consecutive tab delimiters, which breaks field alignment when
# empty values (like repo/dir) appear next to each other.
vals="$(printf '%s' "$input" | jq -r '
  [
    (.model.display_name // ""),
    (.effort.level // ""),
    (.thinking.enabled // false),
    (.output_style.name // ""),
    (.fast_mode // false),
    (.exceeds_200k_tokens // false),
    ((.workspace.repo // null) | if . then (.owner + "/" + .name) else "" end),
    (.workspace.current_dir // ""),
    (.version // ""),
    (.context_window.total_input_tokens // 0),
    (.context_window.total_output_tokens // 0),
    (.context_window.context_window_size // 0),
    ((.context_window.total_input_tokens // 0) + (.context_window.total_output_tokens // 0)),
    (.context_window.used_percentage // ""),
    (.context_window.remaining_percentage // ""),
    ((.context_window.current_usage // {}) | .cache_read_input_tokens // 0),
    ((.context_window.current_usage // {}) | .cache_creation_input_tokens // 0),
    (.cost.total_cost_usd // 0),
    ((.rate_limits.five_hour_window // .rate_limits.five_hour // null) | if . then (.used_percentage // "") else "" end),
    ((.rate_limits.seven_day_window // .rate_limits.seven_day // null) | if . then (.used_percentage // "") else "" end),
    (.provider // "n/a")
  ] | join("")
' 2>/dev/null)"

IFS=$'\x01' read -r model effort thinking style fast exceeds repo dir ver \
  inTok outTok winSize totalUsed used remain cacheRead cacheWrite cost rate5h rate7d provider <<< "$vals"

# ---- Git branch + dirty count (non-blocking, stderr swallowed) ---------------
branch=""
dirty=""
if [ -n "$dir" ] && command -v git >/dev/null 2>&1; then
  if command -v timeout >/dev/null 2>&1; then
    branch="$(timeout 1 git -C "$dir" -c core.optionalLocks=false rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  else
    branch="$(git -C "$dir" -c core.optionalLocks=false rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  fi
  if [ -n "$branch" ]; then
    dc="$(git -C "$dir" -c core.optionalLocks=false status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
    [ "${dc:-0}" -gt 0 ] && dirty="$dc"
  fi
fi

# ---- Path shortening (~/..., last two components) -----------------------------
home="${HOME:-}"
shortdir="${dir/#$home/~}"
[ -z "$shortdir" ] && shortdir="$dir"
if [ "$shortdir" = "/" ]; then
  shortdir="/"
elif [ -n "$shortdir" ]; then
  shortdir="$(basename "$(dirname "$shortdir")")/$(basename "$shortdir")"
fi

# ---- Helpers ----------------------------------------------------------------
format_k() {
  local c="$1"
  case "$c" in
    ''|*[!0-9]*) printf '%s' "$c"; return ;;
  esac
  if [ "$c" -ge 10000 ]; then
    printf '%dk' "$((c / 1000))"
  elif [ "$c" -ge 1000 ]; then
    awk -v n="$c" 'BEGIN{printf "%.1fk", n/1000}'
  else
    printf '%s' "$c"
  fi
}

# Context bar: 10 block chars, filled = pct.
ctx_bar() {
  local pct="$1" w=10 f i bars=""
  f="$(awk -v p="$pct" -v w="$w" 'BEGIN{printf "%d", p*w/100+0.5}' 2>/dev/null || echo 0)"
  case "$f" in *[!0-9]*) f=0 ;; esac
  [ "$f" -gt "$w" ] && f=$w
  for ((i = 0; i < w; i++)); do
    if [ "$i" -lt "$f" ]; then bars="${bars}█"; else bars="${bars}░"; fi
  done
  printf '%s' "$bars"
}

# Context color by threshold.
ctx_color() {
  local p="$1" c="$GR"
  awk "BEGIN{exit !($p >= 80)}" 2>/dev/null && c="$RE"
  awk "BEGIN{exit !($p >= 60)}" 2>/dev/null && c="$YE"
  printf '%s' "$c"
}

# Cache hit % (read / (read + write)).
cache_hit() {
  local ct=$(( ${cacheRead:-0} + ${cacheWrite:-0} ))
  if [ "$ct" -gt 0 ]; then printf '%d' "$(( cacheRead * 100 / ct ))"; else printf '0'; fi
}

# ---- Colors (subtle) ---------------------------------------------------------
R=$'\033[0m'; DIM=$'\033[2m'; CY=$'\033[36m'; YE=$'\033[33m'
GR=$'\033[32m'; RE=$'\033[31m'
SEP="${DIM} • ${R}"

# ---- Context segment (shared by both modes) ----------------------------------
# Format: 10k [    ] 5% of 200k   (tokens [bar] pct% of window)
ctx_seg() {
  if [ -n "$used" ] && [ "$used" != "0" ] && [ "$used" != "0.0" ]; then
    local tokens_str=""
    if [ "${totalUsed:-0}" -gt 0 ] 2>/dev/null; then
      tokens_str="$(format_k "$totalUsed")"
    fi
    local win_str=""
    if [ "${winSize:-0}" -gt 0 ] 2>/dev/null; then
      win_str="$(format_k "$winSize")"
    fi
    printf '%s' "${tokens_str:+$tokens_str }📊 [$(ctx_bar "$used")] $(ctx_color "$used")${used}%${R}${win_str:+ of $win_str}"
  else
    printf '%s' "📊 n/a"
  fi
}

# Auto-compaction segment: shows the configured threshold as both a percentage
# and a token count (= context window size × threshold / 100).
# The value comes from the CLAUDE_CODE_COMPACTION_LEVEL env var set by the
# provider launch scripts (e.g. claude_opencode_zen.py). Guards are null-safe.
ctx_compaction_seg() {
  local ca="${CLAUDE_CODE_COMPACTION_LEVEL:-}"
  case "$ca" in
    ''|*[!0-9]*) printf '🗜️ n/a'; return ;;
  esac
  local win_str=""
  if [ "${winSize:-0}" -gt 0 ] 2>/dev/null; then
    win_str="$(format_k $(( winSize * ca / 100 )))"
  fi
  local toks=""
  [ -n "$win_str" ] && toks=" / ${win_str}"
  printf '🗜️ %s%%%s' "$ca" "$toks"
}

# ---- FULL mode: two lines ----------------------------------------------------
if [ "$MODE" = full ]; then
  L1=""
  seg() { [ -n "$2" ] && L1="${L1}${L1:+$SEP}$1$2"; }
  seg "📦 " "$repo"
  seg "🌐 " "$provider"
  seg "🤖 " "$model"
  seg "🎚️ " "$effort"
  [ "$thinking" = true ] && seg "💭 " "on" || seg "💭 " "off"
  seg "🌿 " "${branch}${dirty:+ ±$dirty}"
  [ "$style" != "default" ] && [ -n "$style" ] && seg "🎨 " "$style"
  [ "$fast" = true ] && seg "⚡ " "fast"
  seg "📏 " "$(format_k "$winSize")"

  L2=""
  seg2() { [ -n "$2" ] && L2="${L2}${L2:+$SEP}$1$2"; }
  seg2 "" "$(ctx_seg)"
  seg2 "" "$(ctx_compaction_seg)"
  if [ "${inTok:-0}" -gt 0 ] || [ "${outTok:-0}" -gt 0 ]; then
    seg2 "📥📤 " "$(format_k "$inTok")/$(format_k "$outTok")"
  fi
  if [ "${cacheRead:-0}" -gt 0 ] || [ "${cacheWrite:-0}" -gt 0 ]; then
    seg2 "💾 " "$(cache_hit)%"
  fi
  [ -n "$remain" ] && seg2 "📊 rem " "${remain}%"
  seg2 "📁 " "$shortdir"
  seg2 "🏷️ " "$ver"
  awk "BEGIN{exit !($cost > 0)}" 2>/dev/null && seg2 "💰 " "$(printf '$%.2f' "$cost")"
  [ -n "$rate5h" ] && seg2 "⏳5h " "${rate5h}%"
  [ -n "$rate7d" ] && seg2 "⏳7d " "${rate7d}%"
  [ "$exceeds" = true ] && seg2 "⚠️ " "200k"

  [ -n "$L1" ] && printf '%s\n' "$L1"
  [ -n "$L2" ] && printf '%s\n' "$L2"
  exit 0
fi

# ---- COMPACT mode: one line --------------------------------------------------
L=""
segc() { [ -n "$2" ] && L="${L}${L:+$SEP}$1$2"; }
segc "📦 " "$repo"
segc "🌿 " "${branch}${dirty:+ ±$dirty}"
segc "🤖 " "$model"
segc "🎚️ " "$effort"
segc "" "$(ctx_seg)"
if [ "${cacheRead:-0}" -gt 0 ] || [ "${cacheWrite:-0}" -gt 0 ]; then
  segc "💾 " "$(cache_hit)%"
fi
if [ "${inTok:-0}" -gt 0 ] || [ "${outTok:-0}" -gt 0 ]; then
  segc "📥📤 " "$(format_k "$inTok")/$(format_k "$outTok")"
fi
awk "BEGIN{exit !($cost > 0)}" 2>/dev/null && segc "💰 " "$(printf '$%.2f' "$cost")"
segc "🏷️ " "$ver"
[ -n "$L" ] && printf '%s\n' "$L"
