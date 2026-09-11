#!/usr/bin/env python3
"""
Claude ↔ Opencode Session Manager

This script allows syncing sessions between Claude Code and Opencode with an interactive selector.
"""
import argparse
import os
import sys
import json
import shutil
import time
from datetime import datetime

# Import shared library
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_library import (
    # Prerequisites
    ensure_prerequisites,
    # API Key management (not needed for this script, but we keep the import for consistency)
    get_and_cache_api_key,
    cache_api_key,
    # Model utilities (not needed, but we keep for consistency)
    filter_chat_models,
    format_token_count,
    # HTTP (not needed, but we keep for consistency)
    http_get_json,
    # Proxy management (not needed, but we keep for consistency)
    start_litellm_proxy,
    stop_running_proxy,
    test_proxy_connection,
    # Cache
    read_cache,
    write_cache,
    # Context window (not needed, but we keep for consistency)
    load_model_context,
    save_model_context,
    # Compaction (not needed, but we keep for consistency)
    load_model_compaction,
    save_model_compaction,
    # Statusline mode (not needed, but we keep for consistency)
    load_statusline_mode,
    save_statusline_mode,
    # Favorites (we will use these)
    load_favorites,
    save_favorites,
    # Terminal
    get_terminal_height,
    # Persistence & statusline
    setup_claude_persistence,
    setup_statusline_symlink,
)

# ─── Configuration ─────────────────────────────────────────────────────────────

# Default paths for session storage (can be overridden by command line or auto-detected)
# We will use a list of candidate directories for each type.
CLAUDE_SESSIONS_CANDIDATES = [
    "~/.claude/projects",
    ".claude/projects"
]
OPCODE_SESSIONS_CANDIDATES = [
    "~/.opencode/sessions",
    "~/.ai_working/opencode_data",
    ".opencode/sessions",
    ".ai_working/opencode_data"
]
FAVORITES_CACHE_FILE = os.path.expanduser("~/.claude_opencode_session_favorites")

# ─── Helper Functions ──────────────────────────────────────────────────────────

def get_sessions_dir(candidates):
    """
    Given a list of candidate directory strings, return the first one that exists.
    Each candidate string is processed as follows:
      - If it starts with "~", expand the user.
      - Then, if the resulting path is not absolute, make it absolute by joining with the current working directory.
      - Then check if it exists and is a directory.
    Returns the absolute path of the first existing directory, or None if none found.
    """
    for candidate in candidates:
        if not candidate:
            continue
        # Expand user
        path = os.path.expanduser(candidate)
        if not os.path.isabs(path):
            path = os.path.abspath(os.path.join(os.getcwd(), path))
        if os.path.isdir(path):
            return path
    return None

def list_sessions(base_dir, session_type):
    """List sessions in the given base directory.
    session_type: either 'claude' or 'opencode'
    Returns a list of dicts with keys: 'id', 'path', 'modified', 'title'.
    """
    sessions = []
    if not base_dir or not os.path.isdir(base_dir):
        return sessions

    if session_type == 'claude':
        # Claude layout: each subdirectory of base_dir may contain .jsonl files (each file is a session)
        # We look for .jsonl files in the immediate subdirectories of base_dir.
        for item in os.listdir(base_dir):
            item_path = os.path.join(base_dir, item)
            if not os.path.isdir(item_path):
                continue
            # Look for .jsonl files in this subdirectory
            for fname in os.listdir(item_path):
                if not fname.endswith(".jsonl"):
                    continue
                session_id = fname[:-6]  # remove .jsonl
                chat_file = os.path.join(item_path, fname)
                if not os.path.isfile(chat_file):
                    continue
                modified = os.path.getmtime(chat_file)
                title = f"Session {session_id}"
                try:
                    with open(chat_file, "r", encoding="utf-8") as f:
                        # Read all lines to find the most recent user message with content
                        lines = f.readlines()
                        if lines:
                            # Check for customTitle in the first line (newer Claude sessions)
                            try:
                                first_data = json.loads(lines[0].strip())
                                if isinstance(first_data, dict) and "customTitle" in first_data and isinstance(first_data["customTitle"], str) and len(first_data["customTitle"].strip()) > 0:
                                    custom_title = first_data["customTitle"]
                                    title = custom_title[:50] + ("..." if len(custom_title) > 50 else "")
                            except (json.JSONDecodeError, IndexError):
                                pass

                            # If we didn't find a customTitle, scan for user messages
                            if title == f"Session {session_id}":
                                # Scan lines in reverse order to find the most recent user message
                                for line in reversed(lines):
                                    line = line.strip()
                                    if not line:
                                        continue
                                    try:
                                        data = json.loads(line)
                                        if isinstance(data, dict) and data.get("type") == "user" and "message" in data:
                                            msg = data["message"]
                                            if isinstance(msg, dict) and "content" in msg and isinstance(msg["content"], str) and len(msg["content"].strip()) > 0:
                                                content = msg["content"]
                                                title = content[:50] + ("..." if len(content) > 50 else "")
                                                break
                                            elif isinstance(msg, str) and len(msg.strip()) > 0:
                                                title = msg[:50] + ("..." if len(msg) > 50 else "")
                                                break
                                    except (json.JSONDecodeError, KeyError):
                                        continue
                except Exception:
                    pass
                sessions.append({
                    "id": session_id,
                    "path": chat_file,   # the .jsonl file path
                    "modified": modified,
                    "title": title,
                })
        # Sort by modification time, newest first
        sessions.sort(key=lambda x: x["modified"], reverse=True)
        return sessions

    elif session_type == 'opencode':
        # Opencode stores session data in multiple locations:
        # 1. Individual session files in storage/session_diff/ (chat sessions)
        # 2. prompt-history.jsonl in state/opencode/ (as a fallback session)
        sessions_found = {}  # Avoid duplicates by session ID

        # Look for individual session JSON files in storage/session_diff/
        # These are the actual chat sessions
        session_diff_dir = os.path.join(base_dir, "storage", "session_diff")
        if os.path.isdir(session_diff_dir):
            for file in os.listdir(session_diff_dir):
                if not file.endswith(".json"):
                    continue
                # Skip non-session files
                if file in ["model.json", "kv.json"]:
                    continue
                session_path = os.path.join(session_diff_dir, file)
                # Extract session ID from filename (without .json extension)
                session_id = os.path.splitext(file)[0]
                # Skip if we've already processed this session
                if session_id in sessions_found:
                    continue
                modified = os.path.getmtime(session_path)
                title = f"Session {session_id}"
                try:
                    with open(session_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            # Try to get title from the session data
                            if "title" in data and isinstance(data["title"], str) and data["title"].strip():
                                title = data["title"]
                            # Fallback: try to extract from messages or other content
                            elif "messages" in data and isinstance(data["messages"], list) and len(data["messages"]) > 0:
                                # Look through recent messages for content
                                for msg in reversed(data["messages"]):  # Check recent messages first
                                    if isinstance(msg, dict) and "content" in msg:
                                        content = msg["content"]
                                        if isinstance(content, str) and len(content.strip()) > 0:
                                            title = content[:50] + ("..." if len(content) > 50 else "")
                                            break
                            # Another fallback: look for any string content that looks like a message
                            elif "content" in data and isinstance(data["content"], str) and len(data["content"].strip()) > 0:
                                content = data["content"]
                                # Only use as title if it looks like substantive content (not just config/status)
                                if len(content.strip()) > 10 and not content.startswith('{') and not content.startswith('['):
                                    title = content[:50] + ("..." if len(content) > 50 else "")
                except Exception:
                    pass  # Keep default title if we can't read the file

                sessions_found[session_id] = {
                    "id": session_id,
                    "path": session_path,
                    "modified": modified,
                    "title": title,
                }

        # Also check for prompt-history.jsonl as a fallback session source
        prompt_history_path = os.path.join(base_dir, "state", "opencode", "prompt-history.jsonl")
        if os.path.exists(prompt_history_path):
            # Use a descriptive ID for prompt history
            session_id = "prompt-history"
            if session_id not in sessions_found:
                modified = os.path.getmtime(prompt_history_path)
                title = "Prompt History"
                try:
                    with open(prompt_history_path, "r", encoding="utf-8") as f:
                        first_line = f.readline().strip()
                        if first_line:
                            data = json.loads(first_line)
                            if isinstance(data, dict) and "message" in data:
                                msg = data["message"]
                                if isinstance(msg, str) and len(msg) > 0:
                                    title = msg[:50] + ("..." if len(msg) > 50 else "")
                except Exception:
                    pass

                sessions_found[session_id] = {
                    "id": session_id,
                    "path": prompt_history_path,
                    "modified": modified,
                    "title": title,
                }

        # Convert to list and sort by modification time, newest first
        sessions = list(sessions_found.values())
        sessions.sort(key=lambda x: x["modified"], reverse=True)
        return sessions

    return sessions

def sync_claude_to_opencode(claude_session, opencode_base_dir):
    """Copy a Claude session (.jsonl file) to Opencode as a new session.
    We'll create a new session file in opencode_base_dir with a generated ID.
    """
    # Generate a new session ID based on timestamp
    from datetime import datetime
    new_id = f"sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{claude_session['id']}"
    new_path = os.path.join(opencode_base_dir, f"{new_id}.jsonl")
    try:
        shutil.copy2(claude_session['path'], new_path)
        return new_path
    except Exception as e:
        print(f"⚠️  Failed to sync Claude session to Opencode: {e}")
    return None

def sync_opencode_to_claude(opencode_session, claude_base_dir):
    """Copy an Opencode session (.jsonl file) to Claude as a new session.
    We'll create a new subdirectory under claude_base_dir with a generated ID,
    and put the .jsonl file inside as chat.jsonl (to match Claude's expected layout).
    """
    from datetime import datetime
    # Create a new subdirectory under claude_base_dir
    new_dir_name = f"sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{opencode_session['id']}"
    new_dir_path = os.path.join(claude_base_dir, new_dir_name)
    try:
        os.makedirs(new_dir_path, exist_ok=True)
        # The chat.jsonl file inside this new directory
        new_chat_file = os.path.join(new_dir_path, "chat.jsonl")
        # Copy the Opencode session file to this new chat.jsonl file
        shutil.copy2(opencode_session['path'], new_chat_file)
        return new_dir_path   # return the directory path (which represents the Claude session)
    except Exception as e:
        print(f"⚠️  Failed to sync Opencode session to Claude: {e}")
    return None

# ─── Session Selector (using prompt_toolkit) ──────────────────────────────────
def session_selector(sessions, session_type, favorites):
    """Interactive session selector using prompt_toolkit.

    Returns (selected_session, action) where:
        selected_session: the session dict (or None if none selected)
        action: one of 'sync', 'switch_view', 'toggle_favorite', 'exit'
    """
    if not sessions:
        return None, None

    # Import prompt_toolkit here (after prerequisites are ensured installed)
    from prompt_toolkit import Application
    from prompt_toolkit.layout import Layout, HSplit
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.styles import Style

    terminal_height = get_terminal_height()
    visible_count = max(3, min(terminal_height - 6, len(sessions)))
    start_idx = 0
    current = [start_idx, 0]  # [0]=idx, [1]=view (0=sessions, 1=favorites) - we don't use favorites view for now
    result = [None, None]  # [0]=idx, [1]=action

    if favorites is None:
        favorites = set()

    def build_options():
        # We always show the sessions list (no favorites view for simplicity)
        return [{"type": "session", "id": s["id"], "idx": i, "session": s} for i, s in enumerate(sessions)]

    def get_formatted_options():
        opts = build_options()
        top = current[0] - (current[0] % visible_count)
        top = max(0, min(top, max(0, len(opts) - visible_count)))
        if current[0] < top:
            top = current[0]
        elif current[0] >= top + visible_count:
            top = current[0] - visible_count + 1

        # View label
        view_label = f"{session_type} Sessions"
        fragments = []
        fragments.append(("class:prompt", f"Select a session to sync • ({view_label}) • "))

        items_above = top
        items_below = len(opts) - (top + visible_count)

        for i in range(top, min(top + visible_count, len(opts))):
            if i == current[0]:
                session = opts[i]["session"]
                is_fav = session["id"] in favorites
                prefix = "★ " if is_fav else "  "
                # Show title followed by ID in parentheses at the end
                fragments.append(("class:current", f"  {prefix}{session['title']} (ID: {session['id']})\n"))
            else:
                session = opts[i]["session"]
                is_fav = session["id"] in favorites
                prefix = "★ " if is_fav else "  "
                fragments.append(("class:normal", f"    {prefix}{session['title']} (ID: {session['id']})\n"))

        if items_above > 0 or items_below > 0:
            hint_parts = []
            if items_above > 0:
                hint_parts.append(f"{items_above} above")
            if items_below > 0:
                hint_parts.append(f"{items_below} below")
            fragments.append(("class:hint", "  " + " ".join(hint_parts) + "\n"))

        return fragments

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        if current[0] > 0:
            current[0] -= 1

    @kb.add("down")
    def _(event):
        if current[0] < len(sessions) - 1:
            current[0] += 1

    @kb.add("pageup")
    def _(event):
        page = min(visible_count - 1, len(sessions))
        current[0] = max(0, current[0] - page)

    @kb.add("pagedown")
    def _(event):
        page = min(visible_count - 1, len(sessions))
        current[0] = min(len(sessions) - 1, current[0] + page)

    @kb.add("home")
    def _(event):
        current[0] = 0

    @kb.add("end")
    def _(event):
        opts_len = len(sessions)
        current[0] = opts_len - 1 if opts_len > 0 else 0

    @kb.add("left")
    def _(event):
        result[1] = 'switch_view'
        event.app.exit()

    @kb.add("right")
    def _(event):
        result[1] = 'switch_view'
        event.app.exit()

    @kb.add("space")
    def _(event):
        if current[0] < len(sessions):
            session_id = sessions[current[0]]["id"]
            if session_id in favorites:
                favorites.discard(session_id)
            else:
                favorites.add(session_id)
            result[1] = 'toggle_favorite'
            event.app.exit()

    @kb.add("enter")
    def _(event):
        if current[0] < len(sessions):
            result[0] = current[0]
            result[1] = 'sync'
            event.app.exit()
        else:
            result[0] = None
            result[1] = None
            event.app.exit()

    @kb.add("escape")
    def _(event):
        result[1] = 'exit'
        event.app.exit()

    control = FormattedTextControl(get_formatted_options)
    window = Window(
        content=control,
        height=max(visible_count + 4, 8),
        always_hide_cursor=True,
    )

    style = Style.from_dict({
        "current": "reverse",
        "normal": "",
        "hint": "italic #888888",
        "prompt": "bold",
    })

    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=False,
        style=style,
        mouse_support=False,
    )

    try:
        app.run()
    except EOFError:
        # If we get EOFError, it means we can't read input (likely non-terminal environment)
        # Treat this as a request to exit
        result[1] = 'exit'
    except Exception as e:
        # If we get any other exception, treat it as a request to exit unless we already have an action
        if result[1] is None:
            result[1] = 'exit'

    if result[0] is not None:
        selected_idx = result[0]
        selected_session = sessions[selected_idx]
        action = result[1]
        return selected_session, action
    # If no session was selected but we have an action (like exit or switch_view), return it
    if result[1] is not None:
        return None, result[1]
    return None, None

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Manage sessions between Claude Code and Opencode"
    )
    parser.add_argument(
        "--opencode-location",
        type=str,
        default="",
        help="Custom Opencode sessions directory (e.g., .opencode). Overrides auto-detection.",
    )
    parser.add_argument(
        "--claude-location",
        type=str,
        default="",
        help="Custom Claude sessions directory (e.g., .claude_persist). Overrides auto-detection.",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        default=False,
        help="Skip Claude Code permission prompts (safe in devcontainer environments)",
    )
    parser.add_argument(
        "--clear-api-key",
        action="store_true",
        default=False,
        help="Clear the cached API key and prompt again",
    )
    parser.add_argument(
        "--accept-all-defaults",
        action="store_true",
        default=False,
        help="Auto-accept cached/default values for all prompts (quick re-launch)",
    )
    args = parser.parse_args()

    # We don't have usage notes for this script yet, but we can add a simple banner
    print("=" * 60)
    print("🔄  Claude ↔ Opencode Session Manager")
    print("=" * 60)
    print()

    if not ensure_prerequisites(args):
        print("❌ Prerequisites check failed. Exiting.")
        sys.exit(1)

    # Load favorites
    favorites = load_favorites(FAVORITES_CACHE_FILE)
    if favorites is None:
        favorites = set()

    # Build candidate lists for Claude and Opencode
    claude_candidates = []
    if args.claude_location:
        claude_candidates.append(args.claude_location)
    claude_candidates.extend(CLAUDE_SESSIONS_CANDIDATES)

    opencode_candidates = []
    if args.opencode_location:
        opencode_candidates.append(args.opencode_location)
    opencode_candidates.extend(OPCODE_SESSIONS_CANDIDATES)

    # Get the base directories for sessions
    claude_base = get_sessions_dir(claude_candidates)
    opencode_base = get_sessions_dir(opencode_candidates)

    # Initial session lists
    claude_sessions = list_sessions(claude_base, 'claude') if claude_base else []
    opencode_sessions = list_sessions(opencode_base, 'opencode') if opencode_base else []

    print(f"🔍 Session locations:")
    print(f"   Claude: {claude_base or 'Not found'}")
    print(f"   Opencode: {opencode_base or 'Not found'}")
    print()
    print(f"Found {len(claude_sessions)} Claude sessions and {len(opencode_sessions)} Opencode sessions.")
    print()
    # Pause for the user to see the counts
    try:
        input("Press Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        print("\nExiting...")
        sys.exit(0)

    # Main loop
    current_view = 0  # 0 for Claude, 1 for Opencode
    # If the initial view has no sessions, try to switch to the other view if it has sessions
    if current_view == 0 and not claude_sessions and opencode_sessions:
        current_view = 1
    elif current_view == 1 and not opencode_sessions and claude_sessions:
        current_view = 0

    try:
        while True:
            # Determine which sessions to show based on current_view
            if current_view == 0:
                sessions = claude_sessions
                session_type = "Claude"
                base_dir = claude_base
                other_base_dir = opencode_base
                other_sessions = opencode_sessions
                other_type = "Opencode"
            else:
                sessions = opencode_sessions
                session_type = "Opencode"
                base_dir = opencode_base
                other_base_dir = claude_base
                other_sessions = claude_sessions
                other_type = "Claude"

            # If there are no sessions in the current view, we can still show a message and allow switching
            if not sessions:
                print(f"\nNo {session_type} sessions found.")
                if other_sessions:
                    print(f"Press Left/Right to switch to {other_type} sessions ({len(other_sessions)} found).")
                else:
                    print(f"No {other_type} sessions found either.")
                # Wait for a key press to switch view or exit
                try:
                    key = input("\nPress Left/Right to switch view, or any other key to exit: ").strip().lower()
                    if key in ("left", "right"):
                        current_view = 1 - current_view
                        continue
                    else:
                        break
                except (EOFError, KeyboardInterrupt):
                    break

            # Show the selector for the current view
            selected_session, action = session_selector(sessions, session_type, favorites)

            if action == 'exit':
                break
            elif action == 'switch_view':
                current_view = 1 - current_view
                # When switching view, we might want to reset the selection index? The selector will start at top.
                continue
            elif action == 'toggle_favorite':
                # Toggle favorite for the selected session (if any)
                if selected_session is not None:
                    session_id = selected_session["id"]
                    if session_id in favorites:
                        favorites.discard(session_id)
                    else:
                        favorites.add(session_id)
                    # Save favorites immediately
                    save_favorites(favorites, FAVORITES_CACHE_FILE)
                    # We'll redraw the list to show the updated favorite status
                    continue
                else:
                    # No session selected, just redraw
                    continue
            elif action == 'sync':
                if selected_session is not None:
                    # Show confirmation
                    from_to = f"{session_type} → {other_type}"
                    sync_msg = f"About to sync {session_type} session: '{selected_session['title']}' (ID: {selected_session['id']}) to {other_type} as a new session."
                    print(f"\n{sync_msg}")
                    try:
                        if args.accept_all_defaults:
                            # Auto-decline when using --accept-all-defaults
                            print("Sync declined (--accept-all-defaults).")
                            continue
                        answer = input("Sync this session? [y/N]: ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        print("\nSync declined.")
                        continue
                    if answer != 'y':
                        print("Sync declined.")
                        continue
                    # Proceed with sync
                    if current_view == 0:  # viewing Claude, sync to Opencode
                        if opencode_base is not None:
                            new_path = sync_claude_to_opencode(selected_session, opencode_base)
                            if new_path:
                                print(f"\n✅ Synced Claude session to Opencode: {new_path}")
                                # Optionally, we could add the new session to the opencode_sessions list for immediate viewing
                                # But we'll just let the user switch view to see it.
                            else:
                                print(f"\n❌ Failed to sync Claude session to Opencode")
                        else:
                            print(f"\n❌ Opencode sessions directory not set")
                    else:  # viewing Opencode, sync to Claude
                        if claude_base is not None:
                            new_path = sync_opencode_to_claude(selected_session, claude_base)
                            if new_path:
                                print(f"\n✅ Synced Opencode session to Claude: {new_path}")
                            else:
                                print(f"\n❌ Failed to sync Opencode session to Claude")
                        else:
                            print(f"\n❌ Claude sessions directory not set")
                    # Save favorites after any change (sync doesn't change favorites, but we save anyway)
                    save_favorites(favorites, FAVORITES_CACHE_FILE)
                    # Pause briefly to let the user see the message
                    time.sleep(1.5)
                    # After syncing, we can optionally refresh the other side's list if we want to show it immediately.
                    # For simplicity, we'll just continue and let the user switch view to see the new session.
                    continue
                else:
                    # No session selected, just redraw
                    continue
            # If action is None (just moved highlight), we just redraw the list in the next loop iteration.

    except KeyboardInterrupt:
        print("\n👋 Interrupted. Exiting...")
        pass

    # Save favorites before exiting
    save_favorites(favorites, FAVORITES_CACHE_FILE)
    print("\n👋 Goodbye!")

if __name__ == "__main__":
    main()