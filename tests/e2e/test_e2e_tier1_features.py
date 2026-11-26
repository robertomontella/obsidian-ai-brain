"""Tier 1 E2E Tests: Core Feature Contracts (R1-R5) for ai-brain-oss.

Opaque-box tests exercising every requirement through public interfaces
(MCP STDIO JSON-RPC 2.0, CLI tools, and vault/filesystem inspection).
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.e2e.helpers import (
    McpStdioClient,
    REPO_ROOT,
    extract_wikilinks,
    is_stdlib_module,
    make_fake_secrets,
    parse_md_frontmatter,
    query_db_readonly,
    run_setup,
    run_sync_db,
    run_vault_synthesis,
    snapshot_directory,
)


class _Tier1Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aibrain_e2e_t1_")
        self.root = Path(self._tmp.name).resolve()
        self.vault = self.root / "vault"
        self.vault.mkdir(parents=True, exist_ok=True)
        self.db_path = self.vault / "90_System" / "db" / "brain_memory.sqlite"
        self.addCleanup(self._tmp.cleanup)

    def _start_mcp(
        self,
        *,
        vault_path: Path | None = None,
        db_path: Path | None = None,
        writable_inbox: str | None = None,
        read_only: bool = False,
    ) -> McpStdioClient:
        client = McpStdioClient(
            vault_path=vault_path or self.vault,
            db_path=db_path or self.db_path,
            writable_inbox=writable_inbox,
            read_only=read_only,
        )
        self.addCleanup(client.close)
        init_resp = client.initialize()
        self.assertIn("result", init_resp)
        return client


class TestTier1R1CrewHandoff(_Tier1Base):
    """R1: Shared chronological Crew Handoff Logbook."""

    def test_r1_tools_list_exposes_crew_handoff_tools(self) -> None:
        client = self._start_mcp()
        tools = client.list_tools()
        by_name = {t["name"]: t for t in tools}

        self.assertIn("brain_log_handoff", by_name)
        self.assertIn("brain_read_crew_log", by_name)

        log_schema = by_name["brain_log_handoff"].get("inputSchema", {})
        log_props = log_schema.get("properties", {})
        for field in ("agent", "activity", "files", "open_items"):
            self.assertIn(field, log_props, f"Missing '{field}' property on brain_log_handoff")

        read_props = by_name["brain_read_crew_log"].get("inputSchema", {}).get("properties", {})
        self.assertIn("limit", read_props)

    def test_r1_log_handoff_writes_crew_log_markdown_with_all_fields(self) -> None:
        client = self._start_mcp()
        is_err, text, _, _ = client.call_tool(
            "brain_log_handoff",
            {
                "agent": "Antigravity",
                "activity": "Implemented OAuth2 token refresh flow",
                "files": ["src/auth/refresh.py", "tests/test_auth.py"],
                "open_items": ["Verify edge case on expired refresh token"],
                "project": "TaskFlow",
            },
        )
        self.assertFalse(is_err, f"brain_log_handoff failed: {text}")

        crew_log = self.vault / "50_Memory" / "Working" / "crew_log.md"
        self.assertTrue(crew_log.is_file(), "50_Memory/Working/crew_log.md was not created")
        log_text = crew_log.read_text(encoding="utf-8")
        self.assertIn("Antigravity", log_text)
        self.assertIn("Implemented OAuth2 token refresh flow", log_text)
        self.assertIn("src/auth/refresh.py", log_text)
        self.assertIn("Verify edge case on expired refresh token", log_text)

    def test_r1_chronological_multi_agent_handoff_ordering(self) -> None:
        client = self._start_mcp()
        entries = [
            ("Cursor", "Scaffolded API router endpoints", ["api/routes.py"], ["Add pagination"]),
            ("Claude", "Added pagination and validation schemas", ["api/schemas.py"], ["Write integration tests"]),
            ("Gemini", "Added integration tests and benchmarked queries", ["tests/test_api.py"], ["None"]),
        ]
        for agent, activity, files, open_items in entries:
            is_err, text, _, _ = client.call_tool(
                "brain_log_handoff",
                {
                    "agent": agent,
                    "activity": activity,
                    "files": files,
                    "open_items": open_items,
                },
            )
            self.assertFalse(is_err, f"Handoff failed for {agent}: {text}")

        crew_log = self.vault / "50_Memory" / "Working" / "crew_log.md"
        content = crew_log.read_text(encoding="utf-8")
        pos_cursor = content.find("Scaffolded API router endpoints")
        pos_claude = content.find("Added pagination and validation schemas")
        pos_gemini = content.find("Added integration tests and benchmarked queries")
        self.assertNotEqual(pos_cursor, -1)
        self.assertNotEqual(pos_claude, -1)
        self.assertNotEqual(pos_gemini, -1)
        self.assertLess(pos_cursor, pos_claude)
        self.assertLess(pos_claude, pos_gemini)

    def test_r1_read_crew_log_returns_entries_and_honors_limit(self) -> None:
        client = self._start_mcp()
        for idx in range(1, 5):
            is_err, text, _, _ = client.call_tool(
                "brain_log_handoff",
                {
                    "agent": f"Agent-{idx}",
                    "activity": f"Completed milestone step-{idx}",
                    "files": [f"mod_{idx}.py"],
                    "open_items": [f"pending-{idx}"],
                },
            )
            self.assertFalse(is_err, f"Handoff {idx} failed: {text}")

        is_err, read_text, _, _ = client.call_tool("brain_read_crew_log", {"limit": 2})
        self.assertFalse(is_err, f"brain_read_crew_log failed: {read_text}")
        self.assertIn("step-3", read_text)
        self.assertIn("step-4", read_text)
        self.assertNotIn("step-1", read_text)
        self.assertNotIn("step-2", read_text)

    def test_r1_crew_log_is_retrievable_across_server_restarts(self) -> None:
        client1 = self._start_mcp()
        is_err, text, _, _ = client1.call_tool(
            "brain_log_handoff",
            {
                "agent": "Antigravity",
                "activity": "Configured WAL checkpoint intervals for ingestion",
                "files": ["mcp-server/storage.py"],
                "open_items": ["Benchmark concurrent writers"],
            },
        )
        self.assertFalse(is_err, f"Handoff failed: {text}")
        client1.close()

        client2 = self._start_mcp()
        is_err, read_text, _, _ = client2.call_tool("brain_read_crew_log", {"limit": 5})
        self.assertFalse(is_err, f"Read failed after restart: {read_text}")
        self.assertIn("Configured WAL checkpoint intervals for ingestion", read_text)
        self.assertIn("Benchmark concurrent writers", read_text)


class TestTier1R2AutoWikilinks(_Tier1Base):
    """R2: Automatic [[wikilink]] insertion and deduplication on capture/update."""

    def test_r2_auto_capture_inserts_project_wikilink_from_10_projects(self) -> None:
        proj_dir = self.vault / "10_Projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "TaskFlow.md").write_text(
            "---\ntitle: TaskFlow\ntype: project\nstatus: active\n---\n# TaskFlow\nCore workflow platform.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err, text, data, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "TaskFlow Queue Partitioning",
                "content": "Partition worker queues by tenant shard key to avoid noisy neighbors.",
                "memory_type": "memory/semantic",
                "tags": ["queue", "taskflow"],
                "project": "TaskFlow",
            },
        )
        self.assertFalse(is_err, f"brain_auto_capture failed: {text}")
        note_path = self.vault / data["path"]
        self.assertTrue(note_path.is_file())
        links = extract_wikilinks(note_path.read_text(encoding="utf-8"))
        self.assertIn("TaskFlow", links)

    def test_r2_auto_capture_links_related_active_memories(self) -> None:
        client = self._start_mcp()
        is_err1, text1, data1, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Event Bus Retry Policy",
                "content": "Exponential backoff with jitter for Kafka event bus consumer retries.",
                "memory_type": "memory/semantic",
                "tags": ["kafka", "resilience", "event-bus"],
                "project": "DataMesh",
            },
        )
        self.assertFalse(is_err1, text1)
        related_stem = Path(data1["path"]).stem

        is_err2, text2, data2, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Event Bus Dead Letter Queue",
                "content": "Route poisoned Kafka event bus messages to DLQ after five retry attempts.",
                "memory_type": "memory/semantic",
                "tags": ["kafka", "resilience", "dlq"],
                "project": "DataMesh",
            },
        )
        self.assertFalse(is_err2, text2)
        note2_text = (self.vault / data2["path"]).read_text(encoding="utf-8")
        self.assertIn(related_stem, extract_wikilinks(note2_text))

    def test_r2_complementary_update_deduplicates_identical_wikilinks(self) -> None:
        proj_dir = self.vault / "10_Projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "Orion.md").write_text(
            "---\ntitle: Orion\ntype: project\nstatus: active\n---\n# Orion\nTelemetry collector.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err1, text1, data1, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Orion Batch Compression",
                "content": "Compress telemetry batches using zstd level 3 before upload.",
                "memory_type": "memory/semantic",
                "tags": ["orion", "compression"],
                "project": "Orion",
            },
        )
        self.assertFalse(is_err1, text1)

        is_err2, text2, data2, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Orion Batch Compression - Additional Notes",
                "content": "Additional notes: flush compressed telemetry batches every 15 seconds on low traffic.",
                "memory_type": "memory/semantic",
                "tags": ["orion", "compression"],
                "project": "Orion",
            },
        )
        self.assertFalse(is_err2, text2)
        self.assertEqual(data2.get("deduplication", {}).get("level"), "COMPLEMENTARY_UPDATE")

        note_text = (self.vault / data2["path"]).read_text(encoding="utf-8")
        links = extract_wikilinks(note_text)
        self.assertIn("Orion", links)
        self.assertEqual(links.count("Orion"), 1, f"Duplicate [[Orion]] link found in:\n{note_text}")

    def test_r2_wikilinks_and_contradiction_populate_memory_relations_graph(self) -> None:
        proj_dir = self.vault / "10_Projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "Atlas.md").write_text(
            "---\ntitle: Atlas\ntype: project\nstatus: active\n---\n# Atlas\nSearch backend.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err1, text1, data1, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Atlas Index Storage",
                "content": "Atlas uses memory-mapped inverted segments for search.",
                "project": "Atlas",
                "tags": ["atlas", "search"],
            },
        )
        self.assertFalse(is_err1, text1)

        is_err2, text2, data2, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Atlas Index Storage",
                "content": "Switched from memory-mapped inverted segments: replaces Atlas Index Storage with SQLite FTS5.",
                "project": "Atlas",
                "tags": ["atlas", "search"],
            },
        )
        self.assertFalse(is_err2, text2)
        self.assertEqual(data2.get("deduplication", {}).get("level"), "CONTRADICTION")

        relations = query_db_readonly(
            self.db_path,
            "SELECT source_id, target_id, relation_type FROM memory_relations",
        )
        rel_types = {r["relation_type"] for r in relations}
        self.assertIn("supersedes", rel_types)
        self.assertIn("relates_to", rel_types)

    def test_r2_brain_add_memory_also_enriches_project_wikilink(self) -> None:
        proj_dir = self.vault / "10_Projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "Pulse.md").write_text(
            "---\ntitle: Pulse\ntype: project\nstatus: active\n---\n# Pulse\nMetrics agent.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err, text, data, _ = client.call_tool(
            "brain_add_memory",
            {
                "title": "Pulse Ring Buffer Sizing",
                "content": "Allocate a 4096-slot ring buffer per CPU core for lockless metric ingestion.",
                "project": "Pulse",
                "tags": ["pulse", "metrics"],
            },
        )
        self.assertFalse(is_err, text)
        note_text = (self.vault / data["path"]).read_text(encoding="utf-8")
        self.assertIn("Pulse", extract_wikilinks(note_text))


class TestTier1R3RecencyAndArchive(_Tier1Base):
    """R3: last_accessed recency tracking, recency ranking, and explicit-only archiving."""

    def test_r3_read_memory_updates_last_accessed_without_mutating_updated_at(self) -> None:
        client = self._start_mcp()
        is_err, text, added, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Cache Warming Strategy",
                "content": "Preload top 500 tenant keys on worker startup.",
                "memory_type": "memory/semantic",
            },
        )
        self.assertFalse(is_err, text)
        orig_updated_at = added["updated_at"]
        time.sleep(1.1)

        is_err_r, read_text, read_data, _ = client.call_tool(
            "brain_read_memory",
            {"path": added["path"]},
        )
        self.assertFalse(is_err_r, read_text)
        self.assertTrue(read_data.get("last_accessed"), "last_accessed must be populated on read")
        self.assertEqual(read_data.get("updated_at"), orig_updated_at, "read must NOT mutate updated_at")

        note_path = self.vault / added["path"]
        fm, _ = parse_md_frontmatter(note_path.read_text(encoding="utf-8"))
        self.assertTrue(fm.get("last_accessed"), "Frontmatter must include last_accessed after read")
        self.assertEqual(str(fm.get("updated_at")), str(orig_updated_at))

    def test_r3_auto_retrieve_updates_last_accessed_without_mutating_updated_at(self) -> None:
        client = self._start_mcp()
        is_err, text, added, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Circuit Breaker Thresholds",
                "content": "Trip circuit breaker after 5 consecutive upstream timeouts within 30 seconds.",
                "memory_type": "memory/procedural",
            },
        )
        self.assertFalse(is_err, text)
        orig_updated_at = added["updated_at"]
        time.sleep(1.1)

        is_err_s, search_text, results, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "circuit breaker upstream timeouts", "limit": 5},
        )
        self.assertFalse(is_err_s, search_text)
        self.assertGreater(len(results), 0)

        rows = query_db_readonly(
            self.db_path,
            "SELECT last_accessed, updated_at FROM memories WHERE id = ?",
            (added["id"],),
        )
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["last_accessed"])
        self.assertEqual(rows[0]["updated_at"], orig_updated_at)

    def test_r3_recency_weighted_ranking_prefers_recently_accessed_on_equal_relevance(self) -> None:
        sem_dir = self.vault / "50_Memory" / "Semantic"
        sem_dir.mkdir(parents=True, exist_ok=True)
        (sem_dir / "Quantum Gateway Stale.md").write_text(
            "---\n"
            'title: "Quantum Gateway Stale"\n'
            "type: memory/semantic\n"
            "status: active\n"
            "created_at: 2020-01-01 00:00:00\n"
            "updated_at: 2020-01-01 00:00:00\n"
            "last_accessed: 2020-01-01 00:00:00\n"
            "---\n\n"
            "# Quantum Gateway Stale\n\n"
            "Quantum gateway routing invariant uses deterministic shard affinity.\n",
            encoding="utf-8",
        )
        (sem_dir / "Quantum Gateway Fresh.md").write_text(
            "---\n"
            'title: "Quantum Gateway Fresh"\n'
            "type: memory/semantic\n"
            "status: active\n"
            "created_at: 2026-11-24 00:00:00\n"
            "updated_at: 2026-11-24 00:00:00\n"
            "last_accessed: 2026-11-24 00:00:00\n"
            "---\n\n"
            "# Quantum Gateway Fresh\n\n"
            "Quantum gateway routing invariant uses deterministic shard affinity.\n",
            encoding="utf-8",
        )
        sync_res = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        self.assertEqual(sync_res.returncode, 0, sync_res.stderr)

        client = self._start_mcp()
        # Explicitly access Fresh via brain_read_memory to guarantee newest last_accessed
        is_err_r, r_txt, _, _ = client.call_tool(
            "brain_read_memory",
            {"path": "50_Memory/Semantic/Quantum Gateway Fresh.md"},
        )
        self.assertFalse(is_err_r, r_txt)

        is_err_s, s_txt, results, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "Quantum gateway routing invariant deterministic shard affinity", "limit": 5},
        )
        self.assertFalse(is_err_s, s_txt)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "Quantum Gateway Fresh")
        self.assertEqual(results[1]["title"], "Quantum Gateway Stale")

    def test_r3_explicit_archive_moves_cold_notes_to_40_archive(self) -> None:
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        cold_file = epi_dir / "2020-02-01 Legacy Migration.md"
        cold_file.write_text(
            "---\n"
            'title: "Legacy Migration"\n'
            "type: memory/episodic\n"
            "status: active\n"
            "created_at: 2020-02-01 00:00:00\n"
            "updated_at: 2020-02-01 00:00:00\n"
            "last_accessed: 2020-02-01 00:00:00\n"
            "---\n\n"
            "# Legacy Migration\n\n"
            "Old monolith migration notes from 2020.\n",
            encoding="utf-8",
        )
        sync_res = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        self.assertEqual(sync_res.returncode, 0, sync_res.stderr)

        client = self._start_mcp()
        is_err, arch_text, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 90, "max_access_count": 0, "dry_run": False},
        )
        self.assertFalse(is_err, f"brain_archive_memories failed: {arch_text}")

        self.assertFalse(cold_file.exists(), "Cold note should have been moved out of 50_Memory/Episodic")
        archived_files = list((self.vault / "40_Archive").rglob("2020-02-01 Legacy Migration.md"))
        self.assertEqual(len(archived_files), 1, "Cold note must be moved into 40_Archive/")

        fm, _ = parse_md_frontmatter(archived_files[0].read_text(encoding="utf-8"))
        self.assertEqual(fm.get("status"), "archived")
        self.assertTrue(fm.get("archived_at"))

        # Archived notes must be excluded from normal search
        is_err_s, _, search_res, _ = client.call_tool("brain_search_memory", {"query": "monolith migration 2020"})
        self.assertFalse(is_err_s)
        self.assertEqual(len(search_res), 0)

    def test_r3_ordinary_operations_never_implicitly_archive_cold_notes(self) -> None:
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        cold_file = epi_dir / "2019-05-01 Very Old Decision.md"
        cold_file.write_text(
            "---\n"
            'title: "Very Old Decision"\n'
            "type: memory/episodic\n"
            "status: active\n"
            "created_at: 2019-05-01 00:00:00\n"
            "updated_at: 2019-05-01 00:00:00\n"
            "last_accessed: 2019-05-01 00:00:00\n"
            "---\n\n"
            "# Very Old Decision\n\n"
            "Historical decision kept active until explicit archive command.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        client.call_tool("brain_search_memory", {"query": "Historical decision"})
        client.call_tool("brain_auto_retrieve", {"query": "Historical decision"})
        client.call_tool("brain_read_memory", {"path": "50_Memory/Episodic/2019-05-01 Very Old Decision.md"})
        client.call_tool("brain_auto_capture", {"title": "Unrelated New Fact", "content": "Unrelated active fact."})
        client.call_tool("brain_sync", {})

        self.assertTrue(cold_file.is_file(), "Ordinary operations must NEVER implicitly move notes to 40_Archive/")
        archive_dir = self.vault / "40_Archive"
        archived_matches = list(archive_dir.rglob("*Very Old Decision*")) if archive_dir.exists() else []
        self.assertEqual(archived_matches, [])

    def test_r3_legacy_db_without_last_accessed_migrates_idempotently(self) -> None:
        import sqlite3

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        try:
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
                """
            )
        finally:
            conn.close()

        client = self._start_mcp()
        is_err, text, _, _ = client.call_tool(
            "brain_auto_capture",
            {"title": "Post Migration Memory", "content": "Verifying legacy DB migration adds last_accessed."},
        )
        self.assertFalse(is_err, text)

        cols = query_db_readonly(self.db_path, "PRAGMA table_info(memories);")
        col_names = {c["name"] for c in cols}
        self.assertIn("last_accessed", col_names)


class TestTier1R4VaultSynthesis(_Tier1Base):
    """R4: Optional standalone Vault Synthesis decoupled from MCP server core."""

    def _seed_episodic_notes(self, target_dir: Path | None = None) -> list[Path]:
        epi_dir = (target_dir or (self.vault / "50_Memory")) / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        today = time.strftime("%Y-%m-%d")
        notes = [
            (
                f"{today} TaskFlow Retry Incident.md",
                f"---\ntitle: \"TaskFlow Retry Incident\"\ntype: memory/episodic\nstatus: active\n"
                f"project: \"TaskFlow\"\ntags: [taskflow, retries, queue]\ncreated_at: {today}\nupdated_at: {today}\n---\n"
                "# TaskFlow Retry Incident\nObserved duplicate jobs when queue visibility timeout was shorter than retry backoff.\n",
            ),
            (
                f"{today} TaskFlow Idempotency Key Fix.md",
                f"---\ntitle: \"TaskFlow Idempotency Key Fix\"\ntype: memory/episodic\nstatus: active\n"
                f"project: \"TaskFlow\"\ntags: [taskflow, retries, idempotency]\ncreated_at: {today}\nupdated_at: {today}\n---\n"
                "# TaskFlow Idempotency Key Fix\nEnforced deterministic idempotency keys per job execution attempt.\n",
            ),
            (
                f"{today} TaskFlow Backoff Tuning.md",
                f"---\ntitle: \"TaskFlow Backoff Tuning\"\ntype: memory/episodic\nstatus: active\n"
                f"project: \"TaskFlow\"\ntags: [taskflow, retries, backoff]\ncreated_at: {today}\nupdated_at: {today}\n---\n"
                "# TaskFlow Backoff Tuning\nSet visibility timeout to 3x max retry backoff window.\n",
            ),
        ]
        paths: list[Path] = []
        for fname, body in notes:
            p = epi_dir / fname
            p.write_text(body, encoding="utf-8")
            paths.append(p)
        return paths

    def test_r4_vault_synthesis_consolidates_episodic_to_semantic_with_provenance(self) -> None:
        sources = self._seed_episodic_notes()
        res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30", "--target-type", "memory/semantic"])
        self.assertEqual(res.returncode, 0, f"vault_synthesis failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}")

        sem_files = list((self.vault / "50_Memory" / "Semantic").glob("*.md"))
        self.assertGreater(len(sem_files), 0, "Expected synthesized Semantic note in 50_Memory/Semantic/")

        synth_text = "\n".join(p.read_text(encoding="utf-8") for p in sem_files)
        fm, _ = parse_md_frontmatter(sem_files[0].read_text(encoding="utf-8"))
        self.assertEqual(fm.get("type"), "memory/semantic")
        links = extract_wikilinks(synth_text)
        for src in sources:
            self.assertIn(src.stem, links, f"Missing provenance wikilink [[{src.stem}]] in synthesized note")

    def test_r4_vault_synthesis_supports_procedural_target_type(self) -> None:
        self._seed_episodic_notes()
        res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30", "--target-type", "memory/procedural"])
        self.assertEqual(res.returncode, 0, f"vault_synthesis failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}")

        proc_files = list((self.vault / "50_Memory" / "Procedural").glob("*.md"))
        self.assertGreater(len(proc_files), 0, "Expected synthesized Procedural note in 50_Memory/Procedural/")
        fm, _ = parse_md_frontmatter(proc_files[0].read_text(encoding="utf-8"))
        self.assertEqual(fm.get("type"), "memory/procedural")

    def test_r4_vault_synthesis_decoupled_from_mcp_server(self) -> None:
        server_py = REPO_ROOT / "mcp-server" / "server.py"
        synth_py = REPO_ROOT / "tools" / "vault_synthesis.py"
        self.assertTrue(synth_py.is_file(), "tools/vault_synthesis.py does not exist")

        server_tree = ast.parse(server_py.read_text(encoding="utf-8"))
        synth_tree = ast.parse(synth_py.read_text(encoding="utf-8"))

        server_imports = {
            node.module for node in ast.walk(server_tree) if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name for node in ast.walk(server_tree) if isinstance(node, ast.Import) for alias in node.names
        }
        self.assertNotIn("vault_synthesis", server_imports)
        self.assertNotIn("tools.vault_synthesis", server_imports)

        synth_imports = {
            node.module for node in ast.walk(synth_tree) if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name for node in ast.walk(synth_tree) if isinstance(node, ast.Import) for alias in node.names
        }
        self.assertNotIn("server", synth_imports)
        self.assertNotIn("mcp_server", synth_imports)

        help_res = run_vault_synthesis(["--help"])
        self.assertEqual(help_res.returncode, 0)

    def test_r4_vault_synthesis_skill_markdown_exists_and_valid(self) -> None:
        skill_md = REPO_ROOT / "skills" / "vault-synthesis" / "SKILL.md"
        self.assertTrue(skill_md.is_file(), "skills/vault-synthesis/SKILL.md is missing")
        text = skill_md.read_text(encoding="utf-8")
        fm, body = parse_md_frontmatter(text)
        self.assertTrue(fm.get("name"), "SKILL.md frontmatter must declare 'name'")
        self.assertTrue(fm.get("description"), "SKILL.md frontmatter must declare 'description'")
        self.assertIn("vault_synthesis.py", body)

    def test_r4_synthesized_notes_sync_and_retrieve_via_mcp(self) -> None:
        self._seed_episodic_notes()
        res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30", "--target-type", "memory/semantic"])
        self.assertEqual(res.returncode, 0, res.stderr)

        client = self._start_mcp()
        is_err_sync, sync_txt, _, _ = client.call_tool("brain_sync", {})
        self.assertFalse(is_err_sync, sync_txt)

        is_err_ret, ret_txt, results, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "TaskFlow idempotency visibility timeout", "memory_type": "memory/semantic"},
        )
        self.assertFalse(is_err_ret, ret_txt)
        self.assertGreater(len(results), 0, "Synthesized Semantic note must be retrievable via MCP")


class TestTier1R5RepoCoherenceAndIsolation(_Tier1Base):
    """R5: Setup, Sync, Zero-Leak, Vault Isolation, and Zero-External-Dependency Contracts."""

    def test_r5_setup_fresh_mode_installs_and_launches_mcp(self) -> None:
        fresh_vault = self.root / "fresh_vault"
        res = run_setup(["--mode", "fresh", "--dest", str(fresh_vault), "--agents", "all", "--non-interactive"])
        self.assertEqual(res.returncode, 0, f"setup.py fresh failed:\n{res.stdout}\n{res.stderr}")

        cfg_path = fresh_vault / "mcp_ready_config.json"
        self.assertTrue(cfg_path.is_file())
        self.assertTrue((fresh_vault / "50_Memory" / "Working" / "crew_log.md").is_file())

        client = McpStdioClient.from_mcp_config(cfg_path)
        self.addCleanup(client.close)
        init = client.initialize()
        self.assertEqual(init["result"]["serverInfo"]["name"], "ai-brain-mcp")
        is_err, _, stats, _ = client.call_tool("brain_memory_stats", {})
        self.assertFalse(is_err)
        self.assertTrue(stats["wal_enabled"])
        self.assertGreater(stats["total_memories"], 0)

    def test_r5_setup_index_writable_inbox_protects_existing_vault(self) -> None:
        user_vault = self.root / "existing_user_vault"
        idx_dest = self.root / "idx_dest"
        (user_vault / "Notes").mkdir(parents=True, exist_ok=True)
        existing_note = user_vault / "Notes" / "Personal Note.md"
        existing_note.write_text("# Personal Note\nUntouchable user knowledge.\n", encoding="utf-8")
        before = snapshot_directory(user_vault)

        res = run_setup(
            [
                "--mode",
                "index",
                "--vault",
                str(user_vault),
                "--dest",
                str(idx_dest),
                "--writable-inbox",
                "AI-Memory",
                "--agents",
                "all",
                "--non-interactive",
            ]
        )
        self.assertEqual(res.returncode, 0, f"setup.py index failed:\n{res.stdout}\n{res.stderr}")

        after_setup = snapshot_directory(user_vault, exclude_prefixes=("AI-Memory",))
        self.assertEqual(before, after_setup)

        client = McpStdioClient.from_mcp_config(idx_dest / "mcp_ready_config.json")
        self.addCleanup(client.close)
        client.initialize()
        is_err, txt, data, _ = client.call_tool(
            "brain_auto_capture",
            {"title": "Inbox Memory", "content": "Stored safely inside AI-Memory inbox."},
        )
        self.assertFalse(is_err, txt)
        self.assertTrue(data["path"].startswith("AI-Memory/"))

        after_mcp = snapshot_directory(user_vault, exclude_prefixes=("AI-Memory",))
        self.assertEqual(before, after_mcp)

    def test_r5_sync_db_cli_add_modify_move_delete_lifecycle(self) -> None:
        notes_dir = self.vault / "50_Memory" / "Semantic"
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_a = notes_dir / "Note Alpha.md"
        note_a.write_text("---\ntitle: Note Alpha\n---\n# Note Alpha\nInitial alpha body.\n", encoding="utf-8")

        r1 = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        self.assertEqual(r1.returncode, 0, r1.stderr)
        s1 = json.loads(r1.stdout)
        self.assertEqual(len(s1["added"]), 1)

        note_a.write_text("---\ntitle: Note Alpha\n---\n# Note Alpha\nModified alpha body.\n", encoding="utf-8")
        r2 = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        s2 = json.loads(r2.stdout)
        self.assertEqual(len(s2["updated"]), 1)

        moved = notes_dir / "Note Alpha Renamed.md"
        note_a.rename(moved)
        r3 = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        self.assertEqual(r3.returncode, 0, r3.stderr)
        s3 = json.loads(r3.stdout)
        self.assertEqual(len(s3["updated"]), 1)

        moved.unlink()
        r4 = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        self.assertEqual(r4.returncode, 0, r4.stderr)
        s4 = json.loads(r4.stdout)
        self.assertEqual(len(s4["deleted"]), 1)

    def test_r5_zero_leak_sanitization_via_mcp_capture_and_read(self) -> None:
        secrets = make_fake_secrets()
        client = self._start_mcp()
        payload = (
            f"Credentials test:\n"
            f"openai={secrets['openai']}\n"
            f"github={secrets['github']}\n"
            f"gitlab={secrets['gitlab']}\n"
            f"slack={secrets['slack']}\n"
            f"google={secrets['google']}\n"
            f"auth={secrets['bearer']}\n"
            f"dsn={secrets['db_url']}\n"
            f"jwt={secrets['jwt']}\n"
            f"{secrets['password']}\n"
        )
        is_err, txt, added, _ = client.call_tool(
            "brain_auto_capture",
            {"title": "Sanitizer Check", "content": payload},
        )
        self.assertFalse(is_err, txt)

        disk_text = (self.vault / added["path"]).read_text(encoding="utf-8")
        _, read_txt, _, _ = client.call_tool("brain_read_memory", {"path": added["path"]})
        combined = disk_text + "\n" + read_txt

        for secret_key in ("openai", "github", "gitlab", "slack", "google", "jwt"):
            self.assertNotIn(secrets[secret_key], combined, f"Unredacted {secret_key} leaked!")
        self.assertNotIn("fakepass_9876", combined)
        self.assertNotIn("SuperSecretPass99!", combined)
        self.assertNotIn("tok_secret_", combined)

    def test_r5_zero_external_dependencies_across_code_and_e2e_tests(self) -> None:
        local_modules = {
            p.stem
            for folder in ("mcp-server", "tools", "tests/e2e")
            for p in (REPO_ROOT / folder).glob("*.py")
        } | {"setup", "tests", "tests.e2e", "tests.e2e.helpers"}

        py_files = [REPO_ROOT / "setup.py"]
        for folder in ("mcp-server", "tools", "tests/e2e"):
            py_files.extend(sorted((REPO_ROOT / folder).glob("*.py")))

        external_violations: list[str] = []
        for py_file in py_files:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top not in local_modules and not is_stdlib_module(top):
                            external_violations.append(f"{py_file.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    top = node.module.split(".")[0]
                    if top not in local_modules and not is_stdlib_module(top):
                        external_violations.append(f"{py_file.name}: from {node.module} import ...")

        self.assertEqual(external_violations, [], f"External imports forbidden: {external_violations}")


if __name__ == "__main__":
    unittest.main()
