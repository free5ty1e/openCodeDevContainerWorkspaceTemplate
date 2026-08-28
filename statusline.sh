#!/bin/bash

## Legacy:
### input=$(cat); model=$(echo \"$input\" | jq -r \".model.display_name\"); used=$(echo \"$input\" | jq -r \".context_window.total_input_tokens // 0\"); limit=${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-200000}; pct=$(echo \"$input\" | jq -r .context_window.used_percentage); echo \"Model: $model | Context: $used/$limit ($pct%) | Limit: $limit\"

## Legacy 2: 
# input=$(cat)
# # Extract values using jq
# path=$(echo "$input" | jq -r '.workspace.current_dir // "unknown"')
# mode=$(echo "$input" | jq -r '.output_style.name // "unknown"')

# # Attempt to find effort level (not in standard JSON, but maybe available or just default)
# # Since it's not in the provided schema, we can just provide a placeholder if missing
# effort=$(echo "$input" | jq -r '.effort_level // "unknown"')

# # Count agents/subagents: Check if agent object exists
# agent_count=$(echo "$input" | jq -r 'if .agent then "1" else "0" end')

# # Process count: Use pgrep to find instances of claude (or similar)
# # Note: This might vary depending on how many shells are open.
# proc_count=$(pgrep -c "claude" || echo "0")

# echo "Path: $path | Mode: $mode | Effort: $effort | Agents: $agent_count | Shells: $proc_count"


## V3:
# # 1. Capture the piped stdin JSON blob from Claude Code
# input=$(cat)

# # 2. Extract specific variables safely via jq
# DIR_PATH=$(echo "$input" | jq -r '.workspace.current_dir // empty')
# MODEL=$(echo "$input" | jq -r '.model.display_name // empty')
# USAGE=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
# COST=$(echo "$input" | jq -r '(.cost.total_cost_usd // 0) | printf("%.4f", .)')

# # Extract just the parent folder name for clean formatting
# DIR_NAME=$(basename "$DIR_PATH")

# # 3. Read Git branch manually within the captured workspace directory
# if [ -d "$DIR_PATH" ] && git -C "$DIR_PATH" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
#     GIT_BRANCH=$(git -C "$DIR_PATH" branch --show-current 2>/dev/null)
#     GIT_STR=" 🌿 $GIT_BRANCH |"
# else
#     GIT_STR=""
# fi

# # 4. Apply optional ANSI color codes for visual tracking (e.g., warning if context > 75%)
# if (( $(echo "$USAGE > 75.0" | bc -l) )); then
#     CONTEXT_COLOR="\033[31m" # Red
# else
#     CONTEXT_COLOR="\033[32m" # Green
# fi
# RESET="\033[0m"

# # 5. Output the single line string back to Claude's terminal panel
# printf "📁 %s |%s 🤖 %s | 💰 \$%s | Ctx: %b%.1f%%%b\n" \
#     "$DIR_NAME" "$GIT_STR" "$MODEL" "$COST" "$CONTEXT_COLOR" "$USAGE" "$RESET"


## V4: Finalized version with all features integrated
# # 1. Capture the piped stdin JSON blob from Claude Code
# input=$(cat)

# # 2. Extract workspace and session data safely via jq
# DIR_PATH=$(echo "$input" | jq -r '.workspace.current_dir // empty')
# MODEL=$(echo "$input" | jq -r '.model.display_name // "Claude"')
# USAGE=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
# COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')

# # Extract token counts from input JSON
# IN_TOKENS=$(echo "$input" | jq -r '.context_window.total_input_tokens // .context_window.current_usage.input_tokens // 0')
# OUT_TOKENS=$(echo "$input" | jq -r '.context_window.total_output_tokens // .context_window.current_usage.output_tokens // 0')
# DEFAULT_MAX_TOKENS=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')

# # 3. Read Environment Variable Overrides
# COMPACT_WINDOW="${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-$DEFAULT_MAX_TOKENS}"
# MAX_OUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-N/A}"
# COMPACT_PCT="${CLAUDE_AUTOCOMPACT_PCT_OVERRIDE:-80}"

# # Calculate exact auto-compaction trigger token count based on percentage override
# COMPACT_THRESHOLD=$(( COMPACT_WINDOW * COMPACT_PCT / 100 ))
# TOTAL_CTX=$(( IN_TOKENS + OUT_TOKENS ))

# # Helper function to format token counts into 'k' notation (e.g., 44000 -> 44k)
# format_k() {
#     local count=$1
#     if [[ "$count" =~ ^[0-9]+$ ]]; then
#         if [ "$count" -ge 1000 ]; then
#             echo "$((count / 1000))k"
#         else
#             echo "$count"
#         fi
#     else
#         echo "$count"
#     fi
# }

# IN_K=$(format_k "$IN_TOKENS")
# OUT_K=$(format_k "$OUT_TOKENS")
# MAX_OUT_K=$(format_k "$MAX_OUT_TOKENS")
# CTX_K=$(format_k "$TOTAL_CTX")
# LIMIT_K=$(format_k "$COMPACT_WINDOW")
# COMPACT_K=$(format_k "$COMPACT_THRESHOLD")

# # Parent directory name
# DIR_NAME=$(basename "$DIR_PATH")

# # 4. Read Git branch manually
# if [ -d "$DIR_PATH" ] && git -C "$DIR_PATH" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
#     GIT_BRANCH=$(git -C "$DIR_PATH" branch --show-current 2>/dev/null)
#     GIT_STR=" 🌿 $GIT_BRANCH |"
# else
#     GIT_STR=""
# fi

# # 5. Dynamic ANSI colors based on configured override percentage
# WARNING_PCT=$(( COMPACT_PCT - 10 ))
# USAGE_INT=$(printf "%.0f" "$USAGE")

# if [ "$USAGE_INT" -ge "$COMPACT_PCT" ]; then
#     CONTEXT_COLOR="\033[31m" # Red (Auto-compaction active/imminent)
# elif [ "$USAGE_INT" -ge "$WARNING_PCT" ]; then
#     CONTEXT_COLOR="\033[33m" # Yellow (Approaching threshold)
# else
#     CONTEXT_COLOR="\033[32m" # Green
# fi
# RESET="\033[0m"

# # 6. Render the full status line
# printf "📁 %s |%s 🤖 %s | 💰 \$%.2f | 📥 %s | 📤 %s/%s | Ctx: %b%s/%s (%.1f%%)%b | ⚡ Compact @ %s (%s%%)\n" \
#     "$DIR_NAME" \
#     "$GIT_STR" \
#     "$MODEL" \
#     "$COST" \
#     "$IN_K" \
#     "$OUT_K" "$MAX_OUT_K" \
#     "$CONTEXT_COLOR" "$CTX_K" "$LIMIT_K" "$USAGE" "$RESET" \
#     "$COMPACT_K" "$COMPACT_PCT"


## V5: More stats, accurate context window
#!/bin/bash

# 1. Capture the piped stdin JSON blob from Claude Code
input=$(cat)

# 2. Extract core fields via jq
DIR_PATH=$(echo "$input" | jq -r '.workspace.current_dir // empty')
MODEL=$(echo "$input" | jq -r '.model.display_name // "Claude"')
USAGE=$(echo "$input" | jq -r '.context_window.used_percentage // 0')
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')

# Extract Cumulative Tokens across session
IN_TOKENS=$(echo "$input" | jq -r '.context_window.total_input_tokens // .context_window.current_usage.input_tokens // 0')
OUT_TOKENS=$(echo "$input" | jq -r '.context_window.total_output_tokens // .context_window.current_usage.output_tokens // 0')
DEFAULT_MAX_TOKENS=$(echo "$input" | jq -r '.context_window.context_window_size // 200000')

# Extract Cache Stats
CACHE_READ=$(echo "$input" | jq -r '.context_window.current_usage.cache_read_input_tokens // 0')
CACHE_WRITE=$(echo "$input" | jq -r '.context_window.current_usage.cache_creation_input_tokens // 0')

# Extract Rate Limits (if available)
RATE_5H=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')

# 3. Read Environment Variable Overrides
COMPACT_WINDOW="${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-$DEFAULT_MAX_TOKENS}"
MAX_OUT_TOKENS="${CLAUDE_CODE_MAX_OUTPUT_TOKENS:-N/A}"
COMPACT_PCT="${CLAUDE_AUTOCOMPACT_PCT_OVERRIDE:-80}"

# 4. Perform Computations
CUMULATIVE_SESSION=$(( IN_TOKENS + OUT_TOKENS ))
ACTIVE_CTX_TOKENS=$(echo "$COMPACT_WINDOW * $USAGE / 100" | bc -l | awk '{printf "%.0f", $1}')
COMPACT_THRESHOLD=$(( COMPACT_WINDOW * COMPACT_PCT / 100 ))

# Calculate Cache Efficiency Hit Rate
CACHE_TOTAL=$(( CACHE_READ + CACHE_WRITE ))
if [ "$CACHE_TOTAL" -gt 0 ]; then
    CACHE_HIT_PCT=$(echo "$CACHE_READ * 100 / $CACHE_TOTAL" | bc -l | awk '{printf "%.0f", $1}')
else
    CACHE_HIT_PCT="0"
fi

# Formatting Helper (Handles precision cleanly)
format_k() {
    local count=$1
    if [[ "$count" =~ ^[0-9]+$ ]]; then
        if [ "$count" -ge 10000 ]; then
            echo "$((count / 1000))k"
        elif [ "$count" -ge 1000 ]; then
            echo "$count" | awk '{printf "%.1fk", $1/1000}'
        else
            echo "$count"
        fi
    else
        echo "$count"
    fi
}

IN_K=$(format_k "$IN_TOKENS")
OUT_K=$(format_k "$OUT_TOKENS")
SESSION_K=$(format_k "$CUMULATIVE_SESSION")
MAX_OUT_K=$(format_k "$MAX_OUT_TOKENS")
CTX_K=$(format_k "$ACTIVE_CTX_TOKENS")
LIMIT_K=$(format_k "$COMPACT_WINDOW")
COMPACT_K=$(format_k "$COMPACT_THRESHOLD")

# 5. Git Context & Dirty Files Tracker
DIR_NAME=$(basename "$DIR_PATH")
if [ -d "$DIR_PATH" ] && git -C "$DIR_PATH" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    GIT_BRANCH=$(git -C "$DIR_PATH" branch --show-current 2>/dev/null)
    DIRTY_COUNT=$(git -C "$DIR_PATH" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    if [ "$DIRTY_COUNT" -gt 0 ]; then
        GIT_STR=" 🌿 $GIT_BRANCH (±$DIRTY_COUNT) |"
    else
        GIT_STR=" 🌿 $GIT_BRANCH |"
    fi
else
    GIT_STR=""
fi

# 6. Rate Limit Badge
if [ -n "$RATE_5H" ]; then
    RATE_STR=$(printf " | ⏳ 5h Quota: %.0f%%" "$RATE_5H")
else
    RATE_STR=""
fi

# 7. ANSI Color Coding for Context Window
WARNING_PCT=$(( COMPACT_PCT - 10 ))
USAGE_INT=$(printf "%.0f" "$USAGE")

if [ "$USAGE_INT" -ge "$COMPACT_PCT" ]; then
    CONTEXT_COLOR="\033[31m" # Red (Auto-compact imminent)
elif [ "$USAGE_INT" -ge "$WARNING_PCT" ]; then
    CONTEXT_COLOR="\033[33m" # Yellow (Approaching threshold)
else
    CONTEXT_COLOR="\033[32m" # Green
fi
RESET="\033[0m"

# 8. Render full status line
# printf "📁 %s |%s 🤖 %s | 💰 \$%.2f | 🧮 Ses: %s (📥 %s / 📤 %s max %s) | ⚡ Cache Hit: %s%% | Ctx: %b%s/%s (%.1f%%)%b | ✂️ Compact @ %s (%s%%)%s\n" \
#     "$DIR_NAME" \
#     "$GIT_STR" \
#     "$MODEL" \
#     "$COST" \
#     "$SESSION_K" "$IN_K" "$OUT_K" "$MAX_OUT_K" \
#     "$CACHE_HIT_PCT" \
#     "$CONTEXT_COLOR" "$CTX_K" "$LIMIT_K" "$USAGE" "$RESET" \
#     "$COMPACT_K" "$COMPACT_PCT" \
#     "$RATE_STR"
    
printf "📁 %s | 🤖 %s | Ctx: %b%s/%s (%.1f%%)%b | ✂️ Cmp@ %s (%s%%) | 🧮 Ses: %s (📥 %s / 📤 %s max %s) |%s ⚡ CHit: %s%%| 💰 \$%.2f%s\n" \
    "$DIR_NAME" \
    "$MODEL" \
    "$CONTEXT_COLOR" "$CTX_K" "$LIMIT_K" "$USAGE" "$RESET" \
    "$COMPACT_K" "$COMPACT_PCT" \
    "$SESSION_K" "$IN_K" "$OUT_K" "$MAX_OUT_K" \
    "$GIT_STR" \
    "$CACHE_HIT_PCT" \
    "$COST" \
    "$RATE_STR"

