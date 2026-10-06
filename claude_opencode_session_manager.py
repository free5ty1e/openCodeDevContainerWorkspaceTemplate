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
import sqlite3
import subprocess
import tempfile
import random
import hashlib
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
    # ~/.claude is normally a symlink into the workspace (see
    # claude_library.setup_claude_persistence). In a freshly recreated devcontainer
    # that symlink does not exist yet, so fall back to the persistent copy directly
    # rather than reporting zero Claude sessions.
    ".claude_persist/projects",
    ".claude/projects"
]
OPCODE_SESSIONS_CANDIDATES = [
    "~/.local/share/opencode",
    "~/.opencode/sessions",
    "~/.ai_working/opencode_data",
    ".opencode/sessions",
    ".ai_working/opencode_data"
]
FAVORITES_CACHE_FILE = os.path.expanduser("~/.claude_opencode_session_favorites")

# ─── Opencode session format constants ─────────────────────────────────────────
# opencode persists sessions in a SQLite database (opencode.db). Message *content*
# does NOT live in the `message` table -- it lives in the separate `part` table, one
# row per content block, joined by message_id. Writing content into message.data
# (as previous versions of this script did) produces sessions that opencode opens as
# completely empty.
#
# The authoritative, importable shape is what `opencode export <sessionID>` emits:
#   { "info": {session fields}, "messages": [ {"info": {...}, "parts": [...] } ] }
# We build that document and hand it to `opencode import`, which validates it.

OC_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
OC_VERSION = "1.18.34"
OC_DEFAULT_PROVIDER = "opencode"
OC_DEFAULT_MODEL = "big-pickle"

# Text that Claude Code injects into the transcript but that is not a real user turn.
# Importing these produces noisy, confusing bubbles at the top of the session.
_CLAUDE_META_PREFIXES = (
    "<local-command-caveat>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<system-reminder>",
    "<user-memory-input>",
    "<ide_selection>",
)


def _oc_id(prefix, length=22):
    """Generate an opencode-style identifier.

    opencode ids are `<prefix>_<12 chars of ms-timestamp in base32><random base32>`,
    which makes them lexicographically sortable by creation time. Matching that shape
    keeps imported sessions ordered correctly alongside native ones.
    """
    ts = int(time.time() * 1000)
    tsb = ""
    t = ts
    for _ in range(12):
        tsb = OC_ID_ALPHABET[t % 32] + tsb
        t //= 32
    rnd = "".join(random.choice(OC_ID_ALPHABET) for _ in range(max(0, length - 12)))
    return f"{prefix}_{tsb}{rnd}"


def _is_meta_text(text):
    """True if this text is a Claude Code harness/system injection, not a real message."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return True
    return stripped.startswith(_CLAUDE_META_PREFIXES)


def _oc_model(claude_model):
    """Map a Claude model string onto an opencode model reference.

    Claude's model ids (claude-sonnet-4-6, etc.) do not exist in opencode, so an
    unmapped id falls back to a model the local install is known to have.
    """
    if isinstance(claude_model, str) and claude_model.strip():
        m = claude_model.strip()
        if "/" in m:
            provider, model = m.split("/", 1)
            return provider, model
    return OC_DEFAULT_PROVIDER, OC_DEFAULT_MODEL


def _oc_project_for_directory(db_path, preferred_dir):
    """Resolve (project_id, directory) for an imported session.

    opencode only lists a session under a project whose directory is registered in
    `project_directory`, so an unregistered directory makes the session invisible in
    the UI. Prefer an exact match, then any registered directory.
    """
    pids, dirs = None, []
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            for pid, directory in cur.execute("SELECT project_id, directory FROM project_directory"):
                dirs.append(directory)
                if preferred_dir and os.path.normpath(directory) == os.path.normpath(preferred_dir):
                    pids = pid
            if pids is None and dirs:
                pids = cur.execute("SELECT id FROM project LIMIT 1").fetchone()[0]
            conn.close()
        except sqlite3.Error:
            pass
    if pids is None:
        pids = "global"
    directory = preferred_dir if (preferred_dir and dirs and
                                  any(os.path.normpath(d) == os.path.normpath(preferred_dir) for d in dirs)) \
        else (dirs[0] if dirs else (preferred_dir or os.getcwd()))
    return pids, directory


def read_claude_jsonl(path):
    """Parse a Claude Code .jsonl transcript into a list of dicts, skipping bad lines."""
    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records


def claude_to_opencode_export(claude_path, db_path=None, directory=None):
    """Build an opencode export-format document (the shape `opencode import` accepts).

    Returns a dict: {"info": {...}, "messages": [{"info": {...}, "parts": [...]}]}
    """
    db_path = db_path or os.path.join(os.path.expanduser("~/.local/share/opencode"), "opencode.db")
    records = read_claude_jsonl(claude_path)

    title = "Imported from Claude Code"
    session_id = _oc_id("ses")
    messages = []
    parent_mid = None
    tokens = {"input": 0, "output": 0, "reasoning": 0,
              "cache": {"read": 0, "write": 0}}
    claude_cwd = None
    session_model = None

    for rec in records:
        rtype = rec.get("type")

        if rtype == "custom-title":
            ct = rec.get("customTitle")
            if isinstance(ct, str) and ct.strip():
                title = ct.strip()
            continue

        if rtype not in ("user", "assistant"):
            continue
        if rec.get("isSidechain"):
            continue

        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue

        # The transcript's cwd tells us which project this conversation belongs to.
        if claude_cwd is None and isinstance(rec.get("cwd"), str):
            claude_cwd = rec["cwd"]

        content = msg.get("content")
        parts = []
        if isinstance(content, str):
            if not _is_meta_text(content):
                parts.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text")
                    if isinstance(text, str) and not _is_meta_text(text):
                        parts.append({"type": "text", "text": text})
                elif btype == "thinking":
                    thinking = block.get("thinking")
                    if isinstance(thinking, str) and thinking.strip():
                        parts.append({"type": "reasoning", "text": thinking})
                elif btype == "tool_use":
                    parts.append({
                        "type": "tool",
                        "tool": block.get("name") or "unknown",
                        "callID": block.get("id") or _oc_id("call"),
                        "state": {
                            "status": "completed",
                            "title": block.get("name") or "unknown",
                            "metadata": {},
                            "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                            "output": "",
                        },
                    })
                elif btype == "tool_result":
                    body = block.get("content")
                    if isinstance(body, list):
                        body = "\n".join(b.get("text", "") for b in body if isinstance(b, dict))
                    elif not isinstance(body, str):
                        body = json.dumps(body)
                    parts.append({
                        "type": "tool",
                        "tool": block.get("__name") or "unknown",
                        "callID": block.get("tool_use_id") or _oc_id("call"),
                        "state": {
                            "status": "completed",
                            "title": block.get("__name") or "unknown",
                            "metadata": {},
                            "input": {},
                            "output": body,
                        },
                    })

        if not parts:
            continue

        mid = _oc_id("msg")
        created = int(time.time() * 1000) + len(messages)
        provider_id, model_id = _oc_model(msg.get("model"))

        if rtype == "user":
            info = {
                "role": "user",
                "time": {"created": created},
                "agent": "build",
                "model": {"providerID": provider_id, "modelID": model_id},
                "summary": {"diffs": []},
            }
        else:
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
            msg_tokens = {
                "input": int(usage.get("input_tokens") or 0),
                "output": int(usage.get("output_tokens") or 0),
                "reasoning": int(details.get("thinking_tokens") or 0),
                "cache": {
                    "read": int(usage.get("cache_read_input_tokens") or 0),
                    "write": int(usage.get("cache_creation_input_tokens") or 0),
                },
            }
            for key in ("input", "output", "reasoning"):
                tokens[key] += msg_tokens[key]
            tokens["cache"]["read"] += msg_tokens["cache"]["read"]
            tokens["cache"]["write"] += msg_tokens["cache"]["write"]
            session_model = session_model or {"providerID": provider_id, "modelID": model_id}
            info = {
                "role": "assistant",
                "mode": "build",
                "agent": "build",
                "cost": 0,
                "tokens": msg_tokens,
                "path": {"cwd": claude_cwd or directory or os.getcwd(),
                         "root": claude_cwd or directory or os.getcwd()},
                "modelID": model_id,
                "providerID": provider_id,
                "time": {"created": created, "completed": created},
            }
            if parent_mid:
                info["parentID"] = parent_mid

        info["id"] = mid
        info["sessionID"] = session_id

        for part in parts:
            part["id"] = _oc_id("prt")
            part["messageID"] = mid
            part["sessionID"] = session_id
            if part["type"] in ("text", "reasoning"):
                part["time"] = {"start": created, "end": created}
            elif part["type"] == "tool":
                part["state"]["time"] = {"start": created, "end": created}

        messages.append({"info": info, "parts": parts})
        parent_mid = mid

    if not messages:
        return None

    # opencode brackets assistant turns with step-start / step-finish parts.
    wrapped = []
    for message in messages:
        info, parts = message["info"], message["parts"]
        if info["role"] == "assistant":
            has_tool = any(p["type"] == "tool" for p in parts)
            wrapped.append({
                "info": info,
                "parts": [{
                    "type": "step-start",
                    "snapshot": "",
                    "id": _oc_id("prt"),
                    "sessionID": info["sessionID"],
                    "messageID": info["id"],
                }] + parts + [{
                    "type": "step-finish",
                    "reason": "tool-calls" if has_tool else "stop",
                    "snapshot": "",
                    "tokens": info["tokens"],
                    "cost": 0,
                    "id": _oc_id("prt"),
                    "sessionID": info["sessionID"],
                    "messageID": info["id"],
                }],
            })
        else:
            wrapped.append({"info": info, "parts": parts})

    project_id, resolved_dir = _oc_project_for_directory(db_path, directory or claude_cwd)
    now = int(time.time() * 1000)

    return {
        "info": {
            "id": session_id,
            "slug": "claude-import-" + hashlib.md5(session_id.encode()).hexdigest()[:8],
            "projectID": project_id,
            "directory": resolved_dir,
            "path": "",
            "title": title,
            "agent": "build",
            "model": {"id": OC_DEFAULT_MODEL, "providerID": OC_DEFAULT_PROVIDER},
            "version": OC_VERSION,
            "summary": {"additions": 0, "deletions": 0, "files": 0},
            "cost": 0,
            "tokens": tokens,
            "time": {"created": now, "updated": now},
        },
        "messages": wrapped,
    }


def opencode_export_to_claude_jsonl(doc, claude_session_id):
    """Convert an opencode export document back into a Claude Code .jsonl transcript."""
    info = doc.get("info", {}) or {}
    lines = []
    lines.append(json.dumps({
        "type": "custom-title",
        "customTitle": info.get("title") or "Synced from opencode",
        "sessionId": claude_session_id,
    }))
    directory = info.get("directory") or os.getcwd()

    previous_uuid = None
    for message in doc.get("messages", []) or []:
        minfo = message.get("info", {}) or {}
        role = minfo.get("role")
        if role not in ("user", "assistant"):
            continue

        blocks = []
        for part in message.get("parts", []) or []:
            ptype = part.get("type")
            if ptype == "text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    blocks.append({"type": "text", "text": text})
            elif ptype == "reasoning":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    blocks.append({"type": "thinking", "thinking": text})
            elif ptype == "tool":
                state = part.get("state") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": part.get("callID") or _oc_id("call"),
                    "name": part.get("tool") or "unknown",
                    "input": state.get("input") if isinstance(state.get("input"), dict) else {},
                })
                output = state.get("output")
                if isinstance(output, str) and output.strip():
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": part.get("callID") or "",
                        "content": output,
                    })
        if not blocks:
            continue

        uuid = minfo.get("id") or _oc_id("uuid")
        created = (minfo.get("time") or {}).get("created")
        message_obj = {
            "id": uuid,
            "type": "message",
            "role": role,
            "content": blocks,
            "model": f"{minfo.get('providerID', 'opencode')}/{minfo.get('modelID', OC_DEFAULT_MODEL)}",
        }
        if role == "assistant":
            message_obj["usage"] = {
                "input_tokens": (minfo.get("tokens") or {}).get("input", 0),
                "output_tokens": (minfo.get("tokens") or {}).get("output", 0),
                "cache_read_input_tokens": ((minfo.get("tokens") or {}).get("cache") or {}).get("read", 0),
                "cache_creation_input_tokens": ((minfo.get("tokens") or {}).get("cache") or {}).get("write", 0),
            }
            message_obj["stop_reason"] = "end_turn"

        lines.append(json.dumps({
            "parentUuid": previous_uuid,
            "isSidechain": False,
            "type": role,
            "message": message_obj,
            "uuid": uuid,
            "timestamp": datetime.utcfromtimestamp(created / 1000).isoformat() + "Z" if created else None,
            "userType": "external",
            "cwd": directory,
            "sessionId": claude_session_id,
            "version": info.get("version") or OC_VERSION,
        }))
        previous_uuid = uuid

    return "\n".join(lines) + "\n"

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
                                            if isinstance(msg, dict) and "content" in msg and isinstance(msg["content"], str) and not _is_meta_text(msg["content"]):
                                                content = msg["content"]
                                                title = content[:50] + ("..." if len(content) > 50 else "")
                                                break
                                            elif isinstance(msg, str) and not _is_meta_text(msg):
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
        # opencode's sessions live entirely in the SQLite database. There is no
        # per-session file on disk: `storage/session_diff/` holds snapshot diffs and
        # `state/opencode/prompt-history.jsonl` is raw prompt history -- neither is a
        # chat session, so neither is listed here.
        db_path = os.path.join(base_dir, "opencode.db")
        if not os.path.exists(db_path):
            return sessions

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            print(f"\u26a0\ufe0f  Could not open opencode database: {e}")
            return sessions

        try:
            rows = conn.execute(
                "SELECT s.id, s.title, s.directory, s.time_created, s.time_updated,"
                " (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) AS msg_count,"
                " (SELECT COUNT(*) FROM part p WHERE p.session_id = s.id) AS part_count"
                " FROM session s WHERE s.parent_id IS NULL"
                " ORDER BY s.time_updated DESC"
            ).fetchall()
        except sqlite3.Error as e:
            print(f"\u26a0\ufe0f  Could not query opencode sessions: {e}")
            conn.close()
            return sessions

        for row in rows:
            # A session with no parts renders as an empty conversation. Several of
            # those exist from older broken imports; keep them out of the list.
            if not row["part_count"]:
                continue
            sessions.append({
                "id": row["id"],
                "path": db_path,
                "db_path": db_path,
                "modified": (row["time_updated"] or row["time_created"] or 0) / 1000.0,
                "title": row["title"] or f"Session {row['id'][:12]}",
                "msg_count": row["msg_count"],
                "part_count": row["part_count"],
            })

        conn.close()
        return sessions

    return sessions

def sync_claude_to_opencode(claude_session, opencode_base_dir):
    """Import a Claude Code session into opencode.

    Builds an opencode export-format document and hands it to `opencode import`,
    which writes the `session` / `message` / `part` rows itself. Returns the new
    opencode session id, or None on failure.
    """
    db_path = os.path.join(opencode_base_dir, "opencode.db")

    doc = claude_to_opencode_export(claude_session['path'], db_path=db_path)
    if not doc:
        print("\u26a0\ufe0f  No convertible user/assistant messages found in that Claude session.")
        return None

    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    before_ids = _session_ids(db_path)
    started_at_ms = int(time.time() * 1000) - 1000
    try:
        json.dump(doc, tmp)
        tmp.close()

        result = subprocess.run(
            ["opencode", "import", tmp.name],
            capture_output=True, text=True, timeout=900,
            # opencode import re-resolves the session's project and directory from its
            # own working directory, ignoring the values in the document. Run it from
            # the target project directory so the session lands in the right project
            # instead of a synthetic "global" one.
            cwd=doc["info"].get("directory") or None,
        )
    except FileNotFoundError:
        print("\u274c  `opencode` executable not found on PATH; cannot import session.")
        _unlink(tmp.name)
        return None
    except subprocess.TimeoutExpired:
        print("\u274c  `opencode import` timed out.")
        _unlink(tmp.name)
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else "unknown error"
        print(f"❌  opencode import failed: {tail}")
        strays = _cleanup_partial_import(db_path, before_ids, started_at_ms)
        if strays:
            print(f"   Removed partial session stub(s): {', '.join(strays)}")
        return None

    # opencode prints: Imported session: ses_...
    new_id = None
    for line in (result.stdout or "").splitlines():
        if "ses_" in line:
            new_id = line.strip().split("ses_", 1)[1]
            new_id = "ses_" + new_id.split()[0].strip()
            break
    return new_id


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _session_ids(db_path):
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        ids = {row[0] for row in conn.execute("SELECT id FROM session")}
        conn.close()
        return ids
    except sqlite3.Error:
        return set()


def _cleanup_partial_import(db_path, before_ids, started_at_ms):
    """Remove the stub session left behind by a failed `opencode import`.

    opencode import is not atomic: it inserts the session row before validating the
    messages, so a schema mismatch leaves a half-populated session that shows up in the
    UI as a near-empty conversation. Only rows created after this import started and not
    present beforehand are touched.
    """
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        strays = [row[0] for row in cur.execute(
            "SELECT id FROM session WHERE time_created >= ?", (started_at_ms,)
        ) if row[0] not in before_ids]
        if strays:
            cur.executemany("DELETE FROM session WHERE id = ?", [(s,) for s in strays])
            conn.commit()
        conn.close()
        return strays
    except sqlite3.Error:
        return []


def opencode_session_to_export(opencode_session):
    """Read an opencode session out of the database as an export-format document."""
    db_path = opencode_session.get("db_path")
    session_id = opencode_session["id"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, project_id, slug, directory, path, title, agent, model, version,"
            " summary_additions, summary_deletions, summary_files, cost, tokens_input,"
            " tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write,"
            " time_created, time_updated FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        info = {
            "id": row["id"],
            "slug": row["slug"],
            "projectID": row["project_id"],
            "directory": row["directory"],
            "path": row["path"] or "",
            "title": row["title"],
            "agent": row["agent"] or "build",
            "model": json.loads(row["model"]) if row["model"] else {"id": OC_DEFAULT_MODEL, "providerID": OC_DEFAULT_PROVIDER},
            "version": row["version"] or OC_VERSION,
            "summary": {
                "additions": row["summary_additions"] or 0,
                "deletions": row["summary_deletions"] or 0,
                "files": row["summary_files"] or 0,
            },
            "cost": row["cost"] or 0,
            "tokens": {
                "input": row["tokens_input"] or 0,
                "output": row["tokens_output"] or 0,
                "reasoning": row["tokens_reasoning"] or 0,
                "cache": {"read": row["tokens_cache_read"] or 0, "write": row["tokens_cache_write"] or 0},
            },
            "time": {"created": row["time_created"], "updated": row["time_updated"]},
        }

        messages = []
        msg_rows = conn.execute(
            "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created, id",
            (session_id,),
        ).fetchall()
        for mrow in msg_rows:
            try:
                minfo = json.loads(mrow["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            minfo["id"] = mrow["id"]
            minfo["sessionID"] = session_id
            parts = []
            for prow in conn.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created, id",
                (mrow["id"],),
            ):
                try:
                    part = json.loads(prow["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                part["messageID"] = mrow["id"]
                part["sessionID"] = session_id
                parts.append(part)
            messages.append({"info": minfo, "parts": parts})
    finally:
        conn.close()

    return {"info": info, "messages": messages}


def sync_opencode_to_claude(opencode_session, claude_base_dir):
    """Export an opencode session and write it as a Claude Code .jsonl transcript.

    Claude Code expects `<projects>/<slugified-cwd>/<session-id>.jsonl`, so the file is
    written to a directory named after the session's project directory rather than to a
    flat `sync_*` folder (which Claude Code does not read).
    """
    session_id = opencode_session["id"]
    doc = opencode_session_to_export(opencode_session)
    if not doc:
        print(f"\u274c  Could not read opencode session {session_id} from the database.")
        return None

    directory = doc["info"].get("directory") or os.getcwd()
    # Claude Code names each project directory after the absolute path with every slash
    # replaced by a dash: /workspace -> -workspace, /srv/app -> -srv-app.
    slug_dir = directory.strip().replace(os.sep, "-").replace("/", "-") or "-global"
    new_dir = os.path.join(claude_base_dir, slug_dir)
    try:
        os.makedirs(new_dir, exist_ok=True)
        new_file = os.path.join(new_dir, f"{session_id}.jsonl")
        with open(new_file, "w", encoding="utf-8") as f:
            f.write(opencode_export_to_claude_jsonl(doc, session_id))
        return new_file
    except OSError as e:
        print(f"\u26a0\ufe0f  Failed to write Claude session: {e}")
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
    def _(_):
        if current[0] > 0:
            current[0] -= 1

    @kb.add("down")
    def _(_):
        if current[0] < len(sessions) - 1:
            current[0] += 1

    @kb.add("pageup")
    def _(_):
        page = min(visible_count - 1, len(sessions))
        current[0] = max(0, current[0] - page)

    @kb.add("pagedown")
    def _(_):
        page = min(visible_count - 1, len(sessions))
        current[0] = min(len(sessions) - 1, current[0] + page)

    @kb.add("home")
    def _(_):
        current[0] = 0

    @kb.add("end")
    def _(_):
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
    except Exception as _:
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

    # Restore ~/.claude -> .claude_persist so this script sees the same Claude CLI
    # state as the provider scripts. On a recreated devcontainer the symlink is gone
    # even though the persisted sessions are still on disk.
    if not args.claude_location:
        try:
            setup_claude_persistence()
        except Exception as e:
            print(f"⚠️  Could not restore Claude persistence: {e}")

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
                                print(f"\n✅ Imported into opencode as session: {new_path}")
                                print("   Restart opencode (or switch sessions) to see it.")
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
                                print(f"\n✅ Exported opencode session to Claude: {new_path}")
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