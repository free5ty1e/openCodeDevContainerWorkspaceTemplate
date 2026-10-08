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
import uuid
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
# Pi stores one JSONL file per session under ~/.pi/agent/sessions/<encoded-cwd>/
# (the cwd is encoded as --path-with-dashes--). Pi has no database.
PI_SESSIONS_CANDIDATES = [
    "~/.pi/agent/sessions",
    "~/.pi/sessions",
    ".pi/agent/sessions",
]
FAVORITES_CACHE_FILE = os.path.expanduser("~/.claude_opencode_session_favorites")

# ─── Harness registry ──────────────────────────────────────────────────────────
# The session lists the UI cycles through with Left/Right, and the copy targets the
# user picks from after selecting a session. Order is the on-screen order.
HARNESSES = ("claude", "opencode", "pi")
HARNESS_LABELS = {
    "claude": "Claude Code",
    "opencode": "opencode",
    "pi": "Pi",
}


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


# Namespace for deterministic UUIDs: the same source id always maps to the same
# destination id, so re-syncing a session updates its counterpart in place instead
# of piling up duplicate transcripts. Claude and Pi both key sessions by UUID.
_STABLE_UUID_NS = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/claude-opencode-session-sync")


def _stable_uuid(kind, *parts):
    """Deterministic UUID v5 for a source identifier.

    Claude's `--resume` accepts only a UUID or an exact session title, and Pi keys
    session files by UUID, so importing under a foreign id (`ses_...`) produces a
    session that can never be resumed. Deriving the id keeps re-syncs idempotent.
    """
    return str(uuid.uuid5(_STABLE_UUID_NS, ":".join([kind] + [str(p) for p in parts])))


def _stable_uuid_or(kind, value):
    """Preserve values that already are UUIDs, derive one otherwise."""
    if isinstance(value, str):
        try:
            uuid.UUID(value)
            return value
        except ValueError:
            pass
    return _stable_uuid(kind, value)


def _is_meta_text(text):
    """True if this text is a Claude Code harness/system injection, not a real message."""
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped:
        return True
    return stripped.startswith(_CLAUDE_META_PREFIXES)


# Operator reads these stamps in Eastern time regardless of the container's TZ.
IMPORT_TZ_NAME = "America/New_York"
_TITLE_MAX = 50


def _import_stamp(when=None):
    """Render an import stamp like 'imported 20261006-0313pmEastern'."""
    import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(IMPORT_TZ_NAME)
        now = (when or _dt.datetime.now(tz)).astimezone(tz)
    except Exception:
        now = _dt.datetime.now(_dt.timezone.utc)
    return "imported " + now.strftime("%Y%m%d-%I%M%p").lower() + "Eastern"


def _stamped_title(title, when=None):
    """Append the import stamp so a synced session is distinguishable from the original.

    Idempotent: re-syncing an already-synced session must not stack stamps.
    """
    base = (title or "").strip() or "Untitled session"
    if "(imported " in base:
        return base
    if len(base) > _TITLE_MAX:
        base = base[:_TITLE_MAX].rstrip() + "..."
    return f"{base} ({_import_stamp(when)})"


def _derive_claude_title(records, session_id):
    """Derive a display title for a Claude session from its transcript records.

    Priority matches what the session picker shows: an explicit `custom-title`
    record wins, otherwise fall back to the most recent real user message.

    User messages store content either as a plain string or as a list of blocks;
    both shapes are handled (the picker previously only handled plain strings,
    which is why fresh conversations fell back to a generated id).
    """
    fallback = f"Session {session_id}"
    for rec in records:
        if isinstance(rec, dict) and rec.get("type") == "custom-title":
            ct = rec.get("customTitle")
            if isinstance(ct, str) and ct.strip():
                return ct.strip()
    for rec in reversed(records):
        if not isinstance(rec, dict) or rec.get("type") != "user":
            continue
        if rec.get("isSidechain"):
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and not _is_meta_text(content):
            return content.strip()
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text")
                    if isinstance(t, str) and not _is_meta_text(t):
                        return t.strip()
    return fallback


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

    # Title comes from the source transcript so the imported session reads the same
    # as the original in the picker, then carries an import stamp so the copy can be
    # told apart from the live session.
    src_id = os.path.splitext(os.path.basename(claude_path))[0]
    title = _stamped_title(_derive_claude_title(records, src_id))
    session_id = _oc_id("ses")
    messages = []
    parent_mid = None
    tokens = {"input": 0, "output": 0, "reasoning": 0,
              "cache": {"read": 0, "write": 0}}
    claude_cwd = None
    session_model = None

    for rec in records:
        rtype = rec.get("type")

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
        "customTitle": _stamped_title(info.get("title") or "Synced from opencode"),
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

        msg_uuid = _stable_uuid_or("msg", minfo.get("id") or _oc_id("uuid"))
        created = (minfo.get("time") or {}).get("created")
        message_obj = {
            "id": msg_uuid,
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
            "uuid": msg_uuid,
            "timestamp": datetime.utcfromtimestamp(created / 1000).isoformat() + "Z" if created else None,
            "userType": "external",
            "cwd": directory,
            "sessionId": claude_session_id,
            "version": info.get("version") or OC_VERSION,
        }))
        previous_uuid = msg_uuid

    return "\n".join(lines) + "\n"


# ─── Pi (earendil-works/pi) session format ─────────────────────────────────────
# One JSONL file per session under ~/.pi/agent/sessions/--<cwd-with-dashes>--/,
# named <ISO-timestamp-with-dashes>_<uuid>.jsonl. Each line is an entry carrying
# type/id/parentId/timestamp, linked into a tree; the last entry in the file is the
# active leaf. The first line is a `session` header (metadata only, no id/parentId).
# There is no database. Reference:
# https://github.com/earendil-works/pi packages/coding-agent/docs/session-format.md

def _pi_cwd_slug(directory):
    """Encode a cwd the way Pi names its per-project session directory."""
    resolved = (directory or os.getcwd()).strip()
    safe = resolved.lstrip("/\\").replace("/", "-").replace("\\", "-").replace(":", "-")
    return f"--{safe}--"


def _pi_file_timestamp(ms=None):
    """Filename timestamp: ISO 8601 with ':' and '.' replaced by '-'."""
    import datetime as _dt
    dt = _dt.datetime.fromtimestamp((ms if ms is not None else time.time() * 1000) / 1000,
                                    _dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H-%M-%S-") + f"{dt.microsecond // 1000:03d}Z"


def _pi_iso(ms=None):
    """Entry timestamp: ISO 8601 with milliseconds, as Pi writes it."""
    import datetime as _dt
    dt = _dt.datetime.fromtimestamp((ms if ms is not None else time.time() * 1000) / 1000,
                                    _dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _pi_entry_id(used):
    """8 hex-char entry id, matching Pi's generateId()."""
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in used:
            used.add(candidate)
            return candidate


def _text_of_content(content):
    """Flatten a string-or-block-list content value to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(block.get("text") or "")
            elif isinstance(block, str):
                out.append(block)
        return "".join(out)
    return ""


def _export_message(role, parts, created, cwd, provider=None, model=None, tokens=None):
    """Build one pivot-format message wrapper."""
    provider = provider or OC_DEFAULT_PROVIDER
    model = model or OC_DEFAULT_MODEL
    if role == "user":
        info = {
            "role": "user",
            "time": {"created": created},
            "agent": "build",
            "model": {"providerID": provider, "modelID": model},
            "summary": {"diffs": []},
        }
    else:
        info = {
            "role": "assistant",
            "mode": "build",
            "agent": "build",
            "cost": 0,
            "tokens": tokens or {"input": 0, "output": 0, "reasoning": 0,
                                 "cache": {"read": 0, "write": 0}},
            "path": {"cwd": cwd, "root": cwd},
            "modelID": model,
            "providerID": provider,
            "time": {"created": created},
        }
    return {"info": info, "parts": parts}


def _finalize_export_doc(doc):
    """Fill in the ids/times opencode's importer requires and bracket assistant turns.

    `opencode import` rejects messages/parts without an `id`, and expects assistant
    turns to be wrapped in step-start/step-finish parts. Converters that build the
    pivot doc by hand (rather than reading a real `opencode export`) call this last.
    """
    session_id = doc["info"]["id"]
    default_tokens = {"input": 0, "output": 0, "reasoning": 0,
                      "cache": {"read": 0, "write": 0}}
    padded = []
    prev_mid = None
    for message in doc.get("messages", []) or []:
        info = message.get("info", {}) or {}
        parts = message.get("parts") or []
        created = (info.get("time") or {}).get("created")
        if not isinstance(created, int):
            created = int(time.time() * 1000)
            info.setdefault("time", {})["created"] = created
        mid = info.setdefault("id", _oc_id("msg"))
        info.setdefault("sessionID", session_id)
        # opencode expects assistant turns to reference the turn they follow.
        if info.get("role") == "assistant" and prev_mid and "parentID" not in info:
            info["parentID"] = prev_mid
        for part in parts:
            part.setdefault("id", _oc_id("prt"))
            part.setdefault("messageID", mid)
            part.setdefault("sessionID", session_id)
            if part.get("type") in ("text", "reasoning"):
                part.setdefault("time", {"start": created, "end": created})
            elif part.get("type") == "tool":
                part.setdefault("state", {}).setdefault(
                    "time", {"start": created, "end": created})
        if info.get("role") == "assistant":
            has_tool = any(p.get("type") == "tool" for p in parts)
            padded.append({
                "info": info,
                "parts": [{
                    "type": "step-start", "snapshot": "",
                    "id": _oc_id("prt"), "sessionID": session_id, "messageID": mid,
                }] + parts + [{
                    "type": "step-finish",
                    "reason": "tool-calls" if has_tool else "stop",
                    "snapshot": "", "tokens": info.get("tokens") or default_tokens,
                    "cost": 0, "id": _oc_id("prt"),
                    "sessionID": session_id, "messageID": mid,
                }],
            })
        else:
            padded.append({"info": info, "parts": parts})
        prev_mid = mid
    doc["messages"] = padded
    return doc


def opencode_export_to_pi_jsonl(doc, pi_id, directory=None):
    """Convert the pivot export document into a Pi session JSONL transcript."""
    info = doc.get("info", {}) or {}
    directory = directory or info.get("directory") or os.getcwd()
    title = _stamped_title(info.get("title") or "Synced from opencode")
    created = (info.get("time") or {}).get("created")
    if not isinstance(created, int):
        created = int(time.time() * 1000)
    stamp = _pi_iso(created)

    # Resolve every tool output first: opencode-native sessions carry input and
    # output on one part, while Claude-origin sessions split them across a
    # tool_use part and a later tool_result part sharing a callID.
    tool_output = {}
    for message in doc.get("messages", []) or []:
        for part in message.get("parts", []) or []:
            if part.get("type") != "tool":
                continue
            state = part.get("state") or {}
            out = state.get("output")
            if isinstance(out, str) and out.strip():
                tool_output[part.get("callID")] = (out, state.get("status") == "error")

    used = set()
    lines = [{
        "type": "session", "version": 3, "id": pi_id,
        "timestamp": stamp, "cwd": directory,
    }]
    # Display name goes first so the resume picker shows the title, and so it can
    # never end up as the tree leaf (the leaf must be a conversation entry).
    info_id = _pi_entry_id(used)
    lines.append({
        "type": "session_info", "id": info_id, "parentId": None,
        "timestamp": stamp, "name": title,
    })
    prev_id = info_id

    for message in doc.get("messages", []) or []:
        minfo = message.get("info", {}) or {}
        role = minfo.get("role")
        parts = message.get("parts") or []
        ts = (minfo.get("time") or {}).get("created")
        if not isinstance(ts, int):
            ts = created
        if role == "user":
            text = "\n".join(p.get("text", "") for p in parts
                             if p.get("type") == "text" and p.get("text"))
            if not text.strip():
                continue
            eid = _pi_entry_id(used)
            lines.append({
                "type": "message", "id": eid, "parentId": prev_id,
                "timestamp": _pi_iso(ts),
                "message": {"role": "user", "content": text, "timestamp": ts},
            })
            prev_id = eid
        elif role == "assistant":
            blocks = []
            calls = []
            for part in parts:
                ptype = part.get("type")
                if ptype == "text" and (part.get("text") or "").strip():
                    blocks.append({"type": "text", "text": part["text"]})
                elif ptype == "reasoning" and (part.get("text") or "").strip():
                    blocks.append({"type": "thinking", "thinking": part["text"]})
                elif ptype == "tool":
                    state = part.get("state") or {}
                    call_id = part.get("callID") or _oc_id("call")
                    blocks.append({
                        "type": "toolCall", "id": call_id,
                        "name": part.get("tool") or "unknown",
                        "arguments": state.get("input") if isinstance(state.get("input"), dict) else {},
                    })
                    calls.append((call_id, part.get("tool") or "unknown"))
            if not blocks:
                continue
            tokens = minfo.get("tokens") or {}
            cache = tokens.get("cache") or {}
            input_tokens = int(tokens.get("input") or 0)
            output_tokens = int(tokens.get("output") or 0)
            usage = {
                "input": input_tokens,
                "output": output_tokens,
                "cacheRead": int(cache.get("read") or 0),
                "cacheWrite": int(cache.get("write") or 0),
                "reasoning": int(tokens.get("reasoning") or 0),
                "totalTokens": input_tokens + output_tokens,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
            }
            eid = _pi_entry_id(used)
            lines.append({
                "type": "message", "id": eid, "parentId": prev_id,
                "timestamp": _pi_iso(ts),
                "message": {
                    "role": "assistant", "content": blocks, "api": "anthropic-messages",
                    "provider": minfo.get("providerID") or OC_DEFAULT_PROVIDER,
                    "model": minfo.get("modelID") or OC_DEFAULT_MODEL,
                    "usage": usage, "stopReason": "toolUse" if calls else "stop",
                    "timestamp": ts,
                },
            })
            prev_id = eid
            # Pi models each tool result as its own entry following the assistant turn.
            for call_id, name in calls:
                out, is_error = tool_output.get(call_id, ("", False))
                if not out:
                    continue
                rid = _pi_entry_id(used)
                lines.append({
                    "type": "message", "id": rid, "parentId": prev_id,
                    "timestamp": _pi_iso(ts),
                    "message": {
                        "role": "toolResult", "toolCallId": call_id, "toolName": name,
                        "content": [{"type": "text", "text": out}],
                        "isError": bool(is_error), "timestamp": ts,
                    },
                })
                prev_id = rid

    return "\n".join(json.dumps(line) for line in lines) + "\n"


def _derive_pi_title(records, session_id):
    """Pi's display name (`session_info`) wins; otherwise the first real user message."""
    for rec in records:
        if rec.get("type") == "session_info":
            name = rec.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    for rec in records:
        if rec.get("type") != "message":
            continue
        msg = rec.get("message") or {}
        if msg.get("role") != "user":
            continue
        text = _text_of_content(msg.get("content"))
        if text.strip():
            return text.strip()
    return f"Session {session_id}"


def pi_records_to_opencode_export(records, pi_id, directory=None):
    """Parse a Pi session transcript into the pivot export document."""
    header = next((r for r in records if r.get("type") == "session"), {})
    cwd = header.get("cwd") or directory or os.getcwd()
    title = _stamped_title(_derive_pi_title(records, pi_id))
    messages = []
    parts_by_call = {}
    tokens = {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    session_model = None

    for rec in records:
        if rec.get("type") != "message":
            continue
        msg = rec.get("message") or {}
        role = msg.get("role")
        ts = msg.get("timestamp")
        if not isinstance(ts, int):
            ts = int(time.time() * 1000)
        if role == "user":
            text = _text_of_content(msg.get("content"))
            if not text.strip():
                continue
            messages.append(_export_message(
                "user", [{"type": "text", "text": text}], ts, cwd))
        elif role == "assistant":
            parts = []
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text" and (block.get("text") or "").strip():
                    parts.append({"type": "text", "text": block["text"]})
                elif btype == "thinking" and (block.get("thinking") or "").strip():
                    parts.append({"type": "reasoning", "text": block["thinking"]})
                elif btype == "toolCall":
                    call_id = block.get("id") or _oc_id("call")
                    part = {
                        "type": "tool", "tool": block.get("name") or "unknown",
                        "callID": call_id,
                        "state": {
                            "status": "completed",
                            "title": block.get("name") or "unknown",
                            "metadata": {},
                            "input": block.get("arguments") if isinstance(block.get("arguments"), dict) else {},
                            "output": "",
                        },
                    }
                    parts.append(part)
                    parts_by_call[call_id] = part
            if not parts:
                continue
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            msg_tokens = {
                "input": int(usage.get("input") or 0),
                "output": int(usage.get("output") or 0),
                "reasoning": int(usage.get("reasoning") or 0),
                "cache": {
                    "read": int(usage.get("cacheRead") or 0),
                    "write": int(usage.get("cacheWrite") or 0),
                },
            }
            for key in ("input", "output", "reasoning"):
                tokens[key] += msg_tokens[key]
            tokens["cache"]["read"] += msg_tokens["cache"]["read"]
            tokens["cache"]["write"] += msg_tokens["cache"]["write"]
            provider = msg.get("provider") or OC_DEFAULT_PROVIDER
            model = msg.get("model") or OC_DEFAULT_MODEL
            session_model = session_model or {"providerID": provider, "modelID": model}
            messages.append(_export_message(
                "assistant", parts, ts, cwd, provider, model, msg_tokens))
        elif role == "toolResult":
            call_id = msg.get("toolCallId")
            output = _text_of_content(msg.get("content"))
            part = parts_by_call.get(call_id)
            if part is not None:
                # Attach the output to the tool call it belongs to.
                part["state"]["output"] = output
                part["state"]["status"] = "error" if msg.get("isError") else "completed"
            elif output.strip():
                part = {
                    "type": "tool", "tool": msg.get("toolName") or "unknown",
                    "callID": call_id or _oc_id("call"),
                    "state": {
                        "status": "error" if msg.get("isError") else "completed",
                        "title": msg.get("toolName") or "unknown",
                        "metadata": {}, "input": {}, "output": output,
                    },
                }
                messages.append(_export_message("user", [part], ts, cwd))

    now = int(time.time() * 1000)
    model = session_model or {"providerID": OC_DEFAULT_PROVIDER, "modelID": OC_DEFAULT_MODEL}
    return _finalize_export_doc({
        "info": {
            "id": _oc_id("ses"),
            "slug": "pi-import-" + hashlib.md5(pi_id.encode()).hexdigest()[:8],
            "projectID": "global",
            "directory": cwd,
            "path": "",
            "title": title,
            "agent": "build",
            "model": {"id": model["modelID"], "providerID": model["providerID"]},
            "version": OC_VERSION,
            "summary": {"additions": 0, "deletions": 0, "files": 0},
            "cost": 0,
            "tokens": tokens,
            "time": {"created": now, "updated": now},
        },
        "messages": messages,
    })


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
                # Same derivation the exporter uses, so a session's title reads
                # identically before and after syncing.
                try:
                    title = _derive_claude_title(
                        read_claude_jsonl(chat_file), session_id)
                except Exception:
                    title = f"Session {session_id}"
                if len(title) > _TITLE_MAX:
                    title = title[:_TITLE_MAX].rstrip() + "..."
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

    elif session_type == 'pi':
        # Pi layout mirrors Claude's: base dir holds one subdirectory per project
        # (--<cwd-with-dashes>--) containing one .jsonl per session.
        for item in os.listdir(base_dir):
            item_path = os.path.join(base_dir, item)
            if not os.path.isdir(item_path):
                continue
            for fname in os.listdir(item_path):
                if not fname.endswith(".jsonl"):
                    continue
                session_file = os.path.join(item_path, fname)
                if not os.path.isfile(session_file):
                    continue
                # Filename is <ISO-timestamp>_<session-id>.jsonl; the id is a UUID.
                session_id = fname[:-6].split("_", 1)[-1]
                records = read_claude_jsonl(session_file)
                modified = os.path.getmtime(session_file)
                if not any(r.get("type") == "session" for r in records):
                    # Not a Pi session file (or truncated); skip it.
                    continue
                title = _derive_pi_title(records, session_id)
                if len(title) > _TITLE_MAX:
                    title = title[:_TITLE_MAX].rstrip() + "..."
                sessions.append({
                    "id": session_id,
                    "path": session_file,
                    "modified": modified,
                    "title": title,
                })
        sessions.sort(key=lambda x: x["modified"], reverse=True)
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

    return _import_export_into_opencode(doc, opencode_base_dir)


def _import_export_into_opencode(doc, opencode_base_dir):
    """Hand a pivot export document to `opencode import`, returning the new session id.

    Shared by every source harness; `opencode import` does the actual row writes.
    """
    db_path = os.path.join(opencode_base_dir, "opencode.db")

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


def _write_export_to_claude(doc, claude_base_dir, claude_id):
    """Write a pivot export document as a Claude transcript. Returns the file path."""
    directory = doc["info"].get("directory") or os.getcwd()
    # Claude Code names each project directory after the absolute path with every slash
    # replaced by a dash: /workspace -> -workspace, /srv/app -> -srv-app.
    slug_dir = directory.strip().replace(os.sep, "-").replace("/", "-") or "-global"
    new_dir = os.path.join(claude_base_dir, slug_dir)
    try:
        os.makedirs(new_dir, exist_ok=True)
        new_file = os.path.join(new_dir, f"{claude_id}.jsonl")
        with open(new_file, "w", encoding="utf-8") as f:
            f.write(opencode_export_to_claude_jsonl(doc, claude_id))
        return new_file
    except OSError as e:
        print(f"\u26a0\ufe0f  Failed to write Claude session: {e}")
        return None


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

    # Claude keys sessions by UUID; derive one from the opencode id so the same
    # source session always maps to the same transcript file.
    return _write_export_to_claude(doc, claude_base_dir, _stable_uuid("claude", session_id))


def _pi_dest_file(pi_base_dir, directory, pi_id):
    """Resolve the Pi session file for `pi_id`, reusing an existing file on re-sync."""
    import glob
    target_dir = os.path.join(pi_base_dir, _pi_cwd_slug(directory))
    os.makedirs(target_dir, exist_ok=True)
    existing = glob.glob(os.path.join(target_dir, f"*_{pi_id}.jsonl"))
    if existing:
        return existing[0]
    return os.path.join(target_dir, f"{_pi_file_timestamp()}_{pi_id}.jsonl")


def _write_export_to_pi(doc, pi_base_dir, pi_id):
    """Write a pivot export document as a Pi session file. Returns the file path."""
    directory = doc["info"].get("directory") or os.getcwd()
    try:
        new_file = _pi_dest_file(pi_base_dir, directory, pi_id)
        with open(new_file, "w", encoding="utf-8") as f:
            f.write(opencode_export_to_pi_jsonl(doc, pi_id, directory))
        return new_file
    except OSError as e:
        print(f"\u26a0\ufe0f  Failed to write Pi session: {e}")
        return None


def sync_claude_to_pi(claude_session, pi_base_dir):
    """Import a Claude Code session into Pi."""
    doc = claude_to_opencode_export(claude_session["path"])
    if not doc:
        print("\u26a0\ufe0f  No convertible messages found in that Claude session.")
        return None
    return _write_export_to_pi(doc, pi_base_dir, _stable_uuid("pi", "claude", claude_session["id"]))


def sync_opencode_to_pi(opencode_session, pi_base_dir):
    """Import an opencode session into Pi."""
    doc = opencode_session_to_export(opencode_session)
    if not doc:
        print(f"\u274c  Could not read opencode session {opencode_session['id']} from the database.")
        return None
    return _write_export_to_pi(doc, pi_base_dir, _stable_uuid("pi", "opencode", opencode_session["id"]))


def sync_pi_to_claude(pi_session, claude_base_dir):
    """Export a Pi session and write it as a Claude Code .jsonl transcript."""
    records = read_claude_jsonl(pi_session["path"])
    doc = pi_records_to_opencode_export(records, pi_session["id"])
    return _write_export_to_claude(doc, claude_base_dir,
                                   _stable_uuid("claude", "pi", pi_session["id"]))


def sync_pi_to_opencode(pi_session, opencode_base_dir):
    """Import a Pi session into opencode."""
    records = read_claude_jsonl(pi_session["path"])
    doc = pi_records_to_opencode_export(records, pi_session["id"])
    if not doc["messages"]:
        print("\u26a0\ufe0f  No convertible messages found in that Pi session.")
        return None
    return _import_export_into_opencode(doc, opencode_base_dir)


def sync_session(source_type, session, target_type, bases):
    """Dispatch a copy between any two harnesses.

    `bases` maps harness key -> base directory. Returns the new id/path, or None.
    """
    if source_type == target_type:
        return None
    if not bases.get(target_type):
        print(f"\u274c  {HARNESS_LABELS[target_type]} storage location is not set.")
        return None
    dispatch = {
        ("claude", "opencode"): lambda: sync_claude_to_opencode(session, bases["opencode"]),
        ("claude", "pi"): lambda: sync_claude_to_pi(session, bases["pi"]),
        ("opencode", "claude"): lambda: sync_opencode_to_claude(session, bases["claude"]),
        ("opencode", "pi"): lambda: sync_opencode_to_pi(session, bases["pi"]),
        ("pi", "claude"): lambda: sync_pi_to_claude(session, bases["claude"]),
        ("pi", "opencode"): lambda: sync_pi_to_opencode(session, bases["opencode"]),
    }
    fn = dispatch.get((source_type, target_type))
    if fn is None:
        print(f"\u274c  Unsupported sync: {source_type} -> {target_type}")
        return None
    return fn()


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

        fragments.append(("class:hint",
                          "  ↑/↓ move · ←/→ switch list · Enter copy · Space favorite · Esc exit\n"))

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
        result[1] = 'switch_view_back'
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

# ─── Target picker ─────────────────────────────────────────────────────────────
def choose_target(source_type):
    """Prompt for which harness to copy the selected session into.

    Returns a harness key, or None if the user cancels. The source harness is not
    offered, since copying a session onto its own list is meaningless.
    """
    options = [h for h in HARNESSES if h != source_type]
    if not options:
        return None

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    current = [0]
    result = [None]

    def get_text():
        frags = [("class:prompt", "Copy into which harness?   ")]
        for i, harness in enumerate(options):
            marker = "▶ " if i == current[0] else "  "
            cls = "class:current" if i == current[0] else "class:normal"
            frags.append((cls, f"{marker}{HARNESS_LABELS[harness]}    "))
        frags.append(("class:hint", "\n  ←/→ choose · Enter copy · Esc cancel"))
        return frags

    kb = KeyBindings()

    @kb.add("left")
    @kb.add("up")
    def _(event):
        current[0] = (current[0] - 1) % len(options)

    @kb.add("right")
    @kb.add("down")
    def _(event):
        current[0] = (current[0] + 1) % len(options)

    @kb.add("enter")
    def _(event):
        result[0] = options[current[0]]
        event.app.exit()

    @kb.add("escape")
    @kb.add("c-c")
    def _(event):
        result[0] = None
        event.app.exit()

    style = Style.from_dict({
        "current": "reverse",
        "normal": "",
        "hint": "italic #888888",
        "prompt": "bold",
    })
    app = Application(
        layout=Layout(HSplit([Window(
            content=FormattedTextControl(get_text),
            height=3, always_hide_cursor=True)])),
        key_bindings=kb,
        full_screen=False,
        style=style,
    )
    try:
        app.run()
    except Exception:
        return None
    return result[0]


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
        "--pi-location",
        type=str,
        default="",
        help="Custom Pi sessions directory (e.g., ~/.pi/agent/sessions). Overrides auto-detection.",
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

    # Build candidate lists for each harness
    claude_candidates = []
    if args.claude_location:
        claude_candidates.append(args.claude_location)
    claude_candidates.extend(CLAUDE_SESSIONS_CANDIDATES)

    opencode_candidates = []
    if args.opencode_location:
        opencode_candidates.append(args.opencode_location)
    opencode_candidates.extend(OPCODE_SESSIONS_CANDIDATES)

    pi_candidates = []
    if args.pi_location:
        pi_candidates.append(args.pi_location)
    pi_candidates.extend(PI_SESSIONS_CANDIDATES)

    # Get the base directories for sessions
    claude_base = get_sessions_dir(claude_candidates)
    opencode_base = get_sessions_dir(opencode_candidates)
    pi_base = get_sessions_dir(pi_candidates)
    if pi_base is None:
        # Pi creates its session root on first use; fall back to the standard location
        # so a copy can be written even before Pi has ever been run.
        pi_base = os.path.expanduser("~/.pi/agent/sessions")

    bases = {"claude": claude_base, "opencode": opencode_base, "pi": pi_base}

    def load_all():
        return {
            "claude": list_sessions(claude_base, "claude") if claude_base else [],
            "opencode": list_sessions(opencode_base, "opencode") if opencode_base else [],
            "pi": list_sessions(pi_base, "pi") if pi_base else [],
        }

    all_sessions = load_all()

    print("🔍 Session locations:")
    print(f"   Claude Code: {claude_base or 'Not found'}")
    print(f"   opencode:    {opencode_base or 'Not found'}")
    print(f"   Pi:          {pi_base or 'Not found'}")
    print()
    print("Found " + ", ".join(
        f"{len(all_sessions[h])} {HARNESS_LABELS[h]}" for h in HARNESSES) + " sessions.")
    print("Use ←/→ to move between the three lists.")
    print()
    # Pause for the user to see the counts
    try:
        input("Press Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        print("\nExiting...")
        sys.exit(0)

    # Main loop. current_view indexes HARNESSES; start on a list that has content.
    current_view = next((i for i, h in enumerate(HARNESSES) if all_sessions[h]), 0)

    try:
        while True:
            source = HARNESSES[current_view]
            sessions = all_sessions[source]
            label = f"{HARNESS_LABELS[source]} · {current_view + 1}/{len(HARNESSES)}"

            # If there are no sessions in the current view, we can still switch lists.
            if not sessions:
                print(f"\nNo {HARNESS_LABELS[source]} sessions found.")
                try:
                    input("Press Enter to switch to the next list, or Ctrl-C to exit: ")
                except (EOFError, KeyboardInterrupt):
                    break
                current_view = (current_view + 1) % len(HARNESSES)
                continue

            # Show the selector for the current view
            selected_session, action = session_selector(sessions, label, favorites)

            if action == 'exit':
                break
            elif action == 'switch_view':
                current_view = (current_view + 1) % len(HARNESSES)
                continue
            elif action == 'switch_view_back':
                current_view = (current_view - 1) % len(HARNESSES)
                continue
            elif action == 'toggle_favorite':
                if selected_session is not None:
                    session_id = selected_session["id"]
                    if session_id in favorites:
                        favorites.discard(session_id)
                    else:
                        favorites.add(session_id)
                    save_favorites(favorites, FAVORITES_CACHE_FILE)
                continue
            elif action == 'sync':
                if selected_session is None:
                    continue
                target = choose_target(source)
                if target is None:
                    print("\nCopy cancelled.")
                    continue
                print(f"\nCopy {HARNESS_LABELS[source]} session "
                      f"'{selected_session['title']}' (ID: {selected_session['id']}) "
                      f"into {HARNESS_LABELS[target]} as a new session.")
                try:
                    if args.accept_all_defaults:
                        print("Copy declined (--accept-all-defaults).")
                        continue
                    answer = input("Proceed? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\nCopy declined.")
                    continue
                if answer != 'y':
                    print("Copy declined.")
                    continue
                # Proceed with the copy
                new_ref = sync_session(source, selected_session, target, bases)
                if new_ref:
                    print(f"\n✅ Copied into {HARNESS_LABELS[target]}: {new_ref}")
                    # Refresh so the new session appears without a restart.
                    all_sessions = load_all()
                else:
                    print(f"\n❌ Failed to copy into {HARNESS_LABELS[target]}.")
                save_favorites(favorites, FAVORITES_CACHE_FILE)
                time.sleep(1.5)
                continue
            # If action is None (just moved highlight), we redraw on the next iteration.

    except KeyboardInterrupt:
        print("\n👋 Interrupted. Exiting...")
        pass

    # Save favorites before exiting
    save_favorites(favorites, FAVORITES_CACHE_FILE)
    print("\n👋 Goodbye!")

if __name__ == "__main__":
    main()