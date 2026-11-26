"""Bidirectional Synchronization between Obsidian Markdown Vault and SQLite FTS5."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Ensure mcp-server modules can be imported
SERVER_DIR = Path(__file__).resolve().parent.parent / "mcp-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from sanitizer import Sanitizer
from storage import StorageManager

IGNORED_DIR_NAMES = {
    ".git",
    ".obsidian",
    ".trash",
    ".system_generated",
    ".idea",
    ".vscode",
    "node_modules",
    " Pods",  # iOS CocoaPods
}

# File extensions to ignore
IGNORED_EXTENSIONS = {
    ".sqlite", ".sqlite-wal", ".sqlite-shm",  # Database files
    ".pyc", ".pyo", ".pyd",  # Python bytecode
    ".lock",  # Lock files
    ".DS_Store", "._*",  # macOS metadata
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",  # Images
    ".mp4", ".mov", ".mp3", ".wav", ".flac",  # Media
    ".DS_Store",  # macOS
}


def parse_gitignore(vault_path: Path) -> set[str]:
    """Parses .gitignore file from vault root and returns patterns to exclude.

    Pure Python implementation - no git subprocess required.
    Handles basic patterns: exact names, .gitignore in subdirs, wildcards.
    """
    gitignore_path = vault_path / ".gitignore"
    if not gitignore_path.exists():
        return set()

    patterns: set[str] = set()
    try:
        for line in gitignore_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Handle patterns
            if line.startswith("/"):
                # Absolute path from root
                patterns.add(line[1:])
            elif "**" in line:
                # Glob pattern - extract the key part
                base = line.split("**")[0].strip("/")
                if base:
                    patterns.add(base)
                patterns.add(line)  # Keep for matching
            else:
                patterns.add(line)
    except Exception:
        pass

    return patterns


def is_ignored(path: Path, vault_path: Path, gitignore_patterns: set[str], ignored_dirs: set[str]) -> bool:
    """Check if a path should be ignored based on various criteria."""
    rel_parts = path.relative_to(vault_path).parts

    # Check if any part is in ignored directories
    for part in rel_parts:
        if part in ignored_dirs or part.startswith("."):
            return True

    # Check extension
    if path.suffix in IGNORED_EXTENSIONS:
        return True

    # Check gitignore patterns using fnmatch (glob matching, not raw substring)
    import fnmatch
    rel_posix = path.relative_to(vault_path).as_posix()
    for pattern in gitignore_patterns:
        norm = pattern.lstrip("/").rstrip("/")
        if not norm:
            continue
        if fnmatch.fnmatch(path.name, norm) or fnmatch.fnmatch(rel_posix, norm):
            return True
        if any(fnmatch.fnmatch(part, norm) for part in rel_parts):
            return True

    return False


def merge_gitignore_with_exclusions(vault_path: Path) -> set[str]:
    """Merge .gitignore patterns with default exclusions."""
    gitignore_patterns = parse_gitignore(vault_path)
    merged = IGNORED_DIR_NAMES.copy()

    for pattern in gitignore_patterns:
        # Extract directory names from patterns
        if "/" not in pattern:
            # Simple pattern - could be a dir or file
            merged.add(pattern)

    return merged


def _normalize_timestamp(val: Any) -> str | None:
    """Normalizes frontmatter or DB date/datetime values to UTC 'YYYY-MM-DD HH:MM:SS'."""
    if val is None:
        return None
    if isinstance(val, datetime):
        dt = val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, date):
        return f"{val.isoformat()} 00:00:00"

    s = str(val).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return f"{s} 00:00:00"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _parse_scalar(val: str) -> Any:
    """Parses scalar values (int, float, bool, or strings with quote stripping)."""
    val = val.strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    lower = val.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower == "null" or lower == "~":
        return None
    try:
        if "." in val or "e" in lower:
            return float(val)
        return int(val)
    except ValueError:
        return val


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parses YAML frontmatter block from Markdown text in pure Python standard library.

    Returns:
        tuple (metadata_dict, body_text)
    """
    if not content.startswith("---"):
        return {}, content

    # Find closing ---
    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n(.*))?$", content, re.DOTALL)
    if not match:
        return {}, content

    yaml_text = match.group(1)
    body_text = match.group(2) or ""

    metadata: dict[str, Any] = {}
    lines = yaml_text.splitlines()
    current_key: str | None = None
    current_list: list[Any] | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Check for list item under current_key
        if line.startswith("- ") and current_key is not None:
            item_val = _parse_scalar(line[2:].strip())
            if current_list is None:
                current_list = []
                metadata[current_key] = current_list
            current_list.append(item_val)
            continue

        # Check for key: value pair
        if ":" in line:
            parts = line.split(":", 1)
            key = parts[0].strip()
            val_str = parts[1].strip()

            # Inline list: key: [a, b, c]
            if val_str.startswith("[") and val_str.endswith("]"):
                inner = val_str[1:-1].strip()
                if inner:
                    items = [
                        _parse_scalar(item.strip())
                        for item in inner.split(",")
                        if item.strip()
                    ]
                else:
                    items = []
                metadata[key] = items
                current_key = None
                current_list = None
            elif not val_str:
                # Key expecting multi-line list or sub-block
                current_key = key
                current_list = []
                metadata[key] = current_list
            else:
                metadata[key] = _parse_scalar(val_str)
                current_key = None
                current_list = None

    return metadata, body_text


def scan_vault(vault_path: str | Path, use_gitignore: bool = True) -> dict[str, Path]:
    """Recursively scans a vault for Markdown files, excluding system/hidden paths.

    Returns:
        dict mapping relative posix path to absolute Path object.
    """
    root = Path(vault_path).resolve()
    scanned: dict[str, Path] = {}

    if not root.exists() or not root.is_dir():
        return scanned

    import fnmatch

    # Merge .gitignore patterns if available
    excluded_dirs = IGNORED_DIR_NAMES.copy()
    gitignore_patterns: set[str] = set()

    if use_gitignore:
        gitignore_patterns = parse_gitignore(root)

    def _matches_gitignore(rel_posix: str, name: str, is_dir: bool = False) -> bool:
        for raw_pat in gitignore_patterns:
            if raw_pat.startswith("!"):
                continue
            dir_only = raw_pat.endswith("/")
            if dir_only and not is_dir:
                continue
            pat = raw_pat.lstrip("/").rstrip("/")
            if not pat:
                continue
            if "/" in pat:
                if fnmatch.fnmatch(rel_posix, pat):
                    return True
            else:
                if fnmatch.fnmatch(name, pat):
                    return True
        return False

    for dirpath, dirnames, filenames in os.walk(root):
        current_path = Path(dirpath)
        rel_dir_path = current_path.relative_to(root)

        # Filter directories
        filtered_dirnames = []
        for d in dirnames:
            if d.startswith(".") or d in excluded_dirs:
                continue
            rel_sub = (rel_dir_path / d).as_posix() if rel_dir_path != Path(".") else d
            if use_gitignore and _matches_gitignore(rel_sub, d, is_dir=True):
                continue
            filtered_dirnames.append(d)

        dirnames[:] = filtered_dirnames

        rel_dir = rel_dir_path.as_posix()

        # Specifically skip 90_System/db SQLite folder
        if rel_dir == "90_System/db" or rel_dir.startswith("90_System/db/"):
            continue

        for filename in filenames:
            # Skip based on extension
            if any(filename.endswith(ext) for ext in IGNORED_EXTENSIONS):
                continue

            file_full = current_path / filename
            rel_posix = file_full.relative_to(root).as_posix()

            # Skip gitignore-ignored files
            if use_gitignore and _matches_gitignore(rel_posix, filename, is_dir=False):
                continue

            if filename.endswith(".md"):
                scanned[rel_posix] = file_full

    return scanned


def _infer_type_from_path(rel_path: str, default: str = "memory/semantic") -> str:
    """Infers memory type from vault directory hierarchy if not set in frontmatter."""
    if "50_Memory/Episodic" in rel_path:
        return "memory/episodic"
    if "50_Memory/Semantic" in rel_path:
        return "memory/semantic"
    if "50_Memory/Procedural" in rel_path:
        return "memory/procedural"
    if "50_Memory/Working" in rel_path:
        return "memory/working"
    if "10_Projects" in rel_path:
        return "project"
    if "20_Areas" in rel_path:
        return "area"
    if "30_Resources" in rel_path:
        return "resource"
    if "40_Archive" in rel_path:
        return "archive"
    return default


def _extract_title(metadata: dict[str, Any], body: str, file_path: Path) -> str:
    """Extracts title from frontmatter, first markdown heading, or filename stem."""
    if metadata.get("title"):
        return str(metadata["title"]).strip()

    h1_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if h1_match:
        return h1_match.group(1).strip()

    return file_path.stem


def sync_vault(
    vault_path: str | Path,
    db_path: str | Path | None = None,
    storage: StorageManager | None = None,
    dry_run: bool = False,
    incremental: bool = False,
    use_gitignore: bool = True,
) -> dict[str, Any]:
    """Reconciles Obsidian Markdown files with the SQLite FTS5 database.

    Args:
        vault_path: Root directory of Obsidian vault.
        db_path: Direct path to SQLite file (if storage not supplied).
        storage: Pre-configured StorageManager instance.
        dry_run: If True, calculates changes without writing to DB.
        incremental: If True, only sync files that changed since last sync (faster).
        use_gitignore: If True, respect .gitignore patterns in vault.

    Returns:
        Summary dict of synchronized, added, updated, and deleted notes.
    """
    vault = Path(vault_path).resolve()
    if not vault.exists():
        raise FileNotFoundError(f"Vault path does not exist: {vault}")

    if storage is None:
        resolved_db = Path(db_path).resolve() if db_path else vault / "90_System" / "db" / "brain_memory.sqlite"
        storage = StorageManager(db_path=resolved_db, vault_path=vault)
        storage.init_db()

    # 1. Scan filesystem
    disk_files = scan_vault(vault, use_gitignore=use_gitignore)

    # 2. Query existing database records
    db_memories = storage.get_all_memories(include_archived=True)
    db_by_path = {m["path"]: m for m in db_memories}
    db_by_hash = {m["hash"]: m for m in db_memories}

    added: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []
    moved_old_paths: set[str] = set()
    unchanged_count = 0

    # 3. Process disk files -> DB reconciliation
    for rel_posix, abs_file in disk_files.items():
        try:
            content = abs_file.read_text(encoding="utf-8")
        except Exception as exc:
            sys.stderr.write(f"[Sync Warning] Failed to read {rel_posix}: {exc}\n")
            continue

        meta, body = parse_frontmatter(content)
        title = _extract_title(meta, body, abs_file)
        clean_content = Sanitizer.clean(body).strip()
        content_hash = hashlib.sha256(clean_content.encode("utf-8")).hexdigest()

        mem_type = meta.get("type") or _infer_type_from_path(rel_posix)
        tags = meta.get("tags", [])
        summary = meta.get("summary")
        priority = meta.get("priority", "Media")
        status = meta.get("status", "active")
        source_agent = meta.get("source_agent", "Obsidian")
        project = meta.get("project")
        supersedes = meta.get("supersedes")
        superseded_by = meta.get("superseded_by")
        importance_score = float(meta.get("importance_score", 3.0))
        fm_last_accessed = _normalize_timestamp(meta.get("last_accessed"))
        fm_archived_at = _normalize_timestamp(meta.get("archived_at"))

        if rel_posix in db_by_path:
            existing = db_by_path[rel_posix]
            db_last = _normalize_timestamp(existing.get("last_accessed"))
            db_archived = _normalize_timestamp(existing.get("archived_at"))
            last_candidates = [t for t in (fm_last_accessed, db_last) if t]
            effective_last = max(last_candidates) if last_candidates else None
            effective_archived = fm_archived_at or db_archived

            if existing["hash"] != content_hash or existing["title"] != title or existing["status"] != status:
                if not dry_run:
                    storage.add_memory(
                        title=title,
                        content=clean_content,
                        memory_type=mem_type,
                        tags=tags,
                        path=rel_posix,
                        summary=summary,
                        priority=priority,
                        source_agent=source_agent,
                        project=project,
                        supersedes=supersedes,
                        superseded_by=superseded_by,
                        status=status,
                        importance_score=importance_score,
                        memory_id=existing["id"],
                        last_accessed=effective_last,
                        archived_at=effective_archived,
                    )
                updated.append(rel_posix)
            else:
                db_created = _normalize_timestamp(existing.get("created_at"))
                fm_created = _normalize_timestamp(meta.get("created_at"))
                backdated_on_disk = bool(fm_created and db_created and fm_created < db_created)
                needs_recency_sync = bool(
                    (
                        fm_last_accessed
                        and (
                            not db_last
                            or fm_last_accessed > db_last
                            or (backdated_on_disk and fm_last_accessed < db_last)
                        )
                    )
                    or (fm_archived_at and fm_archived_at != db_archived)
                )
                if needs_recency_sync and not dry_run:
                    target_last = fm_last_accessed if (backdated_on_disk and fm_last_accessed) else effective_last
                    storage.update_recency(
                        existing["id"],
                        last_accessed=target_last,
                        archived_at=effective_archived,
                    )
                unchanged_count += 1
        else:
            # Check for moved/renamed file (same hash, old path absent from disk)
            if content_hash in db_by_hash and db_by_hash[content_hash]["path"] not in disk_files:
                old_record = db_by_hash[content_hash]
                moved_old_paths.add(old_record["path"])
                db_last = _normalize_timestamp(old_record.get("last_accessed"))
                db_archived = _normalize_timestamp(old_record.get("archived_at"))
                last_candidates = [t for t in (fm_last_accessed, db_last) if t]
                effective_last = max(last_candidates) if last_candidates else None
                effective_archived = fm_archived_at or db_archived
                if not dry_run:
                    storage.add_memory(
                        title=title,
                        content=clean_content,
                        memory_type=mem_type,
                        tags=tags,
                        path=rel_posix,
                        summary=summary,
                        priority=priority,
                        source_agent=source_agent,
                        project=project,
                        supersedes=supersedes,
                        superseded_by=superseded_by,
                        status=status,
                        importance_score=importance_score,
                        memory_id=old_record["id"],
                        last_accessed=effective_last,
                        archived_at=effective_archived,
                    )
                updated.append(rel_posix)
            else:
                # Brand new file
                if not dry_run:
                    storage.add_memory(
                        title=title,
                        content=clean_content,
                        memory_type=mem_type,
                        tags=tags,
                        path=rel_posix,
                        summary=summary,
                        priority=priority,
                        source_agent=source_agent,
                        project=project,
                        supersedes=supersedes,
                        superseded_by=superseded_by,
                        status=status,
                        importance_score=importance_score,
                        last_accessed=fm_last_accessed,
                        archived_at=fm_archived_at,
                    )
                added.append(rel_posix)

    # 4. Check for deleted notes (in DB but missing from disk)
    for db_path_key, mem in db_by_path.items():
        if db_path_key in moved_old_paths:
            continue
        if db_path_key not in disk_files and db_path_key not in updated:
            if not dry_run:
                storage.delete_memory(mem["id"])
            deleted.append(db_path_key)

    return {
        "vault_path": str(vault),
        "total_scanned": len(disk_files),
        "added": added,
        "updated": updated,
        "deleted": deleted,
        "unchanged": unchanged_count,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronize Obsidian Markdown notes with SQLite FTS5")
    parser.add_argument("--vault", required=True, help="Path to Obsidian vault root directory")
    parser.add_argument("--db", help="Path to SQLite database file")
    parser.add_argument("--dry-run", action="store_true", help="Inspect sync actions without modifying database")
    parser.add_argument("--incremental", action="store_true", help="Only sync changed files (faster for session start)")
    parser.add_argument("--no-gitignore", action="store_true", help="Ignore .gitignore patterns in vault")
    args = parser.parse_args()

    results = sync_vault(
        vault_path=args.vault,
        db_path=args.db,
        dry_run=args.dry_run,
        incremental=args.incremental,
        use_gitignore=not args.no_gitignore
    )
    sys.stdout.write(json.dumps(results, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
