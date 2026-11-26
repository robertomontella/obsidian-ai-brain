"""Tier 3 E2E Tests: Cross-Feature Integration & Pairwise Interactions (R1-R5).

Tests pairwise and multi-feature interactions through public MCP STDIO,
CLI tools, and filesystem/vault inspections.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from tests.e2e.helpers import (
    McpStdioClient,
    extract_wikilinks,
    make_fake_secrets,
    parse_md_frontmatter,
    query_db_readonly,
    run_setup,
    run_sync_db,
    run_vault_synthesis,
    snapshot_directory,
)


class TestTier3CrossFeature(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aibrain_e2e_t3_")
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
        client.initialize()
        return client

    def test_t3_1_r1_r5_crew_log_zero_leak_with_writable_inbox(self) -> None:
        """R1 + R5: Crew handoff sanitizes secrets and stays confined to writable_inbox."""
        secrets = make_fake_secrets()
        (self.vault / "Personal").mkdir(parents=True, exist_ok=True)
        (self.vault / "Personal" / "Diary.md").write_text("# Diary\nPrivate user file.\n", encoding="utf-8")
        before = snapshot_directory(self.vault, exclude_prefixes=("AI-Memory",))

        client = self._start_mcp(db_path=self.root / "t3_inbox_index.sqlite", writable_inbox="AI-Memory")
        is_err, txt, _, _ = client.call_tool(
            "brain_log_handoff",
            {
                "agent": "Antigravity",
                "activity": f"Debugged auth webhook with token {secrets['github']} and {secrets['bearer']}",
                "files": ["AI-Memory/Semantic/Auth.md"],
                "open_items": [f"Rotate {secrets['openai']}"],
            },
        )
        self.assertFalse(is_err, txt)
        self.assertEqual(before, snapshot_directory(self.vault, exclude_prefixes=("AI-Memory",)))

        inbox_log = self.vault / "AI-Memory" / "Working" / "crew_log.md"
        self.assertTrue(inbox_log.is_file())
        log_disk = inbox_log.read_text(encoding="utf-8")
        _, read_txt, _, _ = client.call_tool("brain_read_crew_log", {"limit": 5})
        for tok in (secrets["github"], secrets["openai"], "tok_secret_"):
            self.assertNotIn(tok, log_disk + read_txt)

    def test_t3_2_r1_r2_auto_capture_project_and_crew_handoff_alignment(self) -> None:
        """R1 + R2: Project-linked auto-capture and crew handoff reference the same project and artifacts."""
        (self.vault / "10_Projects").mkdir(parents=True, exist_ok=True)
        (self.vault / "10_Projects" / "TaskFlow.md").write_text(
            "---\ntitle: TaskFlow\ntype: project\nstatus: active\n---\n# TaskFlow\nScheduler.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err1, t1, mem, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "TaskFlow Lease Renewal",
                "content": "Workers renew distributed leases every 10 seconds with 30s TTL.",
                "project": "TaskFlow",
                "tags": ["taskflow", "leases"],
            },
        )
        self.assertFalse(is_err1, t1)
        note_text = (self.vault / mem["path"]).read_text(encoding="utf-8")
        self.assertIn("TaskFlow", extract_wikilinks(note_text))

        is_err2, t2, _, _ = client.call_tool(
            "brain_log_handoff",
            {
                "agent": "Cursor",
                "activity": "Implemented worker lease renewal loop",
                "files": [mem["path"], "worker/lease.py"],
                "open_items": ["Add chaos test for lease expiry"],
                "project": "TaskFlow",
            },
        )
        self.assertFalse(is_err2, t2)
        _, crew_txt, _, _ = client.call_tool("brain_read_crew_log", {"limit": 5})
        self.assertIn("Implemented worker lease renewal loop", crew_txt)
        self.assertIn(mem["path"], crew_txt)

    def test_t3_3_r2_r3_wikilinks_recency_boost_and_selective_archive(self) -> None:
        """R2 + R3: Wikilinked active note is boosted on retrieval and preserved while cold note is archived."""
        (self.vault / "10_Projects").mkdir(parents=True, exist_ok=True)
        proj = self.vault / "10_Projects" / "Orion.md"
        proj.write_text("---\ntitle: Orion\ntype: project\nstatus: active\n---\n# Orion\nPlatform.\n", encoding="utf-8")

        sem_dir = self.vault / "50_Memory" / "Semantic"
        sem_dir.mkdir(parents=True, exist_ok=True)
        cold_note = sem_dir / "Orion Cold Spec.md"
        cold_note.write_text(
            "---\ntitle: \"Orion Cold Spec\"\ntype: memory/semantic\nstatus: active\nproject: \"Orion\"\n"
            "created_at: 2020-01-01 00:00:00\nupdated_at: 2020-01-01 00:00:00\nlast_accessed: 2020-01-01 00:00:00\n---\n"
            "# Orion Cold Spec\nOrion telemetry pipeline specification.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err, txt, hot_mem, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Orion Hot Spec",
                "content": "Orion telemetry pipeline specification.",
                "project": "Orion",
                "tags": ["orion", "telemetry"],
            },
        )
        self.assertFalse(is_err, txt)
        hot_text = (self.vault / hot_mem["path"]).read_text(encoding="utf-8")
        self.assertIn("Orion", extract_wikilinks(hot_text))

        # Retrieve to boost hot_mem recency (while cold_note stays unaccessed from 2020)
        client.call_tool("brain_read_memory", {"path": hot_mem["path"]})
        _, _, search_res, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "Orion telemetry pipeline specification", "limit": 5},
        )
        self.assertGreaterEqual(len(search_res), 2)
        self.assertEqual(search_res[0]["title"], "Orion Hot Spec")

        # Explicitly archive cold notes (older_than_days=90, max_access_count=0)
        is_err_a, arch_txt, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 90, "max_access_count": 0, "dry_run": False},
        )
        self.assertFalse(is_err_a, arch_txt)
        self.assertTrue((self.vault / hot_mem["path"]).is_file(), "Active note must remain outside archive")
        self.assertTrue(proj.is_file(), "Project note must remain outside archive")
        self.assertFalse(cold_note.exists(), "Cold note should move to 40_Archive/")
        self.assertEqual(len(list((self.vault / "40_Archive").rglob("Orion Cold Spec.md"))), 1)

    def test_t3_4_r3_r5_sync_db_persists_last_accessed_and_archived_at_roundtrip(self) -> None:
        """R3 + R5: sync_db.py rebuild preserves last_accessed and archived_at from Markdown frontmatter."""
        client = self._start_mcp()
        _, _, active_rec, _ = client.call_tool(
            "brain_auto_capture",
            {"title": "Active Protocol", "content": "Active protocol payload for rebuild test."},
        )
        time.sleep(1.1)
        _, _, read_rec, _ = client.call_tool("brain_read_memory", {"path": active_rec["path"]})
        expected_last_accessed = read_rec["last_accessed"]
        self.assertTrue(expected_last_accessed)

        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        (epi_dir / "2020-03-03 Cold Archived.md").write_text(
            "---\ntitle: \"Cold Archived\"\ntype: memory/episodic\nstatus: active\n"
            "created_at: 2020-03-03 00:00:00\nupdated_at: 2020-03-03 00:00:00\nlast_accessed: 2020-03-03 00:00:00\n---\n"
            "# Cold Archived\nLegacy note.\n",
            encoding="utf-8",
        )
        client.call_tool("brain_sync", {})
        client.call_tool("brain_archive_memories", {"older_than_days": 90, "max_access_count": 0})
        client.close()

        # Delete SQLite DB and rebuild cleanly from Markdown via sync_db.py
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.db_path) + suffix)
            if p.exists():
                p.unlink()

        sync_res = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        self.assertEqual(sync_res.returncode, 0, sync_res.stderr)

        rows_active = query_db_readonly(
            self.db_path,
            "SELECT last_accessed, status FROM memories WHERE path = ?",
            (active_rec["path"],),
        )
        self.assertEqual(len(rows_active), 1)
        self.assertEqual(rows_active[0]["status"], "active")
        self.assertEqual(rows_active[0]["last_accessed"], expected_last_accessed)

        rows_arch = query_db_readonly(
            self.db_path,
            "SELECT status, path FROM memories WHERE title = 'Cold Archived'",
        )
        self.assertEqual(len(rows_arch), 1)
        self.assertEqual(rows_arch[0]["status"], "archived")
        self.assertTrue(rows_arch[0]["path"].startswith("40_Archive/"))

    def test_t3_5_r4_r2_vault_synthesis_provenance_wikilinks_then_sync_and_retrieve(self) -> None:
        """R4 + R2: vault_synthesis.py produces Semantic note with [[wikilinks]] retrievable after brain_sync."""
        client = self._start_mcp()
        today = time.strftime("%Y-%m-%d")
        epi_stems: list[str] = []
        for idx, body in enumerate(
            (
                "Configured SQLite busy_timeout to 5000ms for multi-agent stability.",
                "Enabled WAL mode and synchronous=NORMAL for sub-5ms FTS5 queries.",
                "Verified concurrent MCP writers pass zero lock timeouts under load.",
            ),
            1,
        ):
            is_err, txt, rec, _ = client.call_tool(
                "brain_auto_capture",
                {
                    "title": f"{today} SQLite Concurrency Note {idx}",
                    "content": body,
                    "memory_type": "memory/episodic",
                    "project": "CoreDB",
                    "tags": ["sqlite", "concurrency", "wal"],
                },
            )
            self.assertFalse(is_err, txt)
            epi_stems.append(Path(rec["path"]).stem)

        synth_res = run_vault_synthesis(
            ["--vault", str(self.vault), "--days", "30", "--target-type", "memory/semantic"]
        )
        self.assertEqual(synth_res.returncode, 0, synth_res.stderr)

        is_err_sync, sync_txt, _, _ = client.call_tool("brain_sync", {})
        self.assertFalse(is_err_sync, sync_txt)

        is_err_ret, ret_txt, results, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "SQLite WAL busy_timeout concurrency", "memory_type": "memory/semantic"},
        )
        self.assertFalse(is_err_ret, ret_txt)
        self.assertGreater(len(results), 0)
        synth_disk = (self.vault / results[0]["path"]).read_text(encoding="utf-8")
        links = extract_wikilinks(synth_disk)
        for stem in epi_stems:
            self.assertIn(stem, links)

    def test_t3_6_r4_r3_synthesis_ranks_above_old_episodic_and_explicit_archives_sources(self) -> None:
        """R4 + R3: Synthesized Semantic note ranks over old Episodic sources; sources archive explicitly."""
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        for i in (1, 2):
            (epi_dir / f"2026-11-10 Cache Note {i}.md").write_text(
                f"---\ntitle: \"Cache Note {i}\"\ntype: memory/episodic\nstatus: active\n"
                "project: CacheSys\ntags: [cache, ttl]\ncreated_at: 2026-11-10 00:00:00\n"
                "updated_at: 2026-11-10 00:00:00\nlast_accessed: 2026-11-10 00:00:00\n---\n"
                f"# Cache Note {i}\nCache eviction jitter tuning iteration {i}.\n",
                encoding="utf-8",
            )

        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        synth_res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30", "--target-type", "memory/semantic"])
        self.assertEqual(synth_res.returncode, 0, synth_res.stderr)

        # Backdate episodic notes so they qualify as cold for explicit archive test
        for p in epi_dir.glob("*.md"):
            txt = p.read_text(encoding="utf-8").replace("2026-11-10 00:00:00", "2020-01-01 00:00:00")
            p.write_text(txt, encoding="utf-8")

        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        _, _, ret_res, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "Cache eviction jitter tuning", "limit": 5},
        )
        self.assertGreaterEqual(len(ret_res), 2)
        self.assertEqual(ret_res[0]["type"], "memory/semantic")

        is_err_a, arch_txt, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 90, "max_access_count": 0},
        )
        self.assertFalse(is_err_a, arch_txt)
        self.assertEqual(list(epi_dir.glob("*.md")), [])
        self.assertGreater(len(list((self.vault / "50_Memory" / "Semantic").glob("*.md"))), 0)

    def test_t3_7_r1_r3_r5_strict_read_only_allows_reads_blocks_all_writes(self) -> None:
        """R1 + R3 + R5: Strict read-only permits read/retrieve/crew_log read while blocking all writes."""
        client_rw = self._start_mcp()
        _, _, mem, _ = client_rw.call_tool(
            "brain_auto_capture",
            {"title": "ReadOnly Seed", "content": "Seeded content for strict read-only audit."},
        )
        client_rw.call_tool(
            "brain_log_handoff",
            {"agent": "Claude", "activity": "Seeded crew log entry", "files": [mem["path"]]},
        )
        client_rw.close()

        snap_before = snapshot_directory(self.vault, exclude_prefixes=("90_System",))
        client_ro = self._start_mcp(read_only=True, writable_inbox=None)

        is_err_ret, _, ret_list, _ = client_ro.call_tool("brain_auto_retrieve", {"query": "strict read-only audit"})
        self.assertFalse(is_err_ret)
        self.assertEqual(len(ret_list), 1)

        is_err_rd, _, _, _ = client_ro.call_tool("brain_read_memory", {"path": mem["path"]})
        self.assertFalse(is_err_rd)

        is_err_cl, cl_txt, _, _ = client_ro.call_tool("brain_read_crew_log", {"limit": 5})
        self.assertFalse(is_err_cl)
        self.assertIn("Seeded crew log entry", cl_txt)

        for blocked_tool, args in (
            ("brain_auto_capture", {"title": "Blocked", "content": "Nope"}),
            ("brain_log_handoff", {"agent": "Cursor", "activity": "Nope"}),
            ("brain_archive_memories", {"older_than_days": 1}),
        ):
            is_err_b, _, _, _ = client_ro.call_tool(blocked_tool, args)
            self.assertTrue(is_err_b, f"{blocked_tool} should be rejected in read-only mode")

        self.assertEqual(snap_before, snapshot_directory(self.vault, exclude_prefixes=("90_System",)))

    def test_t3_8_r4_r5_setup_fresh_installs_modules_and_tools_working_end_to_end(self) -> None:
        """R4 + R5: setup.py --mode fresh installs server, crew_log, wikilinks, and vault_synthesis into 90_System/."""
        fresh_vault = self.root / "fresh_installed"
        res = run_setup(["--mode", "fresh", "--dest", str(fresh_vault), "--agents", "all", "--non-interactive"])
        self.assertEqual(res.returncode, 0, res.stderr)

        scripts_dir = fresh_vault / "90_System" / "scripts"
        tools_dir = fresh_vault / "90_System" / "tools"
        for mod in ("server.py", "storage.py", "deduplication.py", "sanitizer.py", "crew_log.py", "wikilinks.py"):
            self.assertTrue((scripts_dir / mod).is_file(), f"Missing installed script: {mod}")
        for tool in ("sync_db.py", "vault_synthesis.py"):
            self.assertTrue((tools_dir / tool).is_file(), f"Missing installed tool: {tool}")

        client = McpStdioClient.from_mcp_config(fresh_vault / "mcp_ready_config.json")
        self.addCleanup(client.close)
        client.initialize()

        today = time.strftime("%Y-%m-%d")
        for i in (1, 2):
            is_err, txt, _, _ = client.call_tool(
                "brain_auto_capture",
                {
                    "title": f"{today} Installed Episodic {i}",
                    "content": f"Installed vault synthesis step {i} for TaskFlow.",
                    "memory_type": "memory/episodic",
                    "project": "TaskFlow",
                    "tags": ["taskflow", "installed"],
                },
            )
            self.assertFalse(is_err, txt)

        synth_res = run_vault_synthesis(
            ["--vault", str(fresh_vault), "--days", "30", "--target-type", "memory/semantic"],
            script_path=tools_dir / "vault_synthesis.py",
        )
        self.assertEqual(synth_res.returncode, 0, synth_res.stderr)
        sem_notes = list((fresh_vault / "50_Memory" / "Semantic").glob("*.md"))
        self.assertTrue(any("TaskFlow" in p.read_text(encoding="utf-8") for p in sem_notes))


if __name__ == "__main__":
    unittest.main()
