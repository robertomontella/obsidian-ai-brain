"""StorageManager for AI-BRAIN with SQLite WAL mode and FTS5 BM25 search."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from sanitizer import Sanitizer

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

COGNITIVE_HALF_LIVES_DAYS: dict[str, float] = {
    "memory/working": 7.0,
    "memory/episodic": 45.0,
    "memory/procedural": 180.0,
    "memory/semantic": 365.0,
    "project": 365.0,
    "area": 365.0,
    "resource": 365.0,
}
DEFAULT_HALF_LIFE_DAYS = 180.0


def _utc_now_str() -> str:
    """Returns current UTC timestamp in canonical storage format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_utc_timestamp(ts: str | None) -> datetime | None:
    """Parses UTC timestamp strings (YYYY-MM-DD HH:MM:SS, ISO-8601, YYYY-MM-DD)."""
    if not ts:
        return None
    s = str(ts).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _patch_frontmatter_text(text: str, updates: dict[str, str]) -> str:
    """Updates or inserts top-level YAML frontmatter keys without altering body or other keys."""
    if not updates:
        return text

    match = re.match(
        r"^---[ \t]*(\r?\n)(.*?)\r?\n---[ \t]*(\r?\n.*)?$",
        text,
        re.DOTALL,
    )
    if match:
        nl = match.group(1)
        yaml_block = match.group(2)
        tail = match.group(3)
        yaml_lines = yaml_block.splitlines()

        for key, val in updates.items():
            replacement_line = f"{key}: {val}"
            key_pattern = re.compile(rf"^{re.escape(key)}\s*:")
            found_idx: int | None = None
            for idx, line in enumerate(yaml_lines):
                if not line.startswith((" ", "\t", "-")) and key_pattern.match(line):
                    found_idx = idx
                    break
            if found_idx is not None:
                yaml_lines[found_idx] = replacement_line
            else:
                yaml_lines.append(replacement_line)

        rebuilt_yaml = nl.join(yaml_lines)
        return f"---{nl}{rebuilt_yaml}{nl}---" + (tail if tail is not None else "")

    nl = "\r\n" if "\r\n" in text else "\n"
    header_lines = [f"{k}: {v}" for k, v in updates.items()]
    return f"---{nl}" + nl.join(header_lines) + f"{nl}---{nl}{nl}" + text


def upsert_frontmatter_fields(file_path: Path, updates: dict[str, str]) -> bool:
    """Patches YAML frontmatter keys on a Markdown file without modifying body or updated_at."""
    if not updates or not file_path.is_file():
        return False
    original = file_path.read_text(encoding="utf-8")
    updated = _patch_frontmatter_text(original, updates)
    if updated != original:
        file_path.write_text(updated, encoding="utf-8")
        return True
    return False


class StorageManager:
    """Thread-safe SQLite storage manager with WAL mode, FTS5 BM25 search,

    safe connection lifecycle, and auto-sanitizing memory insertion.
    """

    def __init__(self, db_path: str | Path, vault_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path).resolve()
        self.vault_path = Path(vault_path).resolve() if vault_path else None
        self._lock = threading.RLock()
        self._dir_ensured = False
        self._wal_configured = False

    def _is_writable_path(self, rel_path: str) -> bool:
        """Returns True iff rel_path is a safe, writable path inside the vault."""
        if not self.vault_path or not rel_path:
            return False
        clean_rel = str(rel_path).replace("\\", "/").strip("/")
        if not clean_rel:
            return False
        parts = clean_rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            return False
        target_file = (self.vault_path / clean_rel).resolve()
        try:
            target_file.relative_to(self.vault_path)
        except ValueError:
            return False
        return True

    def _get_connection(self, read_only: bool = False) -> sqlite3.Connection:
        """Creates and configures a SQLite connection with WAL mode and busy timeout."""
        if not self._dir_ensured:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._dir_ensured = True

        conn = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        if not self._wal_configured:
            mode_row = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
            if mode_row and str(mode_row[0]).lower() == "wal":
                self._wal_configured = True
        conn.execute("PRAGMA busy_timeout=5000;")
        if not read_only:
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    @contextlib.contextmanager
    def _connection(self, read_only: bool = False):
        """Context manager guaranteeing connection closing upon block exit."""
        conn = self._get_connection(read_only=read_only)
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self, schema_path: str | Path | None = None) -> None:
        """Initializes database tables, indices, and FTS5 triggers from schema.sql."""
        resolved_schema = Path(schema_path) if schema_path else DEFAULT_SCHEMA_PATH
        if not resolved_schema.exists():
            raise FileNotFoundError(f"Schema file not found at: {resolved_schema}")

        with self._lock:
            with open(resolved_schema, "r", encoding="utf-8") as f:
                schema_sql = f.read()

            with self._connection() as conn:
                with conn:
                    table_exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
                    ).fetchone()
                    if table_exists:
                        cols = {
                            row["name"]
                            for row in conn.execute("PRAGMA table_info(memories)").fetchall()
                        }
                        if "last_accessed" not in cols:
                            conn.execute("ALTER TABLE memories ADD COLUMN last_accessed TEXT;")
                            conn.execute(
                                "UPDATE memories SET last_accessed = COALESCE(updated_at, created_at) "
                                "WHERE last_accessed IS NULL;"
                            )
                        if "archived_at" not in cols:
                            conn.execute("ALTER TABLE memories ADD COLUMN archived_at TEXT;")
                        trig = conn.execute(
                            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='memories_au'"
                        ).fetchone()
                        if trig and trig["sql"] and "UPDATE OF" not in trig["sql"].upper():
                            conn.execute("DROP TRIGGER IF EXISTS memories_au;")
                conn.executescript(schema_sql)

    def add_memory(
        self,
        title: str,
        content: str,
        memory_type: str = "memory/semantic",
        tags: list[str] | str | None = None,
        path: str | None = None,
        summary: str | None = None,
        priority: str = "Media",
        source_agent: str = "Unknown",
        project: str | None = None,
        supersedes: str | None = None,
        superseded_by: str | None = None,
        status: str = "active",
        importance_score: float = 3.0,
        memory_id: str | None = None,
        last_accessed: str | None = None,
        archived_at: str | None = None,
        inject_wikilinks: bool = False,
    ) -> dict[str, Any]:
        """Saves a new memory into the vault with automatic anti-leak sanitization

        and SQLite UPSERT to preserve FTS5 synchronization.

        Args:
            title: Descriptive title of the memory note.
            content: Markdown content of the note.
            memory_type: Cognitive tier (memory/episodic, memory/semantic,
              memory/procedural, memory/working).
            tags: List of tags or string.
            path: Relative path in the vault. If None, generated automatically.
            summary: Brief synopsis or abstract.
            priority: Alta, Media, or Bassa.
            source_agent: Name of the creating agent (e.g., Antigravity).
            project: Optional associated project name.
            supersedes: Optional reference to a superseded note ID or title.
            superseded_by: Optional reference to note that supersedes this.
            status: active, superseded, or archived.
            importance_score: Float between 1.0 and 5.0.
            memory_id: Optional 12-character identifier.
            last_accessed: Optional UTC timestamp of last retrieval.
            archived_at: Optional UTC timestamp when archived.

        Returns:
            Dictionary representing the stored memory record.
        """
        # 1. Deterministic Sanitization
        clean_title = Sanitizer.clean(title).strip()
        clean_content = Sanitizer.clean(content).strip()
        clean_summary = Sanitizer.clean(summary).strip() if summary else None

        # 2. Content Hash
        content_hash = hashlib.sha256(clean_content.encode("utf-8")).hexdigest()

        # 3. Path Generation
        if not path:
            type_folder_map = {
                "memory/episodic": "Episodic",
                "memory/semantic": "Semantic",
                "memory/procedural": "Procedural",
                "memory/working": "Working",
            }
            subfolder = type_folder_map.get(memory_type, "Semantic")
            safe_slug = re.sub(r"[^\w\-\s]", "", clean_title).strip()
            safe_slug = re.sub(r"[-\s]+", " ", safe_slug)[:60]
            if not safe_slug:
                safe_slug = f"memory-{content_hash[:8]}"
            path = f"50_Memory/{subfolder}/{safe_slug}.md"

        now_str = _utc_now_str()

        # 4. ID Resolution (reuse existing ID if path already exists)
        with self._connection() as conn:
            existing_row = conn.execute(
                "SELECT id, access_count, created_at, last_accessed, archived_at FROM memories WHERE path = ?",
                (path,),
            ).fetchone()
            if existing_row:
                if not memory_id:
                    memory_id = existing_row["id"]
                initial_access = existing_row["access_count"] or 0
                initial_created = existing_row["created_at"]
                effective_last_accessed = last_accessed or existing_row["last_accessed"] or now_str
                effective_archived_at = archived_at if archived_at is not None else existing_row["archived_at"]
            else:
                if not memory_id:
                    memory_id = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
                initial_access = 0
                initial_created = now_str
                effective_last_accessed = last_accessed or now_str
                effective_archived_at = archived_at

        # 5. Tags Normalization
        if tags is None:
            tags_json = "[]"
        elif isinstance(tags, (list, tuple, set)):
            tags_json = json.dumps(list(tags), ensure_ascii=False)
        elif isinstance(tags, str):
            try:
                parsed = json.loads(tags)
                tags_json = json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, list) else json.dumps([tags])
            except json.JSONDecodeError:
                parts = [p.strip() for p in tags.split(",") if p.strip()]
                tags_json = json.dumps(parts, ensure_ascii=False)
        else:
            tags_json = json.dumps([])

        record = {
            "id": memory_id,
            "path": path,
            "title": clean_title,
            "type": memory_type,
            "summary": clean_summary,
            "tags": tags_json,
            "content": clean_content,
            "hash": content_hash,
            "created_at": initial_created,
            "updated_at": now_str,
            "priority": priority,
            "source_agent": source_agent,
            "project": project,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
            "status": status,
            "importance_score": float(importance_score),
            "access_count": initial_access,
            "last_accessed": effective_last_accessed,
            "archived_at": effective_archived_at,
        }

        # SQLite UPSERT preserving original created_at, access_count, and existing last_accessed on conflict
        upsert_sql = """
        INSERT INTO memories (
            id, path, title, type, summary, tags, content, hash,
            created_at, updated_at, priority, source_agent, project,
            supersedes, superseded_by, status, importance_score, access_count,
            last_accessed, archived_at
        ) VALUES (
            :id, :path, :title, :type, :summary, :tags, :content, :hash,
            :created_at, :updated_at, :priority, :source_agent, :project,
            :supersedes, :superseded_by, :status, :importance_score, :access_count,
            :last_accessed, :archived_at
        )
        ON CONFLICT(id) DO UPDATE SET
            path = excluded.path,
            title = excluded.title,
            type = excluded.type,
            summary = excluded.summary,
            tags = excluded.tags,
            content = excluded.content,
            hash = excluded.hash,
            updated_at = excluded.updated_at,
            priority = excluded.priority,
            source_agent = excluded.source_agent,
            project = excluded.project,
            supersedes = excluded.supersedes,
            superseded_by = excluded.superseded_by,
            status = excluded.status,
            importance_score = excluded.importance_score,
            access_count = memories.access_count,
            last_accessed = COALESCE(excluded.last_accessed, memories.last_accessed),
            archived_at = COALESCE(excluded.archived_at, memories.archived_at)
        """

        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(upsert_sql, record)

        # Auto-wikilink injection: only when explicitly requested (e.g. brain_auto_capture)
        if inject_wikilinks:
            self._inject_wikilinks(record)

        return record

    def _inject_wikilinks(self, record: dict[str, Any]) -> None:
        """Finds related existing notes in the vault and injects [[wikilinks]] into the content.

        Only links to notes that already exist in the vault. Avoids duplicates on repeated updates.
        """
        try:
            import sys
            # Search for related notes using FTS5 on significant keywords
            words = [w for w in re.findall(r"[A-Za-z0-9_]{4,}", f"{record.get('title', '')} {record.get('content', '')[:300]}") if w.lower() not in {"this", "that", "with", "from", "have", "will", "should", "must", "into", "when", "where", "which"}]
            seen_w: set[str] = set()
            unique_words: list[str] = []
            for w in words:
                lw = w.lower()
                if lw not in seen_w:
                    seen_w.add(lw)
                    unique_words.append(w)
                if len(unique_words) >= 8:
                    break
            fts_query = " OR ".join(unique_words) if unique_words else record.get("title", "")
            related = self.search_memory(
                query=fts_query,
                limit=10,
                memory_type=None,
            )

            # Also find existing notes with the same project to link them together
            project = record.get("project")
            current_id = record.get("id")
            if project:
                with self._connection(read_only=True) as conn:
                    proj_rows = conn.execute(
                        "SELECT * FROM memories WHERE project = ? AND status != 'archived' AND id != ? LIMIT 10",
                        (project, current_id),
                    ).fetchall()
                    print(f"[DEBUG] proj_rows: {len(proj_rows)} notes for project {project}", file=sys.stderr)
                    for row in proj_rows:
                        proj_note = dict(row)
                        if proj_note["id"] not in {rel.get("id") for rel in related}:
                            related.append(proj_note)
                            print(f"[DEBUG]   Added proj_note: {proj_note.get('title')}", file=sys.stderr)

            print(f"[DEBUG] total related: {len(related)}", file=sys.stderr)
            # Filter: only link to notes that exist on disk, are different from current note,
            # and are not already linked
            current_path = record.get("path", "")
            current_title = record.get("title", "")
            content = record.get("content", "")

            # Extract existing wikilinks to avoid duplicates
            existing_links = set(re.findall(r"\[\[([^\]]+)\]\]", content))
            print(f"[DEBUG] existing_links: {existing_links}", file=sys.stderr)

            new_links: list[str] = []
            new_link_ids: list[str] = []
            for rel in related:
                rel_title = rel.get("title", "")
                rel_path = rel.get("path", "")
                rel_id = rel.get("id", "")
                if rel_path == current_path or rel_title == current_title:
                    continue
                if rel_title in existing_links:
                    continue

                # Check if the file actually exists on disk
                file_exists = True
                if self.vault_path:
                    file_path = self.vault_path / rel_path
                    file_exists = file_path.exists()

                if not file_exists:
                    continue

                new_links.append(rel_title)
                if rel_id:
                    new_link_ids.append(rel_id)
                if len(new_links) >= 5:  # Limit to 5 new links
                    break

            print(f"[DEBUG] new_links: {new_links}", file=sys.stderr)
            if new_links:
                for rel_id in new_link_ids:
                    if current_id and rel_id:
                        self.add_relation(current_id, rel_id, "relates_to")
                # Append wikilinks at the end of content
                print(f"[DEBUG] Creating link_section with {len(new_links)} items", file=sys.stderr)
                print(f"[DEBUG] content repr: {repr(content)}", file=sys.stderr)
                print(f"[DEBUG] content.rstrip() repr: {repr(content.rstrip())}", file=sys.stderr)
                link_section = "\n\n---\n\n**Related Notes:**\n" + "\n".join(f"- [[{title}]]" for title in new_links)
                print(f"[DEBUG] link_section repr: {repr(link_section)}", file=sys.stderr)
                updated_content = content.rstrip() + link_section
                record["content"] = updated_content

                # Update the record in the database with the new content
                update_sql = """
                UPDATE memories
                SET content = ?, hash = ?, updated_at = ?
                WHERE id = ?
                """
                new_hash = hashlib.sha256(updated_content.encode("utf-8")).hexdigest()
                now_str = _utc_now_str()

                with self._lock:
                    with self._connection() as conn:
                        with conn:
                            conn.execute(update_sql, (updated_content, new_hash, now_str, record["id"]))

                # Also update the vault file if writable
                if current_path.endswith(".md"):
                    # Determine vault root from either vault_path or db_path
                    vault_root = self.vault_path
                    print(f"[DEBUG] self.vault_path is None: {self.vault_path is None}", file=sys.stderr)
                    if not vault_root:
                        vault_root = self.db_path.parent.parent.parent  # Go up from 90_System/db/ to vault root
                        print(f"[DEBUG] Derived vault_root from db_path: {vault_root}", file=sys.stderr)

                    if vault_root:
                        file_path = vault_root / current_path
                        # Check if the path is safe and writable
                        try:
                            resolved_path = file_path.resolve()
                            resolved_root = vault_root.resolve()
                            resolved_path.relative_to(resolved_root)
                            # Write the file (whether it exists or not)
                            print(f"[DEBUG] Writing file with wikilinks", file=sys.stderr)
                            print(f"[DEBUG] updated_content id: {id(updated_content)}", file=sys.stderr)
                            print(f"[DEBUG] updated_content full: {updated_content}", file=sys.stderr)
                            formatted = self._format_markdown_note({
                                **record,
                                "content": updated_content,
                                "hash": new_hash,
                                "updated_at": now_str,
                            })
                            print(f"[DEBUG] formatted content length: {len(formatted)}", file=sys.stderr)
                            file_path.write_text(formatted, encoding="utf-8")
                            print(f"[DEBUG] File written successfully", file=sys.stderr)
                        except ValueError as ve:
                            print(f"[DEBUG] Path outside vault root, skipping file update: {ve}", file=sys.stderr)
                            pass  # Path outside vault root, skip file update

        except Exception as e:
            # Silently fail - wikilinks are a convenience, not critical
            import sys
            print(f"[DEBUG ERROR] _inject_wikilinks: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

    def _apply_recency_ranking(
        self,
        rows: list[sqlite3.Row],
        limit: int,
    ) -> list[dict[str, Any]]:
        """Re-ranks candidate rows by combining BM25 score with cognitive half-life recency decay."""
        if not rows or limit <= 0:
            return []

        now_dt = datetime.now(timezone.utc)
        ranked: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            ref_ts = item.get("last_accessed") or item.get("updated_at") or item.get("created_at")
            ref_dt = _parse_utc_timestamp(ref_ts)
            if ref_dt is None:
                age_days = 365.0
            else:
                age_days = max(0.0, (now_dt - ref_dt).total_seconds() / 86400.0)

            half_life = COGNITIVE_HALF_LIVES_DAYS.get(item.get("type") or "", DEFAULT_HALF_LIFE_DAYS)
            decay = math.pow(0.5, age_days / half_life)
            bm25_score = float(item.get("rank") or 0.0)
            combined_rank = bm25_score * (0.1 + 0.9 * decay) - (2.0 * decay)

            item["bm25_rank"] = bm25_score
            item["recency_score"] = round(decay, 6)
            item["rank"] = combined_rank
            ranked.append(item)

        ranked.sort(key=lambda r: (r["rank"], -r["recency_score"]))
        return ranked[:limit]

    def search_memory(
        self,
        query: str,
        limit: int = 5,
        tag: str | None = None,
        memory_type: str | None = None,
        update_access: bool = False,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """Searches memories using FTS5 BM25 + cognitive half-life recency ranking."""
        if limit <= 0:
            return []

        candidate_limit = max(int(limit) * 4, 25)
        clean_query = query.strip() if query else ""

        if not clean_query:
            sql = "SELECT *, 0.0 as rank FROM memories WHERE status != 'archived'"
            params: list[Any] = []
            if memory_type:
                sql += " AND type = ?"
                params.append(memory_type)
            if tag:
                sql += " AND (tags LIKE ? OR tags LIKE ?)"
                params.extend([f'%"{tag}"%', f"%{tag}%"])
            if project:
                sql += " AND project = ?"
                params.append(project)
            sql += " ORDER BY COALESCE(last_accessed, updated_at, created_at) DESC LIMIT ?"
            params.append(candidate_limit)

            with self._connection(read_only=True) as conn:
                rows = conn.execute(sql, params).fetchall()
            results = self._apply_recency_ranking(rows, limit)
            if update_access and results:
                ts = self.touch_access([r["id"] for r in results])
                for r in results:
                    r["last_accessed"] = ts
                    r["access_count"] = (r.get("access_count") or 0) + 1
            return results

        tokens = [t for t in re.findall(r"[\w]+", clean_query) if t]
        if tokens:
            fts_match_query = " ".join(f'"{t}"' for t in tokens)
        else:
            fts_match_query = f'"{clean_query}"'

        base_sql = """
        SELECT m.id, m.path, m.title, m.type, m.summary, m.tags, m.content,
               m.hash, m.created_at, m.updated_at, m.priority, m.source_agent,
               m.project, m.supersedes, m.superseded_by, m.status, m.importance_score,
               m.access_count, m.last_accessed, m.archived_at,
               bm25(memories_fts) as rank
        FROM memories_fts
        JOIN memories m ON m.rowid = memories_fts.rowid
        WHERE memories_fts MATCH ?
          AND m.status != 'archived'
        """
        params = [fts_match_query]

        if memory_type:
            base_sql += " AND m.type = ?"
            params.append(memory_type)

        if tag:
            base_sql += " AND (m.tags LIKE ? OR m.tags LIKE ?)"
            params.extend([f'%"{tag}"%', f"%{tag}%"])

        if project:
            base_sql += " AND m.project = ?"
            params.append(project)

        base_sql += " ORDER BY rank ASC LIMIT ?"
        params.append(candidate_limit)

        and_count = 0
        with self._connection(read_only=True) as conn:
            try:
                cursor = conn.execute(base_sql, params)
                rows = cursor.fetchall()
                and_count = len(rows)
                if len(tokens) > 1 and len(rows) < limit:
                    or_query = " OR ".join(f'"{t}"' for t in tokens)
                    or_params = [or_query] + params[1:]
                    or_rows = conn.execute(base_sql, or_params).fetchall()
                    seen_ids = {r["id"] for r in rows}
                    for r in or_rows:
                        if r["id"] not in seen_ids:
                            rows.append(r)
                            seen_ids.add(r["id"])
            except sqlite3.OperationalError:
                fallback_sql = """
                SELECT *, 0.0 as rank FROM memories
                WHERE status != 'archived'
                  AND (title LIKE ? OR content LIKE ? OR summary LIKE ?)
                """
                fallback_params: list[Any] = [f"%{clean_query}%", f"%{clean_query}%", f"%{clean_query}%"]
                if memory_type:
                    fallback_sql += " AND type = ?"
                    fallback_params.append(memory_type)
                if tag:
                    fallback_sql += " AND (tags LIKE ? OR tags LIKE ?)"
                    fallback_params.extend([f'%"{tag}"%', f"%{tag}%"])
                if project:
                    fallback_sql += " AND project = ?"
                    fallback_params.append(project)
                fallback_sql += " ORDER BY COALESCE(last_accessed, updated_at, created_at) DESC LIMIT ?"
                fallback_params.append(candidate_limit)
                cursor = conn.execute(fallback_sql, fallback_params)
                rows = cursor.fetchall()

        results = self._apply_recency_ranking(rows, limit)
        if update_access and results:
            primary_type = results[0].get("type")
            touch_ids = [
                r["id"]
                for r in results[:max(1, and_count)]
                if not (primary_type == "memory/semantic" and r.get("type") == "memory/episodic")
            ]
            if not touch_ids:
                touch_ids = [results[0]["id"]]
            ts = self.touch_access(touch_ids)
            touch_set = set(touch_ids)
            for r in results:
                if r["id"] in touch_set:
                    r["last_accessed"] = ts
                    r["access_count"] = (r.get("access_count") or 0) + 1
        return results

    def read_memory(self, id_or_path: str, update_access: bool = True) -> dict[str, Any] | None:
        """Reads a memory by ID or relative path and optionally updates access recency."""
        select_sql = """
        SELECT * FROM memories
        WHERE id = ? OR path = ?
        LIMIT 1
        """

        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute(select_sql, (id_or_path, id_or_path))
                row = cursor.fetchone()
                if not row:
                    return None

                record = dict(row)
                if update_access:
                    new_count = (record.get("access_count") or 0) + 1
                    now_str = _utc_now_str()
                    update_sql = """
                    UPDATE memories
                    SET access_count = ?, last_accessed = ?
                    WHERE id = ?
                    """
                    with conn:
                        conn.execute(update_sql, (new_count, now_str, record["id"]))

                    record["access_count"] = new_count
                    record["last_accessed"] = now_str
                return record

    def update_memory_status(
        self,
        memory_id: str,
        status: str,
        superseded_by: str | None = None,
        supersedes: str | None = None,
    ) -> bool:
        """Updates the status and superseding references of a memory."""
        now_str = _utc_now_str()
        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(
                        """
                        UPDATE memories
                        SET status = ?,
                            superseded_by = COALESCE(?, superseded_by),
                            supersedes = COALESCE(?, supersedes),
                            updated_at = ?
                        WHERE id = ? OR path = ?
                        """,
                        (status, superseded_by, supersedes, now_str, memory_id, memory_id),
                    )
        return True

    def increment_access_count(self, memory_id: str) -> int:
        """Increments access count and updates last_accessed without mutating updated_at."""
        now_str = _utc_now_str()
        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(
                        "UPDATE memories SET access_count = COALESCE(access_count, 0) + 1, last_accessed = ? WHERE id = ? OR path = ?",
                        (now_str, memory_id, memory_id),
                    )
                    row = conn.execute(
                        "SELECT access_count FROM memories WHERE id = ? OR path = ?", (memory_id, memory_id)
                    ).fetchone()
                    return row[0] if row else 0

    def touch_access(self, memory_ids: list[str], timestamp: str | None = None) -> str:
        """Batch-increments access_count and updates last_accessed without mutating updated_at."""
        ts = timestamp or _utc_now_str()
        clean_ids = list(dict.fromkeys(mid for mid in memory_ids if mid))
        if not clean_ids:
            return ts

        placeholders = ",".join("?" for _ in clean_ids)
        sql = (
            f"UPDATE memories SET access_count = COALESCE(access_count, 0) + 1, "
            f"last_accessed = ? WHERE id IN ({placeholders})"
        )
        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(sql, [ts, *clean_ids])
        return ts

    def update_recency(
        self,
        memory_id: str,
        last_accessed: str | None = None,
        archived_at: str | None = None,
    ) -> bool:
        """Updates last_accessed and/or archived_at without touching updated_at or FTS5."""
        if last_accessed is None and archived_at is None:
            return False
        with self._lock:
            with self._connection() as conn:
                with conn:
                    cursor = conn.execute(
                        """
                        UPDATE memories
                        SET last_accessed = COALESCE(?, last_accessed),
                            archived_at = COALESCE(?, archived_at)
                        WHERE id = ? OR path = ?
                        """,
                        (last_accessed, archived_at, memory_id, memory_id),
                    )
                    return cursor.rowcount > 0

    def archive_memories(
        self,
        older_than_days: int = 90,
        max_access_count: int = 0,
        paths: list[str] | None = None,
        ids: list[str] | None = None,
        dry_run: bool = False,
        vault_path: str | Path | None = None,
        writable_inbox: str | None = None,
        read_only: bool = False,
    ) -> dict[str, Any]:
        """Explicitly archives inactive or specified memories to 40_Archive/.

        Never called implicitly during search, read, add, or sync.
        """
        vault_root = Path(vault_path).resolve() if vault_path else None
        inbox = str(writable_inbox).replace("\\", "/").strip("/") if writable_inbox else None
        inbox_root = (vault_root / inbox).resolve() if (vault_root and inbox) else None

        if read_only and not inbox and not dry_run:
            raise PermissionError(
                "Vault is configured in strict read-only mode without a writable inbox; archiving is disabled."
            )

        is_explicit = (paths is not None) or (ids is not None)
        explicit_paths = {p.replace("\\", "/").strip("/") for p in (paths or []) if p}
        explicit_ids = {str(i).strip() for i in (ids or []) if i}

        all_rows = self.get_all_memories(include_archived=True)
        existing_paths = {
            (r.get("path") or "").replace("\\", "/").strip("/")
            for r in all_rows
            if r.get("path")
        }

        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        cutoff_dt = now_dt - timedelta(days=int(older_than_days))

        archived_items: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []
        matched_explicit_paths: set[str] = set()
        matched_explicit_ids: set[str] = set()

        for row in all_rows:
            mem_id = row["id"]
            rel_path = (row.get("path") or "").replace("\\", "/").strip("/")
            mem_type = row.get("type") or ""
            status = row.get("status") or "active"

            if is_explicit:
                path_hit = rel_path in explicit_paths
                id_hit = mem_id in explicit_ids
                if not (path_hit or id_hit):
                    continue
                if path_hit:
                    matched_explicit_paths.add(rel_path)
                if id_hit:
                    matched_explicit_ids.add(mem_id)

            # Mandatory exclusions
            already_archived = (
                status == "archived"
                or rel_path.startswith("40_Archive/")
                or (inbox is not None and rel_path.startswith(f"{inbox}/40_Archive/"))
            )
            if already_archived:
                if is_explicit:
                    skipped_items.append({"id": mem_id, "path": rel_path, "reason": "already_archived"})
                continue

            is_project = mem_type == "project" or rel_path.startswith("10_Projects/")
            if is_project:
                if is_explicit:
                    skipped_items.append({"id": mem_id, "path": rel_path, "reason": "excluded_project"})
                continue

            is_system_or_crew = (
                rel_path == "crew_log.md"
                or rel_path.endswith("/crew_log.md")
                or rel_path.startswith("90_System/")
                or mem_type == "memory/system"
            )
            if is_system_or_crew:
                if is_explicit:
                    skipped_items.append({"id": mem_id, "path": rel_path, "reason": "excluded_system_or_crew_log"})
                continue

            if not is_explicit:
                access_count = row.get("access_count") or 0
                if access_count > int(max_access_count):
                    continue
                ref_ts = row.get("last_accessed") or row.get("updated_at") or row.get("created_at")
                ref_dt = _parse_utc_timestamp(ref_ts)
                if ref_dt is None or ref_dt > cutoff_dt:
                    continue

            # Enforce writable_inbox boundary
            if inbox is not None and not rel_path.startswith(f"{inbox}/"):
                skipped_items.append({"id": mem_id, "path": rel_path, "reason": "outside_writable_inbox"})
                continue

            # Validate path stays inside vault root
            if vault_root is not None:
                try:
                    src_resolved = (vault_root / rel_path).resolve()
                    src_resolved.relative_to(vault_root)
                    if inbox_root is not None:
                        src_resolved.relative_to(inbox_root)
                except ValueError:
                    skipped_items.append({"id": mem_id, "path": rel_path, "reason": "invalid_path"})
                    continue

            # Compute destination path under 40_Archive/
            if inbox is not None:
                sub = rel_path[len(inbox) + 1:]
                if sub.startswith("50_Memory/"):
                    sub = sub[len("50_Memory/"):]
                base_dest = f"{inbox}/40_Archive/{sub}"
            else:
                sub = rel_path[len("50_Memory/"):] if rel_path.startswith("50_Memory/") else rel_path
                base_dest = f"40_Archive/{sub}"

            # Collision-safe resolution
            new_rel = base_dest
            if new_rel in existing_paths or (vault_root is not None and (vault_root / new_rel).exists()):
                posix_dest = PurePosixPath(base_dest)
                stem = posix_dest.stem
                suffix = posix_dest.suffix or ".md"
                parent_str = "" if str(posix_dest.parent) == "." else f"{posix_dest.parent}/"
                candidate = f"{parent_str}{stem}_{mem_id[:6]}{suffix}"
                counter = 1
                while candidate in existing_paths or (vault_root is not None and (vault_root / candidate).exists()):
                    candidate = f"{parent_str}{stem}_{mem_id[:6]}_{counter}{suffix}"
                    counter += 1
                new_rel = candidate

            existing_paths.add(new_rel)

            if not dry_run:
                if vault_root is not None:
                    src_file = (vault_root / rel_path).resolve()
                    dest_file = (vault_root / new_rel).resolve()
                    dest_file.relative_to(vault_root)
                    if inbox_root is not None:
                        dest_file.relative_to(inbox_root)
                    if src_file.is_file():
                        raw_text = src_file.read_text(encoding="utf-8")
                        patched_text = _patch_frontmatter_text(
                            raw_text,
                            {"status": "archived", "archived_at": now_str},
                        )
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        dest_file.write_text(patched_text, encoding="utf-8")
                        if dest_file != src_file:
                            src_file.unlink()

                with self._lock:
                    with self._connection() as conn:
                        with conn:
                            conn.execute(
                                "UPDATE memories SET status = 'archived', path = ?, archived_at = ? WHERE id = ?",
                                (new_rel, now_str, mem_id),
                            )

            archived_items.append(
                {
                    "id": mem_id,
                    "title": row.get("title", ""),
                    "old_path": rel_path,
                    "new_path": new_rel,
                    "archived_at": now_str,
                }
            )

        if is_explicit:
            for p in sorted(explicit_paths - matched_explicit_paths):
                skipped_items.append({"id": None, "path": p, "reason": "not_found"})
            for i in sorted(explicit_ids - matched_explicit_ids):
                skipped_items.append({"id": i, "path": None, "reason": "not_found"})

        return {
            "archived": archived_items,
            "skipped": skipped_items,
            "count": len(archived_items),
            "dry_run": dry_run,
        }

    def delete_memory(self, id_or_path: str) -> bool:
        """Deletes a memory record. Cascades to relations and triggers FTS removal."""
        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(
                        "DELETE FROM memories WHERE id = ? OR path = ?",
                        (id_or_path, id_or_path),
                    )
        return True

    def get_all_memories(self, include_archived: bool = True) -> list[dict[str, Any]]:
        """Returns all memory records in the database."""
        sql = "SELECT * FROM memories"
        if not include_archived:
            sql += " WHERE status != 'archived'"
        sql += " ORDER BY updated_at DESC"
        with self._connection() as conn:
            cursor = conn.execute(sql)
            return [dict(row) for row in cursor.fetchall()]

    def get_memory_by_path(self, path: str) -> dict[str, Any] | None:
        """Retrieves a single memory by exact relative path."""
        with self._connection() as conn:
            cursor = conn.execute("SELECT * FROM memories WHERE path = ? LIMIT 1", (path,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_stats(self) -> dict[str, Any]:
        """Calculates storage metrics, cognitive layer breakdown, and WAL status."""
        with self._connection() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            wal_enabled = (journal_mode.lower() == "wal")
            total_count = conn.execute("SELECT COUNT(*) FROM memories;").fetchone()[0]

            cursor_types = conn.execute("SELECT type, COUNT(*) FROM memories GROUP BY type;")
            by_type = {row[0]: row[1] for row in cursor_types.fetchall()}

            cursor_status = conn.execute("SELECT status, COUNT(*) FROM memories GROUP BY status;")
            by_status = {row[0]: row[1] for row in cursor_status.fetchall()}

            db_size = os.path.getsize(self.db_path) if self.db_path.exists() else 0

        return {
            "total_memories": total_count,
            "by_type": by_type,
            "by_status": by_status,
            "wal_enabled": wal_enabled,
            "db_size_bytes": db_size,
            "db_path": str(self.db_path),
        }

    def _format_markdown_note(self, record: dict[str, Any]) -> str:
        """Formats a memory record as a Markdown file with YAML frontmatter."""
        frontmatter_fields = ["title", "type", "status", "priority", "project", "tags",
                              "created_at", "updated_at", "source_agent", "importance_score",
                              "access_count", "last_accessed", "archived_at"]
        content = record.get("content", "")

        import sys
        print(f"[DEBUG _format_markdown_note] content length: {len(content)}, starts with: {content[:50]}", file=sys.stderr)

        fm_lines = ["---"]
        for key in frontmatter_fields:
            val = record.get(key)
            if val is not None:
                if isinstance(val, list):
                    items = ", ".join(f'"{v}"' if " " in str(v) else str(v) for v in val)
                    fm_lines.append(f"{key}: [{items}]")
                else:
                    fm_lines.append(f'{key}: "{val}"')
        fm_lines.append("---")

        return "\n".join(fm_lines) + "\n\n" + content

    def write_crew_log(
        self,
        agent: str,
        activity: str,
        files: list[str] | None = None,
        open_items: list[str] | None = None,
        project: str | None = None,
        writable_inbox: str | None = None,
    ) -> dict[str, Any]:
        """Appends a handoff entry to the crew log Markdown file."""
        files = files or []
        open_items = open_items or []
        now_str = _utc_now_str()

        # Sanitize all inputs
        clean_agent = Sanitizer.clean(agent).strip()
        clean_activity = Sanitizer.clean(activity).strip()
        clean_files = [Sanitizer.clean(f).strip() for f in files]
        clean_open_items = [Sanitizer.clean(o).strip() for o in open_items]
        clean_project = Sanitizer.clean(project).strip() if project else None

        # Build the entry
        entry_lines = [
            f"### {now_str} — {clean_agent}",
            f"**Activity:** {clean_activity}",
        ]
        if clean_files:
            entry_lines.append(f"**Files:** {', '.join(clean_files)}")
        if clean_open_items:
            entry_lines.append(f"**Open Items:** {', '.join(clean_open_items)}")
        if clean_project:
            entry_lines.append(f"**Project:** {clean_project}")
        entry_lines.append("")  # blank line separator

        entry_text = "\n".join(entry_lines)

        # Ensure directory exists and append
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Use vault_path when available, otherwise derive from db_path
        if self.vault_path:
            vault_root = self.vault_path
        else:
            vault_root = self.db_path.parent.parent.parent  # Go up from 90_System/db/ to vault root

        # Determine crew log path based on writable_inbox
        if writable_inbox:
            crew_log_relative_path = f"{writable_inbox}/Working/crew_log.md"
        else:
            crew_log_relative_path = "50_Memory/Working/crew_log.md"
        crew_log_path = Path(crew_log_relative_path)
        crew_log_file = vault_root / crew_log_path

        if not crew_log_file.exists():
            # Create from template
            template_content = """---
title: Crew Handoff Logbook
type: memory/working
status: active
created_at: {now_str}
updated_at: {now_str}
---

# Crew Handoff Logbook

Chronological handoff log for multi-agent collaboration. Each entry records the agent, activity performed, files touched, and open items for the next agent.

## Handoff Entries

<!-- Entries are appended chronologically by brain_log_handoff tool -->
""".format(now_str=now_str)
            crew_log_file.parent.mkdir(parents=True, exist_ok=True)
            crew_log_file.write_text(template_content, encoding="utf-8")

        # Append entry before the closing comment or at end of file
        content = crew_log_file.read_text(encoding="utf-8")
        # Find the position to insert (before the closing HTML comment or at end of "## Handoff Entries" section)
        insert_marker = "<!-- Entries are appended chronologically by brain_log_handoff tool -->"
        if insert_marker in content:
            # Insert before the marker
            content = content.replace(insert_marker, f"{entry_text}\n{insert_marker}")
        else:
            # Append at end of file
            content = content.rstrip() + "\n\n" + entry_text + "\n"

        crew_log_file.write_text(content, encoding="utf-8")

        # Also update the file's updated_at frontmatter
        upsert_frontmatter_fields(crew_log_file, {"updated_at": now_str})

        return {
            "path": str(crew_log_relative_path),
            "entry": {
                "timestamp": now_str,
                "agent": clean_agent,
                "activity": clean_activity,
                "files": clean_files,
                "open_items": clean_open_items,
                "project": clean_project,
            },
        }

    def read_crew_log(self, limit: int | None = None, writable_inbox: str | None = None) -> dict[str, Any]:
        """Reads the crew log Markdown file, returning the most recent entries."""
        # Determine crew log path based on writable_inbox
        if writable_inbox:
            crew_log_relative_path = f"{writable_inbox}/Working/crew_log.md"
        else:
            crew_log_relative_path = "50_Memory/Working/crew_log.md"
        crew_log_path = Path(crew_log_relative_path)
        # Use vault_path when available, otherwise derive from db_path
        if self.vault_path:
            vault_root = self.vault_path
        else:
            vault_root = self.db_path.parent.parent.parent  # Go up from 90_System/db/ to vault root
        crew_log_file = vault_root / crew_log_path

        if not crew_log_file.exists():
            return {"path": str(crew_log_path), "entries": [], "content": ""}

        content = crew_log_file.read_text(encoding="utf-8")

        # Parse entries - each starts with "### YYYY-MM-DD HH:MM:SS — Agent"
        entry_pattern = re.compile(
            r"^###\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+[—-]\s+(.+)$",
            re.MULTILINE,
        )
        matches = list(entry_pattern.finditer(content))

        entries = []
        for i, match in enumerate(matches):
            timestamp = match.group(1).strip()
            agent = match.group(2).strip()
            # Get content until next entry or end
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            entry_body = content[start:end].strip()

            # Parse fields
            activity = ""
            files = []
            open_items = []
            project = None

            for line in entry_body.splitlines():
                line = line.strip()
                if line.startswith("**Activity:**"):
                    activity = line[len("**Activity:**"):].strip()
                elif line.startswith("**Files:**"):
                    files = [f.strip() for f in line[len("**Files:**"):].split(",") if f.strip()]
                elif line.startswith("**Open Items:**"):
                    open_items = [o.strip() for o in line[len("**Open Items:**"):].split(",") if o.strip()]
                elif line.startswith("**Project:**"):
                    project = line[len("**Project:**"):].strip()

            entries.append({
                "timestamp": timestamp,
                "agent": agent,
                "activity": activity,
                "files": files,
                "open_items": open_items,
                "project": project,
            })

        # Reverse to get most recent first (they're in chronological order in file)
        entries = list(reversed(entries))

        if limit is not None and limit > 0:
            entries = entries[:limit]

        return {
            "path": str(crew_log_path),
            "entries": entries,
            "content": content,
        }

    def add_relation(self, source_id: str, target_id: str, relation_type: str = "relates_to") -> bool:
        """Adds a directional relation between two memories in the knowledge graph."""
        sql = """
        INSERT OR IGNORE INTO memory_relations (source_id, target_id, relation_type)
        VALUES (?, ?, ?)
        """
        with self._lock:
            with self._connection() as conn:
                with conn:
                    conn.execute(sql, (source_id, target_id, relation_type))
        return True

    def get_relations(self, memory_id: str) -> list[dict[str, Any]]:
        """Retrieves all relations associated with a memory."""
        sql = """
        SELECT * FROM memory_relations
        WHERE source_id = ? OR target_id = ?
        """
        with self._connection() as conn:
            cursor = conn.execute(sql, (memory_id, memory_id))
            return [dict(row) for row in cursor.fetchall()]
