#!/usr/bin/env bash
# ==============================================================================
# statusline.sh — Claude Code status line for the zen-proxy setup
#
# Claude Code pipes a JSON payload on stdin on every refresh. We print a 2-line
# status line packed with live session stats.
#
# IMPORTANT (why this is written the way it is):
#   When Claude Code runs THROUGH the zen translation proxy, the CLI does NOT
#   receive upstream token usage, so fields like context_window.used_percentage
#   and the token/cost counts come back null or 0. Every field below is
#   null-guarded and stats are shown *only when present*, so the bar never
#   fills up with misleading zeros and never goes blank on a missing field.
#
# Field names verified against the Claude Code 2.1.251 statusLine payload
# (captured live from a running session).
# ==============================================================================

input="$(cat)"
[ -z "$input" ] && exit 0

# Fallback if jq is missing (statusline would otherwise be blank).
if ! command -v jq >/dev/null 2>&1; then
  printf '%s\n' "$(whoami 2>/dev/null)@$(hostname -s 2>/dev/null)"
  exit 0
fi

# ---- Single jq pass -> tab-separated, null-safe values -----------------------
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
    (.context_window.used_percentage // ""),
    (.context_window.remaining_percentage // ""),
    ((.context_window.current_usage // {}) | .cache_read_input_tokens // 0),
    ((.context_window.current_usage // {}) | .cache_creation_input_tokens // 0),
    (.cost.total_cost_usd // 0),
    ((.rate_limits.five_hour_window // .rate_limits.five_hour // null) | if . then (.used_percentage // "") else "" end),
    ((.rate_limits.seven_day_window // .rate_limits.seven_day // null) | if . then (.used_percentage // "") else "" end)
  ] | @tsv
' 2>/dev/null)"

IFS=$'\t' read -r model effort thinking style fast exceeds repo dir ver \
  inTok outTok winSize used remain cacheRead cacheWrite cost rate5h rate7d <<< "$vals"

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
if [ -n "$shortdir" ]; then
  shortdir="$(basename "$(dirname "$shortdir")")/$(basename "$shortdir")"
fi

# ---- Token formatter (1234 -> 1.2k, 44000 -> 44k) -----------------------------
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

# ---- Colors (subtle) ----------------------------------------------------------
R=$'\033[0m'; DIM=$'\033[2m'; CY=$'\033[36m'; YE=$'\033[33m'
GR=$'\033[32m'; RE=$'\033[31m'

# ---- Line 1: identity / mode --------------------------------------------------
l1=""
add() { [ -n "$2" ] && l1="${l1}${l1:+${DIM} • ${R}}${CY}$1${R}: $2"; }
add "repo"   "$repo"
add "branch" "${branch}${dirty:+ ±$dirty}"
add "dir"    "$shortdir"
add "model"  "$model"
add "effort" "$effort"
[ "$thinking" = true ] && add "think" "on" || add "think" "off"
[ "$style" != "default" ] && [ -n "$style" ] && add "style" "$style"
[ "$fast" = true ] && add "fast" "on"

# ---- Line 2: resource usage / stats -------------------------------------------
l2=""
add2() { [ -n "$2" ] && l2="${l2}${l2:+${DIM} • ${R}}${YE}$1${R}: $2"; }

# Context window (color by usage when a real % is present)
if [ -n "$used" ] && [ "$used" != "0" ] && [ "$used" != "0.0" ]; then
  ctx_color="$GR"
  if awk "BEGIN{exit !($used >= 80)}" 2>/dev/null; then ctx_color="$RE"
  elif awk "BEGIN{exit !($used >= 60)}" 2>/dev/null; then ctx_color="$YE"; fi
  add2 "ctx" "${ctx_color}${used}%${R}"
elif [ -n "$remain" ]; then
  add2 "ctx" "${remain}% free"
else
  add2 "ctx" "n/a"
fi

# Tokens (only when the CLI actually counted some)
if [ "${inTok:-0}" -gt 0 ] || [ "${outTok:-0}" -gt 0 ]; then
  add2 "tok" "in $(format_k "$inTok") / out $(format_k "$outTok")"
fi

# Cache read/write (only when present)
if [ "${cacheRead:-0}" -gt 0 ] || [ "${cacheWrite:-0}" -gt 0 ]; then
  add2 "cache" "R $(format_k "$cacheRead") / W $(format_k "$cacheWrite")"
fi

# Cost (only when > 0 — proxied sessions report 0)
if awk "BEGIN{exit !($cost > 0)}" 2>/dev/null; then
  add2 "cost" "$(printf '$%.2f' "$cost")"
fi

# Upstream rate limits (usually absent through the proxy — show if present)
[ -n "$rate5h" ] && add2 "5h" "${rate5h}%"
[ -n "$rate7d" ] && add2 "7d" "${rate7d}%"

add2 "v" "$ver"
[ "$exceeds" = true ] && add2 "warn" "${RE}200k!${R}"

if [ -n "$l2" ]; then printf '%s\n%s\n' "$l1" "$l2"; else printf '%s\n' "$l1"; fi
