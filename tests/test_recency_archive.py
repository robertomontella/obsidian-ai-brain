"""Unit tests for M1: Recency tracking, cognitive ranking, explicit archiving, sync_db, and MCP aliases."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mcp-server"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from server import McpServer
from storage import StorageManager, upsert_frontmatter_fields
from sync_db import parse_frontmatter, sync_vault


class TestRecencyAndArchive(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.vault_path = self.root / "vault"
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.vault_path / "90_System" / "db" / "brain_memory.sqlite"
        self.storage = StorageManager(db_path=self.db_path)
        self.storage.init_db()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _set_db_timestamps(
        self,
        memory_id: str,
        *,
        created_at: str | None = None,
        updated_at: str | None = None,
        last_accessed: str | None = None,
        access_count: int | None = None,
    ) -> None:
        with self.storage._connection() as conn:
            with conn:
                if created_at is not None:
                    conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (created_at, memory_id))
                if updated_at is not None:
                    conn.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (updated_at, memory_id))
                conn.execute("UPDATE memories SET last_accessed = ? WHERE id = ?", (last_accessed, memory_id))
                if access_count is not None:
                    conn.execute("UPDATE memories SET access_count = ? WHERE id = ?", (access_count, memory_id))

    def test_schema_and_idempotent_migration(self) -> None:
        """Legacy DB without last_accessed/archived_at and with unscoped memories_au migrates cleanly."""
        legacy_db = self.root / "legacy.sqlite"
        conn = sqlite3.connect(str(legacy_db))
        conn.executescript(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                summary TEXT,
                tags TEXT,
                content TEXT,
                hash TEXT NOT NULL,
                created_at TEXT,
                updated_at TEXT,
                priority TEXT DEFAULT 'Media',
                source_agent TEXT,
                project TEXT,
                supersedes TEXT,
                superseded_by TEXT,
                status TEXT DEFAULT 'active',
                importance_score REAL DEFAULT 3.0,
                access_count INTEGER DEFAULT 0
            );
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                title, content, tags, summary,
                content='memories', content_rowid='rowid', tokenize='porter unicode61'
            );
            CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, title, content, tags, summary)
                VALUES('delete', old.rowid, old.title, old.content, old.tags, old.summary);
                INSERT INTO memories_fts(rowid, title, content, tags, summary)
                VALUES (new.rowid, new.title, new.content, new.tags, new.summary);
            END;
            INSERT INTO memories (id, path, title, type, content, hash, created_at, updated_at, access_count)
            VALUES ('legacy000001', '50_Memory/Semantic/Legacy.md', 'Legacy Note', 'memory/semantic',
                    'Legacy content body', 'abc123', '2025-01-01 00:00:00', '2025-02-01 00:00:00', 3);
            INSERT INTO memories_fts(rowid, title, content, tags, summary)
            SELECT rowid, title, content, tags, summary FROM memories;
            """
        )
        conn.commit()
        conn.close()

        migrated = StorageManager(db_path=legacy_db)
        migrated.init_db()
        # Running init_db a second time must remain idempotent
        migrated.init_db()

        with migrated._connection() as c:
            cols = {row["name"] for row in c.execute("PRAGMA table_info(memories)").fetchall()}
            self.assertIn("last_accessed", cols)
            self.assertIn("archived_at", cols)

            trig = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='memories_au'"
            ).fetchone()
            self.assertIsNotNone(trig)
            self.assertIn("UPDATE OF", trig["sql"].upper())

            row = c.execute("SELECT * FROM memories WHERE id = 'legacy000001'").fetchone()
            self.assertEqual(row["last_accessed"], "2025-02-01 00:00:00")
            self.assertEqual(row["access_count"], 3)

        # Search still finds migrated legacy record
        results = migrated.search_memory("Legacy content")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "legacy000001")

    def test_read_and_increment_update_last_accessed_not_updated_at(self) -> None:
        """read_memory and increment_access_count update last_accessed without mutating updated_at."""
        rec = self.storage.add_memory(
            title="Immutable UpdatedAt",
            content="Testing separation of updated_at and last_accessed.",
            memory_type="memory/semantic",
        )
        old_ts = "2025-05-01 10:00:00"
        self._set_db_timestamps(rec["id"], created_at=old_ts, updated_at=old_ts, last_accessed=old_ts, access_count=0)

        read_rec = self.storage.read_memory(rec["id"])
        self.assertIsNotNone(read_rec)
        self.assertEqual(read_rec["access_count"], 1)
        self.assertEqual(read_rec["updated_at"], old_ts)
        self.assertNotEqual(read_rec["last_accessed"], old_ts)

        new_count = self.storage.increment_access_count(rec["id"])
        self.assertEqual(new_count, 2)

        pure_read = self.storage.read_memory(rec["id"], update_access=False)
        self.assertEqual(pure_read["access_count"], 2)
        self.assertEqual(pure_read["updated_at"], old_ts)

    def test_mcp_retrieval_updates_disk_frontmatter_when_writable(self) -> None:
        """brain_read_memory and brain_search_memory patch disk frontmatter last_accessed without altering body hash."""
        server = McpServer(storage=self.storage, vault_path=self.vault_path)
        add_res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "brain_add_memory",
                    "arguments": {
                        "title": "Writable Retrieval Test",
                        "content": "Unique token alpha-omega-recency for FTS5 retrieval.",
                        "memory_type": "memory/semantic",
                    },
                },
            }
        )
        added = json.loads(add_res["result"]["content"][0]["text"])
        md_file = self.vault_path / added["path"]
        self.assertTrue(md_file.is_file())

        # Baseline sync so DB hash reflects disk Markdown body (with H1 header)
        sync_vault(vault_path=self.vault_path, storage=self.storage)

        old_ts = "2025-03-10 08:00:00"
        self._set_db_timestamps(added["id"], created_at=old_ts, updated_at=old_ts, last_accessed=old_ts, access_count=0)
        upsert_frontmatter_fields(md_file, {"updated_at": old_ts, "last_accessed": old_ts})

        # Search updates last_accessed on SQLite and disk
        search_res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "brain_search_memory",
                    "arguments": {"query": "alpha-omega-recency", "limit": 5},
                },
            }
        )
        results = json.loads(search_res["result"]["content"][0]["text"])
        self.assertEqual(len(results), 1)
        search_ts = results[0]["last_accessed"]
        self.assertNotEqual(search_ts, old_ts)

        meta_after_search, body_after_search = parse_frontmatter(md_file.read_text(encoding="utf-8"))
        self.assertEqual(str(meta_after_search.get("last_accessed")), search_ts)
        self.assertEqual(str(meta_after_search.get("updated_at")), old_ts)
        self.assertIn("Unique token alpha-omega-recency", body_after_search)

        # Force older timestamp before read to verify brain_read_memory also updates frontmatter
        stale_ts = "2025-04-01 12:00:00"
        self._set_db_timestamps(added["id"], updated_at=old_ts, last_accessed=stale_ts)
        upsert_frontmatter_fields(md_file, {"last_accessed": stale_ts})

        read_res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "brain_read_memory",
                    "arguments": {"path": added["path"]},
                },
            }
        )
        read_data = json.loads(read_res["result"]["content"][0]["text"])
        self.assertNotEqual(read_data["last_accessed"], stale_ts)
        self.assertEqual(read_data["updated_at"], old_ts)

        meta_after_read, _ = parse_frontmatter(md_file.read_text(encoding="utf-8"))
        self.assertEqual(str(meta_after_read.get("last_accessed")), read_data["last_accessed"])
        self.assertEqual(str(meta_after_read.get("updated_at")), old_ts)

        # sync_vault must see the note as unchanged (body hash intact)
        sync_summary = sync_vault(vault_path=self.vault_path, storage=self.storage)
        self.assertEqual(len(sync_summary["updated"]), 0)
        self.assertEqual(sync_summary["unchanged"], 1)

    def test_mcp_retrieval_respects_read_only_and_writable_inbox(self) -> None:
        """read_only=True and writable_inbox protect non-writable Markdown files from disk edits on retrieval."""
        user_dir = self.vault_path / "Notes"
        user_dir.mkdir(parents=True, exist_ok=True)
        user_note = user_dir / "External.md"
        original_text = (
            "---\n"
            "title: External User Note\n"
            "type: resource\n"
            "updated_at: 2025-01-01 00:00:00\n"
            "---\n\n"
            "# External User Note\n"
            "External keyword zephyr-isolation-check.\n"
        )
        user_note.write_text(original_text, encoding="utf-8")
        sync_vault(vault_path=self.vault_path, storage=self.storage)

        # 1. Strict read_only=True
        ro_server = McpServer(storage=self.storage, vault_path=self.vault_path, read_only=True)
        mtime_before = user_note.stat().st_mtime
        ro_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "brain_search_memory", "arguments": {"query": "zephyr-isolation-check"}},
            }
        )
        ro_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "brain_read_memory", "arguments": {"path": "Notes/External.md"}},
            }
        )
        self.assertEqual(user_note.read_text(encoding="utf-8"), original_text)
        self.assertEqual(user_note.stat().st_mtime, mtime_before)
        db_rec = self.storage.get_memory_by_path("Notes/External.md")
        self.assertEqual(db_rec["access_count"], 2)

        # 2. Scoped writable_inbox="AI-Memory"
        scoped_server = McpServer(storage=self.storage, vault_path=self.vault_path, writable_inbox="AI-Memory")
        add_res = scoped_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "brain_add_memory",
                    "arguments": {
                        "title": "Inbox Retrieval Note",
                        "content": "Inbox keyword zephyr-isolation-check.",
                    },
                },
            }
        )
        inbox_data = json.loads(add_res["result"]["content"][0]["text"])
        inbox_file = self.vault_path / inbox_data["path"]
        self._set_db_timestamps(inbox_data["id"], last_accessed="2025-01-01 00:00:00")
        upsert_frontmatter_fields(inbox_file, {"last_accessed": "2025-01-01 00:00:00"})

        scoped_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {"name": "brain_search_memory", "arguments": {"query": "zephyr-isolation-check"}},
            }
        )
        # External user note still untouched on disk
        self.assertEqual(user_note.read_text(encoding="utf-8"), original_text)
        # Inbox note updated on disk
        inbox_meta, _ = parse_frontmatter(inbox_file.read_text(encoding="utf-8"))
        self.assertNotEqual(str(inbox_meta.get("last_accessed")), "2025-01-01 00:00:00")

    def test_recency_weighted_ranking_and_layer_halflife(self) -> None:
        """Equal-BM25 notes prioritize recent last_accessed; Working decays faster than Semantic."""
        now_dt = datetime.now(timezone.utc)
        recent_str = (now_dt - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        stale_str = (now_dt - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")

        stale = self.storage.add_memory(
            title="Cache Design Pattern",
            content="Distributed cache invalidation strategy for microservices.",
            memory_type="memory/semantic",
            path="50_Memory/Semantic/StaleCache.md",
        )
        recent = self.storage.add_memory(
            title="Cache Design Pattern V2",
            content="Distributed cache invalidation strategy for microservices.",
            memory_type="memory/semantic",
            path="50_Memory/Semantic/RecentCache.md",
        )
        self._set_db_timestamps(stale["id"], updated_at=stale_str, last_accessed=stale_str)
        self._set_db_timestamps(recent["id"], updated_at=recent_str, last_accessed=recent_str)

        # Default search_memory must NOT mutate access_count
        results = self.storage.search_memory("Distributed cache invalidation strategy", limit=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], recent["id"])
        self.assertGreater(results[0]["recency_score"], results[1]["recency_score"])
        self.assertEqual(self.storage.get_memory_by_path(recent["path"])["access_count"], 0)

        # Cognitive half-life comparison at identical 14-day age: Semantic (365d) > Working (7d)
        same_age = (now_dt - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        working = self.storage.add_memory(
            title="Session Token Note",
            content="Identical cognitive decay benchmark token halflife-check.",
            memory_type="memory/working",
            path="50_Memory/Working/HL_Working.md",
        )
        semantic = self.storage.add_memory(
            title="LongTerm Token Note",
            content="Identical cognitive decay benchmark token halflife-check.",
            memory_type="memory/semantic",
            path="50_Memory/Semantic/HL_Semantic.md",
        )
        self._set_db_timestamps(working["id"], updated_at=same_age, last_accessed=same_age)
        self._set_db_timestamps(semantic["id"], updated_at=same_age, last_accessed=same_age)

        hl_results = self.storage.search_memory("halflife-check", limit=5)
        self.assertEqual(hl_results[0]["id"], semantic["id"])
        self.assertGreater(hl_results[0]["recency_score"], hl_results[1]["recency_score"])

        # Fallback when last_accessed is NULL: uses updated_at
        self._set_db_timestamps(working["id"], updated_at=recent_str, last_accessed=None)
        fallback_results = self.storage.search_memory("halflife-check", limit=5)
        self.assertEqual(fallback_results[0]["id"], working["id"])

    def test_search_latency_stays_under_5ms(self) -> None:
        """Recency-weighted search_memory keeps latency strictly under 5.0 ms."""
        for i in range(60):
            self.storage.add_memory(
                title=f"Performance Benchmark {i}",
                content=f"SQLite FTS5 WAL recency benchmark entry number {i} with concurrent readers.",
                memory_type="memory/semantic" if i % 2 == 0 else "memory/working",
                tags=["perf", "sqlite"],
            )

        # Warm-up query
        self.storage.search_memory(query="recency benchmark", limit=5)

        start = time.perf_counter()
        results = self.storage.search_memory(query="recency benchmark concurrent", limit=5)
        duration_ms = (time.perf_counter() - start) * 1000.0

        self.assertLess(duration_ms, 5.0, f"Search took {duration_ms:.2f}ms, expected < 5.0ms")
        self.assertEqual(len(results), 5)

    def test_zero_implicit_archiving_on_normal_operations(self) -> None:
        """search, auto_retrieve, read, add, auto_capture, and sync never archive old notes implicitly."""
        server = McpServer(storage=self.storage, vault_path=self.vault_path)
        old_ts = "2024-01-01 00:00:00"

        old_ep = self.vault_path / "50_Memory" / "Episodic" / "AncientEpisodic.md"
        old_ep.parent.mkdir(parents=True, exist_ok=True)
        old_ep.write_text(
            f"---\ntitle: Ancient Episodic\ntype: memory/episodic\nstatus: active\ncreated_at: {old_ts}\nupdated_at: {old_ts}\nlast_accessed: {old_ts}\n---\n\nAncient episodic event implicit-guard-token.\n",
            encoding="utf-8",
        )
        old_sem = self.vault_path / "50_Memory" / "Semantic" / "AncientSemantic.md"
        old_sem.parent.mkdir(parents=True, exist_ok=True)
        old_sem.write_text(
            f"---\ntitle: Ancient Semantic\ntype: memory/semantic\nstatus: active\ncreated_at: {old_ts}\nupdated_at: {old_ts}\nlast_accessed: {old_ts}\n---\n\nAncient semantic fact implicit-guard-token.\n",
            encoding="utf-8",
        )

        # Run sync, search, auto_retrieve, read, add, auto_capture
        server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "brain_sync", "arguments": {}}})
        server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "brain_search_memory", "arguments": {"query": "implicit-guard-token"}}})
        server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "brain_auto_retrieve", "arguments": {"prompt": "implicit-guard-token"}}})
        server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "brain_read_memory", "arguments": {"path": "50_Memory/Episodic/AncientEpisodic.md"}}})
        server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "brain_add_memory", "arguments": {"title": "Fresh Note", "content": "New data"}}})
        server.handle_request({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "brain_auto_capture", "arguments": {"text": "Captured insight"}}})

        self.assertTrue(old_ep.is_file())
        self.assertTrue(old_sem.is_file())
        archive_dir = self.vault_path / "40_Archive"
        archived_files = list(archive_dir.rglob("*.md")) if archive_dir.exists() else []
        self.assertEqual(len(archived_files), 0)
        self.assertEqual(self.storage.get_memory_by_path("50_Memory/Episodic/AncientEpisodic.md")["status"], "active")
        self.assertEqual(self.storage.get_memory_by_path("50_Memory/Semantic/AncientSemantic.md")["status"], "active")

    def test_explicit_archive_dry_run_and_criteria(self) -> None:
        """brain_archive_memories archives only qualifying stale notes, supports dry_run, and excludes projects/crew_log."""
        server = McpServer(storage=self.storage, vault_path=self.vault_path)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")
        recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # 1. Stale never-accessed note (SHOULD archive)
        stale_file = self.vault_path / "50_Memory" / "Episodic" / "OldEvent.md"
        stale_file.parent.mkdir(parents=True, exist_ok=True)
        stale_file.write_text(
            f"---\ntitle: Old Event\ntype: memory/episodic\nstatus: active\nupdated_at: {old_ts}\nlast_accessed: {old_ts}\n---\n\n# Old Event\nStale body content archive-target-unique.\n",
            encoding="utf-8",
        )
        # 2. Stale BUT accessed note (access_count=1 > max_access_count=0 -> SHOULD NOT archive)
        accessed_file = self.vault_path / "50_Memory" / "Semantic" / "AccessedOld.md"
        accessed_file.parent.mkdir(parents=True, exist_ok=True)
        accessed_file.write_text(
            f"---\ntitle: Accessed Old\ntype: memory/semantic\nstatus: active\nupdated_at: {old_ts}\nlast_accessed: {old_ts}\n---\n\nAccessed body.\n",
            encoding="utf-8",
        )
        # 3. Recent note (SHOULD NOT archive)
        recent_file = self.vault_path / "50_Memory" / "Working" / "RecentNote.md"
        recent_file.parent.mkdir(parents=True, exist_ok=True)
        recent_file.write_text(
            f"---\ntitle: Recent Note\ntype: memory/working\nstatus: active\nupdated_at: {recent_ts}\nlast_accessed: {recent_ts}\n---\n\nRecent body.\n",
            encoding="utf-8",
        )
        # 4. Old Project note (MUST be excluded)
        proj_file = self.vault_path / "10_Projects" / "CoreProject.md"
        proj_file.parent.mkdir(parents=True, exist_ok=True)
        proj_file.write_text(
            f"---\ntitle: Core Project\ntype: project\nstatus: active\nupdated_at: {old_ts}\nlast_accessed: {old_ts}\n---\n\nProject spec.\n",
            encoding="utf-8",
        )
        # 5. Old crew_log.md (MUST be excluded)
        crew_file = self.vault_path / "50_Memory" / "Working" / "crew_log.md"
        crew_file.write_text(
            f"---\ntitle: Crew Log\ntype: memory/working\nstatus: active\nupdated_at: {old_ts}\nlast_accessed: {old_ts}\n---\n\nLog entries.\n",
            encoding="utf-8",
        )

        sync_vault(vault_path=self.vault_path, storage=self.storage)
        acc_rec = self.storage.get_memory_by_path("50_Memory/Semantic/AccessedOld.md")
        self._set_db_timestamps(acc_rec["id"], updated_at=old_ts, last_accessed=old_ts, access_count=2)

        # Dry run first
        dry_res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "brain_archive_memories",
                    "arguments": {"older_than_days": 90, "max_access_count": 0, "dry_run": True},
                },
            }
        )
        dry_data = json.loads(dry_res["result"]["content"][0]["text"])
        self.assertTrue(dry_data["dry_run"])
        self.assertEqual(dry_data["count"], 1)
        self.assertEqual(dry_data["archived"][0]["old_path"], "50_Memory/Episodic/OldEvent.md")
        self.assertEqual(dry_data["archived"][0]["new_path"], "40_Archive/Episodic/OldEvent.md")
        self.assertTrue(stale_file.is_file())

        # Actual archive execution
        exec_res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "brain_archive_memories",
                    "arguments": {"older_than_days": 90, "max_access_count": 0, "dry_run": False},
                },
            }
        )
        exec_data = json.loads(exec_res["result"]["content"][0]["text"])
        self.assertFalse(exec_data["dry_run"])
        self.assertEqual(exec_data["count"], 1)

        archived_dest = self.vault_path / "40_Archive" / "Episodic" / "OldEvent.md"
        self.assertFalse(stale_file.exists())
        self.assertTrue(archived_dest.is_file())
        self.assertTrue(accessed_file.is_file())
        self.assertTrue(recent_file.is_file())
        self.assertTrue(proj_file.is_file())
        self.assertTrue(crew_file.is_file())

        arch_meta, arch_body = parse_frontmatter(archived_dest.read_text(encoding="utf-8"))
        self.assertEqual(arch_meta.get("status"), "archived")
        self.assertTrue(bool(arch_meta.get("archived_at")))
        self.assertEqual(str(arch_meta.get("updated_at")), old_ts)
        self.assertIn("Stale body content archive-target-unique.", arch_body)

        # Archived notes are excluded from search_memory
        self.assertEqual(self.storage.search_memory("archive-target-unique"), [])
        db_arch = self.storage.get_memory_by_path("40_Archive/Episodic/OldEvent.md")
        self.assertIsNotNone(db_arch)
        self.assertEqual(db_arch["status"], "archived")

    def test_explicit_archive_by_paths_ids_and_collision_safety(self) -> None:
        """Explicit paths/ids archive works and avoids overwriting existing files in 40_Archive/."""
        server = McpServer(storage=self.storage, vault_path=self.vault_path)

        # Existing note already in 40_Archive/Semantic/Collision.md
        existing_arch = self.vault_path / "40_Archive" / "Semantic" / "Collision.md"
        existing_arch.parent.mkdir(parents=True, exist_ok=True)
        existing_arch.write_text("---\ntitle: Old Collision\nstatus: archived\n---\n\nKeep me intact.\n", encoding="utf-8")

        active_note = self.vault_path / "50_Memory" / "Semantic" / "Collision.md"
        active_note.parent.mkdir(parents=True, exist_ok=True)
        active_note.write_text("---\ntitle: Active Collision\nstatus: active\n---\n\nNewer body.\n", encoding="utf-8")

        proj_note = self.vault_path / "10_Projects" / "ProtectedProj.md"
        proj_note.parent.mkdir(parents=True, exist_ok=True)
        proj_note.write_text("---\ntitle: Protected Proj\ntype: project\nstatus: active\n---\n\nProj body.\n", encoding="utf-8")

        sync_vault(vault_path=self.vault_path, storage=self.storage)
        active_rec = self.storage.get_memory_by_path("50_Memory/Semantic/Collision.md")

        res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {
                    "name": "brain_archive_memories",
                    "arguments": {
                        "ids": [active_rec["id"]],
                        "paths": ["10_Projects/ProtectedProj.md", "40_Archive/Semantic/Collision.md"],
                    },
                },
            }
        )
        data = json.loads(res["result"]["content"][0]["text"])
        self.assertEqual(data["count"], 1)
        new_path = data["archived"][0]["new_path"]
        self.assertNotEqual(new_path, "40_Archive/Semantic/Collision.md")
        self.assertTrue(new_path.startswith("40_Archive/Semantic/Collision_"))
        self.assertTrue((self.vault_path / new_path).is_file())
        self.assertIn("Keep me intact.", existing_arch.read_text(encoding="utf-8"))

        skip_reasons = {s["path"]: s["reason"] for s in data["skipped"]}
        self.assertEqual(skip_reasons.get("10_Projects/ProtectedProj.md"), "excluded_project")
        self.assertEqual(skip_reasons.get("40_Archive/Semantic/Collision.md"), "already_archived")

    def test_archive_respects_read_only_and_writable_inbox(self) -> None:
        """Archive rejects read_only=True (unless dry_run) and respects writable_inbox boundaries."""
        # 1. Read-only guard
        ro_server = McpServer(storage=self.storage, vault_path=self.vault_path, read_only=True)
        ro_res = ro_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 40,
                "method": "tools/call",
                "params": {"name": "brain_archive_memories", "arguments": {"older_than_days": 0}},
            }
        )
        self.assertTrue(ro_res["result"]["isError"])
        with self.assertRaises(PermissionError):
            self.storage.archive_memories(older_than_days=0, read_only=True, vault_path=self.vault_path)

        # 2. Writable inbox confinement
        ext_file = self.vault_path / "Notes" / "UserOld.md"
        ext_file.parent.mkdir(parents=True, exist_ok=True)
        ext_file.write_text("---\ntitle: User Old\nlast_accessed: 2024-01-01 00:00:00\n---\n\nUser note.\n", encoding="utf-8")

        inbox_file = self.vault_path / "AI-Memory" / "Episodic" / "InboxOld.md"
        inbox_file.parent.mkdir(parents=True, exist_ok=True)
        inbox_file.write_text("---\ntitle: Inbox Old\nlast_accessed: 2024-01-01 00:00:00\n---\n\nInbox note.\n", encoding="utf-8")

        sync_vault(vault_path=self.vault_path, storage=self.storage)

        scoped_server = McpServer(storage=self.storage, vault_path=self.vault_path, writable_inbox="AI-Memory", read_only=True)
        res = scoped_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 41,
                "method": "tools/call",
                "params": {"name": "brain_archive_memories", "arguments": {"older_than_days": 90}},
            }
        )
        data = json.loads(res["result"]["content"][0]["text"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["archived"][0]["new_path"], "AI-Memory/40_Archive/Episodic/InboxOld.md")
        self.assertTrue((self.vault_path / "AI-Memory" / "40_Archive" / "Episodic" / "InboxOld.md").is_file())
        self.assertTrue(ext_file.is_file())
        self.assertEqual(self.storage.get_memory_by_path("Notes/UserOld.md")["status"], "active")
        self.assertTrue(any(s["path"] == "Notes/UserOld.md" and s["reason"] == "outside_writable_inbox" for s in data["skipped"]))

    def test_sync_db_recency_and_archive_roundtrip(self) -> None:
        """sync_vault persists frontmatter last_accessed/archived_at and keeps newer SQLite recency."""
        note = self.vault_path / "50_Memory" / "Semantic" / "SyncRecency.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "---\n"
            "title: Sync Recency\n"
            "type: memory/semantic\n"
            "status: active\n"
            "last_accessed: 2026-01-15\n"
            "---\n\n"
            "# Sync Recency\nBody text for sync roundtrip.\n",
            encoding="utf-8",
        )
        sync_vault(vault_path=self.vault_path, storage=self.storage)
        rec = self.storage.get_memory_by_path("50_Memory/Semantic/SyncRecency.md")
        self.assertEqual(rec["last_accessed"], "2026-01-15 00:00:00")

        # Simulate read-only DB access advancing SQLite last_accessed beyond disk frontmatter
        newer_db_ts = "2026-06-01 12:30:00"
        self._set_db_timestamps(rec["id"], updated_at="2026-01-15 00:00:00", last_accessed=newer_db_ts)
        sync_vault(vault_path=self.vault_path, storage=self.storage)
        rec_after = self.storage.get_memory_by_path("50_Memory/Semantic/SyncRecency.md")
        self.assertEqual(rec_after["last_accessed"], newer_db_ts)

        # Now disk frontmatter gets an even newer timestamp (e.g. edited externally)
        upsert_frontmatter_fields(note, {"last_accessed": "2026-08-01T09:15:00Z"})
        sync_summary = sync_vault(vault_path=self.vault_path, storage=self.storage)
        self.assertEqual(sync_summary["unchanged"], 1)
        rec_updated = self.storage.get_memory_by_path("50_Memory/Semantic/SyncRecency.md")
        self.assertEqual(rec_updated["last_accessed"], "2026-08-01 09:15:00")
        self.assertEqual(rec_updated["updated_at"], "2026-01-15 00:00:00")

    def test_mcp_aliases_auto_capture_and_auto_retrieve(self) -> None:
        """tools/list exposes new tools and brain_auto_capture/brain_auto_retrieve work with alias args."""
        server = McpServer(storage=self.storage, vault_path=self.vault_path)
        list_res = server.handle_request({"jsonrpc": "2.0", "id": 50, "method": "tools/list", "params": {}})
        tool_names = {t["name"] for t in list_res["result"]["tools"]}
        self.assertIn("brain_auto_capture", tool_names)
        self.assertIn("brain_auto_retrieve", tool_names)
        self.assertIn("brain_archive_memories", tool_names)

        # Capture using text + omitted title + source_agent + project
        cap_res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 51,
                "method": "tools/call",
                "params": {
                    "name": "brain_auto_capture",
                    "arguments": {
                        "text": "# Zero Dep Policy\nUse pure Python stdlib for alias-capture-unique.",
                        "project": "BrainOSS",
                        "source_agent": "ClaudeCode",
                    },
                },
            }
        )
        self.assertFalse(cap_res["result"]["isError"])
        cap_data = json.loads(cap_res["result"]["content"][0]["text"])
        self.assertEqual(cap_data["title"], "Zero Dep Policy")
        self.assertEqual(cap_data["source_agent"], "ClaudeCode")
        self.assertEqual(cap_data["project"], "BrainOSS")

        # Force older last_accessed so retrieval update is observable
        self._set_db_timestamps(cap_data["id"], last_accessed="2025-01-01 00:00:00")

        ret_res = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 52,
                "method": "tools/call",
                "params": {
                    "name": "brain_auto_retrieve",
                    "arguments": {"prompt": "alias-capture-unique", "project": "BrainOSS", "limit": 3},
                },
            }
        )
        self.assertFalse(ret_res["result"]["isError"])
        ret_list = json.loads(ret_res["result"]["content"][0]["text"])
        self.assertEqual(len(ret_list), 1)
        self.assertEqual(ret_list[0]["id"], cap_data["id"])
        self.assertNotEqual(ret_list[0]["last_accessed"], "2025-01-01 00:00:00")


if __name__ == "__main__":
    unittest.main()
