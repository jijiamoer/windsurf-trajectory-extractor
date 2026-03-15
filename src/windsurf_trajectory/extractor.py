"""Windsurf Trajectory Extractor - Core extraction logic.

This module provides deep extraction of Cascade conversation history from
Windsurf's internal protobuf-encoded storage, including:
- Thinking content (internal reasoning, only in thinking mode)
- Visible responses (user-facing text)
- Tool calls with full parameters
- Microsecond-precision timestamps
- Provider information
"""

from __future__ import annotations

import base64
import json
import platform
import re
import sqlite3
import struct
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

__all__ = [
    "find_windsurf_paths",
    "find_antigravity_paths",
    "load_codeium_state",
    "workspace_name",
    "list_workspaces",
    "list_summaries",
    "extract_trajectory",
    "find_by_keywords",
    "list_antigravity_conversations",
    "extract_antigravity_conversation",
]

# Default timezone for timestamp display (configurable)
DEFAULT_TZ = timezone(timedelta(hours=8))  # CST
ANTIGRAVITY_SUMMARIES_KEY = "antigravityUnifiedStateSync.trajectorySummaries"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_BASE64_TEXT_RE = re.compile(r"^[A-Za-z0-9+/=_-]+$")


def find_windsurf_paths() -> tuple[Path | None, Path | None]:
    """Find Windsurf state.vscdb and workspaceStorage paths.

    Supports both stable (Windsurf) and preview (Windsurf - Next) versions
    on macOS, Linux, and Windows.

    Returns:
        Tuple of (state_db_path, workspace_storage_path), either may be None.
    """
    system = platform.system()
    home = Path.home()

    # Define base directories per platform
    if system == "Darwin":  # macOS
        base = home / "Library/Application Support"
    elif system == "Linux":
        base = home / ".config"
    elif system == "Windows":
        import os

        base = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    else:
        base = home / ".config"

    # Try both Windsurf variants (Next first as it's more common for dev users)
    variants = ["Windsurf - Next", "Windsurf"]

    for variant in variants:
        state_db = base / variant / "User/globalStorage/state.vscdb"
        ws_storage = base / variant / "User/workspaceStorage"
        if state_db.exists():
            return state_db, ws_storage

    return None, None


def find_antigravity_paths() -> tuple[Path | None, Path | None, Path | None]:
    """Find Antigravity state DB plus conversation/brain storage paths."""
    system = platform.system()
    home = Path.home()

    if system == "Darwin":
        base = home / "Library/Application Support"
    elif system == "Linux":
        base = home / ".config"
    elif system == "Windows":
        import os

        base = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    else:
        base = home / ".config"

    state_db = base / "Antigravity" / "User/globalStorage/state.vscdb"
    app_data_dir = home / ".gemini/antigravity"
    conversations_dir = app_data_dir / "conversations"
    brain_dir = app_data_dir / "brain"

    return (
        state_db if state_db.exists() else None,
        conversations_dir if conversations_dir.exists() else None,
        brain_dir if brain_dir.exists() else None,
    )


def load_codeium_state(state_db: Path) -> dict[str, Any]:
    """Load codeium.windsurf data from state.vscdb.

    Args:
        state_db: Path to state.vscdb file.

    Returns:
        Parsed JSON data from codeium.windsurf key.

    Raises:
        FileNotFoundError: If state.vscdb doesn't exist.
        KeyError: If codeium.windsurf key not found.
    """
    if not state_db.exists():
        raise FileNotFoundError(f"state.vscdb not found: {state_db}")

    conn = sqlite3.connect(str(state_db))
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'codeium.windsurf'"
        ).fetchone()
        if not row:
            raise KeyError("codeium.windsurf key not found in state.vscdb")
        return json.loads(row[0])
    finally:
        conn.close()


def _read_sqlite_value(state_db: Path, key: str) -> str:
    """Read a single value from VS Code-style ItemTable storage."""
    if not state_db.exists():
        raise FileNotFoundError(f"state.vscdb not found: {state_db}")

    conn = sqlite3.connect(str(state_db))
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            raise KeyError(f"{key!r} key not found in state.vscdb")
        return row[0]
    finally:
        conn.close()


def workspace_name(ws_storage: Path | None, ws_id: str) -> str:
    """Map workspace ID to human-readable project path.

    Args:
        ws_storage: Path to workspaceStorage directory.
        ws_id: Workspace ID (hash).

    Returns:
        Project path or "?" if not found.
    """
    if ws_storage is None:
        return "?"

    ws_file = ws_storage / ws_id / "workspace.json"
    if ws_file.exists():
        try:
            ws_data = json.loads(ws_file.read_text())
            folder = ws_data.get("folder", ws_data.get("workspace", "?"))
            # Parse file:// URI properly for cross-platform support
            decoded = unquote(folder)
            if decoded.startswith("file:///"):
                # Remove file:/// prefix
                path_part = decoded[8:]
                # On Windows, file:///C:/... -> C:/...
                # On Unix, file:///home/... -> /home/...
                if len(path_part) > 1 and path_part[1] == ":":
                    # Windows path (e.g., C:/Users/...)
                    return path_part
                else:
                    # Unix path - add leading slash back
                    return "/" + path_part
            return decoded
        except (json.JSONDecodeError, OSError):
            pass
    return "?"


def _file_uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a local path string when possible."""
    decoded = unquote(uri)
    if decoded.startswith("file:///"):
        path_part = decoded[8:]
        if len(path_part) > 1 and path_part[1] == ":":
            return path_part
        return "/" + path_part
    return decoded


# ── Protobuf Decoding ──────────────────────────────────────────────


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a protobuf varint."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            return result, pos
        shift += 7
    return result, pos


def _parse_fields(data: bytes, start: int, end: int) -> list[dict[str, Any]]:
    """Parse protobuf fields from a byte range."""
    fields: list[dict[str, Any]] = []
    p = start
    while p < end:
        try:
            tag, np = _decode_varint(data, p)
            if tag == 0:
                p = np
                continue
            fn, wt = tag >> 3, tag & 7
            if wt == 0:  # Varint
                val, np = _decode_varint(data, np)
                fields.append({"fn": fn, "type": "varint", "value": val, "pos": p})
                p = np
            elif wt == 2:  # Length-delimited
                sz, np = _decode_varint(data, np)
                if sz > end - np or sz < 0:
                    break
                fields.append(
                    {"fn": fn, "type": "bytes", "start": np, "end": np + sz, "pos": p}
                )
                p = np + sz
            elif wt == 1:  # Fixed64
                fields.append(
                    {
                        "fn": fn,
                        "type": "fixed64",
                        "value": struct.unpack_from("<Q", data, np)[0],
                        "pos": p,
                    }
                )
                p = np + 8
            elif wt == 5:  # Fixed32
                fields.append(
                    {
                        "fn": fn,
                        "type": "fixed32",
                        "value": struct.unpack_from("<I", data, np)[0],
                        "pos": p,
                    }
                )
                p = np + 4
            else:
                break
        except Exception:
            break
    return fields


def _parse_timestamp(
    data: bytes, start: int, end: int, tz: timezone = DEFAULT_TZ
) -> datetime | None:
    """Parse protobuf Timestamp message {f1=seconds, f2=nanos}."""
    fields = _parse_fields(data, start, end)
    seconds = nanos = 0
    for f in fields:
        if f["fn"] == 1 and f["type"] == "varint":
            seconds = f["value"]
        elif f["fn"] == 2 and f["type"] == "varint":
            nanos = f["value"]
    # Sanity check: timestamp should be reasonable (2020-2040)
    # Using wider range to avoid "time bomb" issues
    if 1577836800 < seconds < 2208988800:
        return datetime.fromtimestamp(seconds + nanos / 1e9, tz=tz)
    return None


def _try_decode_str(data: bytes, start: int, end: int) -> str | None:
    """Try to decode bytes as UTF-8 string."""
    try:
        return data[start:end].decode("utf-8")
    except Exception:
        return None


def _looks_like_base64_text(text: str) -> bool:
    text = text.strip()
    return (
        len(text) >= 16 and len(text) % 4 == 0 and bool(_BASE64_TEXT_RE.fullmatch(text))
    )


def _maybe_decode_base64_blob(data: bytes) -> bytes | None:
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None

    if not _looks_like_base64_text(text):
        return None

    try:
        decoded = base64.b64decode(text, validate=True)
    except Exception:
        return None

    return decoded or None


def _is_probable_title(text: str) -> bool:
    text = text.strip()
    if len(text) < 4 or len(text) > 200:
        return False
    if text.startswith("file://") or _UUID_RE.fullmatch(text):
        return False
    if _looks_like_base64_text(text) and not any(ch.isspace() for ch in text):
        return False
    if text in {"main", "master"}:
        return False
    if "/" in text and " " not in text and not re.search(r"[\u4e00-\u9fff]", text):
        return False
    return any(ch.isspace() for ch in text) or bool(re.search(r"[\u4e00-\u9fff]", text))


def _walk_message_strings(
    data: bytes,
    *,
    max_depth: int = 5,
    _depth: int = 0,
) -> tuple[list[str], list[datetime]]:
    """Recursively collect decoded strings and protobuf timestamps from nested blobs."""
    if _depth > max_depth:
        return [], []

    fields = _parse_fields(data, 0, len(data))
    if not fields:
        return [], []

    strings: list[str] = []
    timestamps: list[datetime] = []
    seen_strings: set[str] = set()

    for field in fields:
        if field["type"] != "bytes":
            continue

        start = field["start"]
        end = field["end"]
        raw = data[start:end]

        text = _try_decode_str(data, start, end)
        if text is not None:
            clean = text.strip()
            if clean and clean not in seen_strings:
                strings.append(clean)
                seen_strings.add(clean)

        timestamp = _parse_timestamp(data, start, end)
        if timestamp is not None:
            timestamps.append(timestamp)
            continue

        nested = _maybe_decode_base64_blob(raw)
        if nested is not None:
            sub_strings, sub_timestamps = _walk_message_strings(
                nested, max_depth=max_depth, _depth=_depth + 1
            )
            for item in sub_strings:
                if item not in seen_strings:
                    strings.append(item)
                    seen_strings.add(item)
            timestamps.extend(sub_timestamps)
            continue

        sub_strings, sub_timestamps = _walk_message_strings(
            raw, max_depth=max_depth, _depth=_depth + 1
        )
        for item in sub_strings:
            if item not in seen_strings:
                strings.append(item)
                seen_strings.add(item)
        timestamps.extend(sub_timestamps)

    return strings, timestamps


def _brain_file_inventory(brain_path: Path) -> list[dict[str, Any]]:
    """Collect a stable inventory of files under a brain directory."""
    files: list[dict[str, Any]] = []
    for path in sorted(brain_path.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "path": str(path.relative_to(brain_path)),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=DEFAULT_TZ
                ).isoformat(),
            }
        )
    return files


def _parse_antigravity_summary_entry(entry_blob: bytes) -> dict[str, Any]:
    """Parse one CascadeTrajectorySummary-like message with heuristics."""
    entry_fields = _parse_fields(entry_blob, 0, len(entry_blob))
    cascade_id = ""
    summary_blob = b""

    for field in entry_fields:
        if field["fn"] == 1 and field["type"] == "bytes" and not cascade_id:
            cascade_id = (
                _try_decode_str(entry_blob, field["start"], field["end"]) or ""
            ).strip()
        elif field["fn"] == 2 and field["type"] == "bytes" and not summary_blob:
            summary_blob = entry_blob[field["start"] : field["end"]]

    decoded_summary_blob = _maybe_decode_base64_blob(summary_blob) or summary_blob
    strings, timestamps = _walk_message_strings(decoded_summary_blob)

    workspaces = []
    for item in strings:
        if item.startswith("file://"):
            path = _file_uri_to_path(item)
            if path not in workspaces:
                workspaces.append(path)

    title = next((item for item in strings if _is_probable_title(item)), "")
    last_modified = max(timestamps).isoformat() if timestamps else None

    return {
        "cascade_id": cascade_id,
        "title": title,
        "workspace_paths": workspaces,
        "last_modified": last_modified,
        "raw_summary_size": len(decoded_summary_blob),
    }


def _load_antigravity_summaries(state_db: Path | None) -> dict[str, dict[str, Any]]:
    """Load trajectory summaries from Antigravity's state DB when available."""
    if state_db is None:
        return {}

    try:
        raw = _read_sqlite_value(state_db, ANTIGRAVITY_SUMMARIES_KEY)
    except (FileNotFoundError, KeyError):
        return {}

    blob = base64.b64decode(raw)
    summaries: dict[str, dict[str, Any]] = {}
    for field in _parse_fields(blob, 0, len(blob)):
        if field["fn"] != 1 or field["type"] != "bytes":
            continue
        entry_blob = blob[field["start"] : field["end"]]
        entry = _parse_antigravity_summary_entry(entry_blob)
        cascade_id = entry.get("cascade_id")
        if cascade_id:
            summaries[cascade_id] = entry
    return summaries


# ── Public API ────────────────────────────────────────────────────


def list_workspaces(
    state: dict[str, Any], ws_storage: Path | None
) -> list[dict[str, Any]]:
    """List all workspaces with trajectory data.

    Args:
        state: Codeium state data.
        ws_storage: Path to workspaceStorage directory.

    Returns:
        List of workspace info dicts with id, size, and path.
    """
    workspaces = []
    for k, v in state.items():
        if "cachedActiveTrajectory" in k:
            ws_id = k.split(":")[-1]
            workspaces.append(
                {
                    "id": ws_id,
                    "size": len(str(v)),
                    "path": workspace_name(ws_storage, ws_id),
                }
            )
    return sorted(workspaces, key=lambda x: -x["size"])


def list_summaries(state: dict[str, Any], ws_id: str) -> list[dict[str, str]]:
    """List conversation summaries for a workspace.

    Args:
        state: Codeium state data.
        ws_id: Workspace ID.

    Returns:
        List of summary dicts with uuid and title.

    Raises:
        KeyError: If workspace summaries not found.
    """
    key = f"windsurf.state.cachedTrajectorySummaries:{ws_id}"
    if key not in state:
        raise KeyError(f"Summaries not found for workspace: {ws_id}")

    blob = base64.b64decode(state[key])
    top = _parse_fields(blob, 0, len(blob))

    summaries = []
    for f in top:
        if f["type"] != "bytes":
            continue
        entry = _parse_fields(blob, f["start"], f["end"])
        uuid = ""
        title = ""
        for ef in entry:
            if ef["fn"] == 1 and ef["type"] == "bytes":
                uuid = _try_decode_str(blob, ef["start"], ef["end"]) or ""
            elif ef["fn"] == 2 and ef["type"] == "bytes":
                inner = _parse_fields(blob, ef["start"], ef["end"])
                for inf in inner:
                    if inf["type"] == "bytes" and not title:
                        t = _try_decode_str(blob, inf["start"], inf["end"])
                        if t and len(t) > 5 and not t.startswith("\n"):
                            title = t[:80]
        summaries.append({"uuid": uuid, "title": title})
    return summaries


def extract_trajectory(
    state: dict[str, Any], ws_id: str, tz: timezone = DEFAULT_TZ
) -> dict[str, Any]:
    """Extract complete trajectory data for a workspace.

    Args:
        state: Codeium state data.
        ws_id: Workspace ID.
        tz: Timezone for timestamp display.

    Returns:
        Dict with trajectory_uuid, size_bytes, steps list, and statistics.

    Raises:
        KeyError: If trajectory not found.
    """
    key = f"windsurf.state.cachedActiveTrajectory:{ws_id}"
    if key not in state:
        raise KeyError(f"Trajectory not found for workspace: {ws_id}")

    blob = base64.b64decode(state[key])

    # Parse top-level: f1=UUID, f2=steps container
    top = _parse_fields(blob, 0, len(blob))
    traj_uuid = ""
    inner_start = inner_end = 0

    for f in top:
        if f["fn"] == 1 and f["type"] == "bytes":
            traj_uuid = _try_decode_str(blob, f["start"], f["end"]) or ""
        elif f["fn"] == 2 and f["type"] == "bytes":
            inner_start, inner_end = f["start"], f["end"]

    # Parse steps
    steps_raw = _parse_fields(blob, inner_start, inner_end)
    steps = []

    for step_f in steps_raw:
        if step_f["type"] != "bytes":
            continue

        step = _parse_fields(blob, step_f["start"], step_f["end"])
        step_id = None
        step_type = None
        timestamp = None
        thinking = None
        visible = None
        tool_calls: list[dict[str, Any]] = []
        provider = None
        content_texts: list[str] = []

        for sf in step:
            if sf["fn"] == 1 and sf["type"] == "varint":
                step_id = sf["value"]
            elif sf["fn"] == 4 and sf["type"] == "varint":
                step_type = sf["value"]
            elif sf["fn"] == 5 and sf["type"] == "bytes":
                meta = _parse_fields(blob, sf["start"], sf["end"])
                for mf in meta:
                    if mf["fn"] == 1 and mf["type"] == "bytes":
                        timestamp = _parse_timestamp(blob, mf["start"], mf["end"], tz)
            elif sf["fn"] == 20 and sf["type"] == "bytes":
                # AI response: f3=thinking, f7=tool_call, f8=visible, f12=provider
                ai_fields = _parse_fields(blob, sf["start"], sf["end"])
                for af in ai_fields:
                    if af["fn"] == 3 and af["type"] == "bytes":
                        thinking = _try_decode_str(blob, af["start"], af["end"])
                    elif af["fn"] == 7 and af["type"] == "bytes":
                        tc_fields = _parse_fields(blob, af["start"], af["end"])
                        tc: dict[str, Any] = {}
                        for tf in tc_fields:
                            if tf["type"] == "bytes":
                                t = _try_decode_str(blob, tf["start"], tf["end"])
                                if t:
                                    if tf["fn"] == 1:
                                        tc["tool_id"] = t
                                    elif tf["fn"] == 2:
                                        tc["tool_name"] = t
                                    elif tf["fn"] == 3:
                                        try:
                                            tc["params"] = json.loads(t)
                                        except Exception:
                                            tc["params_raw"] = t[:500]
                        if tc:
                            tool_calls.append(tc)
                    elif af["fn"] == 8 and af["type"] == "bytes":
                        visible = _try_decode_str(blob, af["start"], af["end"])
                    elif af["fn"] == 12 and af["type"] == "bytes":
                        provider = _try_decode_str(blob, af["start"], af["end"])
            elif sf["fn"] in (19, 28) and sf["type"] == "bytes":
                content = _parse_fields(blob, sf["start"], sf["end"])
                for cf in content:
                    if cf["type"] == "bytes":
                        t = _try_decode_str(blob, cf["start"], cf["end"])
                        if t and len(t) > 5:
                            content_texts.append(t[:500])

        entry: dict[str, Any] = {
            "step_id": step_id,
            "step_type": step_type,
            "timestamp": timestamp.isoformat() if timestamp else None,
            "timestamp_unix_ms": int(timestamp.timestamp() * 1000)
            if timestamp
            else None,
            "content_preview": (visible or (content_texts[0] if content_texts else ""))[
                :200
            ],
        }
        if thinking:
            entry["thinking"] = thinking
        if visible:
            entry["visible"] = visible
        if tool_calls:
            entry["tool_calls"] = tool_calls
        if provider:
            entry["provider"] = provider
        steps.append(entry)

    # Statistics
    ts_list = [s["timestamp_unix_ms"] for s in steps if s["timestamp_unix_ms"]]
    stats = {
        "total_steps": len(steps),
        "steps_with_timestamp": len(ts_list),
        "steps_with_thinking": sum(1 for s in steps if s.get("thinking")),
        "steps_with_visible": sum(1 for s in steps if s.get("visible")),
        "steps_with_tool_calls": sum(1 for s in steps if s.get("tool_calls")),
    }
    if ts_list:
        stats["time_range"] = {
            "first": datetime.fromtimestamp(min(ts_list) / 1000, tz=tz).isoformat(),
            "last": datetime.fromtimestamp(max(ts_list) / 1000, tz=tz).isoformat(),
        }

    return {
        "trajectory_uuid": traj_uuid,
        "size_bytes": len(blob),
        "steps": steps,
        "statistics": stats,
    }


def find_by_keywords(
    state: dict[str, Any], keywords: list[str], ws_storage: Path | None
) -> list[dict[str, Any]]:
    """Search all trajectories for keywords.

    Args:
        state: Codeium state data.
        keywords: List of keywords to search.
        ws_storage: Path to workspaceStorage directory.

    Returns:
        List of matching workspace info with hit counts.
    """
    results = []
    for k, v in sorted(state.items()):
        if "cachedActiveTrajectory" not in k:
            continue
        ws_id = k.split(":")[-1]
        try:
            blob = base64.b64decode(v)
            text = blob.decode("ascii", errors="ignore")
            hits = {kw: text.count(kw) for kw in keywords if text.count(kw) > 0}
            if hits:
                # Try to find model info
                models = re.findall(r"--model\s+(\w+)", text)
                result = {
                    "id": ws_id,
                    "path": workspace_name(ws_storage, ws_id),
                    "size_bytes": len(blob),
                    "hits": hits,
                }
                if models:
                    result["models"] = dict(Counter(models))
                results.append(result)
        except Exception:
            pass
    return results


def list_antigravity_conversations(
    state_db: Path | None,
    conversations_dir: Path | None,
    brain_dir: Path | None,
) -> list[dict[str, Any]]:
    """List Antigravity conversations by merging summary/index and filesystem state."""
    summaries = _load_antigravity_summaries(state_db)
    conversation_files = (
        {path.stem: path for path in conversations_dir.glob("*.pb")}
        if conversations_dir is not None
        else {}
    )
    brain_dirs = (
        {path.name: path for path in brain_dir.iterdir() if path.is_dir()}
        if brain_dir is not None
        else {}
    )

    all_ids = sorted(set(summaries) | set(conversation_files) | set(brain_dirs))
    conversations: list[dict[str, Any]] = []

    for cascade_id in all_ids:
        summary = summaries.get(cascade_id, {})
        blob_path = conversation_files.get(cascade_id)
        brain_path = brain_dirs.get(cascade_id)

        blob_stat = blob_path.stat() if blob_path else None
        blob_modified = (
            datetime.fromtimestamp(blob_stat.st_mtime, tz=DEFAULT_TZ).isoformat()
            if blob_stat
            else None
        )

        conversations.append(
            {
                "cascade_id": cascade_id,
                "title": summary.get("title") or "",
                "workspace_paths": summary.get("workspace_paths", []),
                "last_modified": summary.get("last_modified") or blob_modified,
                "has_summary": cascade_id in summaries,
                "has_conversation_blob": blob_path is not None,
                "conversation_blob_size": blob_stat.st_size if blob_stat else None,
                "has_brain_dir": brain_path is not None,
            }
        )

    return sorted(
        conversations,
        key=lambda item: (
            item["last_modified"] or "",
            item["conversation_blob_size"] or 0,
        ),
        reverse=True,
    )


def extract_antigravity_conversation(
    cascade_id: str,
    state_db: Path | None,
    conversations_dir: Path | None,
    brain_dir: Path | None,
) -> dict[str, Any]:
    """Extract Antigravity metadata for a single conversation/cascade ID."""
    summaries = _load_antigravity_summaries(state_db)
    summary = summaries.get(cascade_id, {})

    blob_path = conversations_dir / f"{cascade_id}.pb" if conversations_dir else None
    if blob_path is not None and not blob_path.exists():
        blob_path = None

    brain_path = brain_dir / cascade_id if brain_dir else None
    if brain_path is not None and not brain_path.exists():
        brain_path = None

    if not summary and blob_path is None and brain_path is None:
        raise KeyError(f"Antigravity conversation not found: {cascade_id}")

    result: dict[str, Any] = {
        "cascade_id": cascade_id,
        "summary": summary,
        "conversation_blob": None,
        "brain": None,
    }

    if blob_path is not None:
        data = blob_path.read_bytes()
        stat = blob_path.stat()
        result["conversation_blob"] = {
            "path": str(blob_path),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=DEFAULT_TZ
            ).isoformat(),
            "header_hex": data[:16].hex(),
            "tail_hex": data[-16:].hex() if len(data) >= 16 else data.hex(),
        }

    if brain_path is not None:
        result["brain"] = {
            "path": str(brain_path),
            "files": _brain_file_inventory(brain_path),
        }

    return result
