"""Tier 2 E2E Tests: Boundaries, Negative Paths, Security, and Isolation (R1-R5).

Opaque-box verification of edge cases, error codes, zero-leak redaction,
strict --read-only enforcement, and --writable-inbox containment.
"""

from __future__ import annotations

import json
import tempfile
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


class _Tier2Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aibrain_e2e_t2_")
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


class TestTier2R1CrewLogBoundaries(_Tier2Base):
    """R1 boundary, negative, zero-leak, and isolation tests."""

    def test_r1_zero_leak_redaction_of_secrets_in_crew_log(self) -> None:
        secrets = make_fake_secrets()
        client = self._start_mcp()

        is_err, txt, _, _ = client.call_tool(
            "brain_log_handoff",
            {
                "agent": "Cursor",
                "activity": f"Rotated key {secrets['openai']} and token {secrets['github']} via {secrets['db_url']}",
                "files": [f"config/{secrets['slack']}.env"],
                "open_items": [f"Revoke {secrets['bearer']} and {secrets['gitlab']}"],
            },
        )
        self.assertFalse(is_err, txt)

        crew_log = self.vault / "50_Memory" / "Working" / "crew_log.md"
        log_disk = crew_log.read_text(encoding="utf-8")
        _, log_read, _, _ = client.call_tool("brain_read_crew_log", {"limit": 5})
        combined = log_disk + "\n" + log_read

        for k in ("openai", "github", "slack", "gitlab"):
            self.assertNotIn(secrets[k], combined, f"Secret '{k}' leaked in crew_log!")
        self.assertNotIn("fakepass_9876", combined)
        self.assertNotIn("tok_secret_", combined)

    def test_r1_strict_read_only_rejects_log_handoff_and_allows_read(self) -> None:
        crew_file = self.vault / "50_Memory" / "Working" / "crew_log.md"
        crew_file.parent.mkdir(parents=True, exist_ok=True)
        crew_file.write_text("# Crew Handoff Logbook\n\n- **Antigravity**: Baseline entry.\n", encoding="utf-8")
        snap_before = snapshot_directory(self.vault)

        client = self._start_mcp(db_path=self.root / "ro_index.sqlite", read_only=True, writable_inbox=None)
        is_err_write, err_txt, _, _ = client.call_tool(
            "brain_log_handoff",
            {"agent": "Claude", "activity": "Attempt write in read-only mode"},
        )
        self.assertTrue(is_err_write, "brain_log_handoff must fail in strict --read-only mode")
        self.assertIn("read-only", err_txt.lower())

        is_err_read, read_txt, _, _ = client.call_tool("brain_read_crew_log", {"limit": 5})
        self.assertFalse(is_err_read, read_txt)
        self.assertIn("Baseline entry", read_txt)
        self.assertEqual(snap_before, snapshot_directory(self.vault))

    def test_r1_writable_inbox_confines_crew_log_inside_inbox(self) -> None:
        (self.vault / "Notes").mkdir(parents=True, exist_ok=True)
        ext_note = self.vault / "Notes" / "Existing.md"
        ext_note.write_text("# Existing\nProtected note.\n", encoding="utf-8")
        before = snapshot_directory(self.vault, exclude_prefixes=("AI-Memory",))

        client = self._start_mcp(db_path=self.root / "inbox_index.sqlite", writable_inbox="AI-Memory")
        is_err, txt, _, _ = client.call_tool(
            "brain_log_handoff",
            {
                "agent": "Gemini",
                "activity": "Updated inbox schema",
                "files": ["AI-Memory/Semantic/Schema.md"],
                "open_items": ["Review links"],
            },
        )
        self.assertFalse(is_err, txt)

        inbox_log = self.vault / "AI-Memory" / "Working" / "crew_log.md"
        self.assertTrue(inbox_log.is_file(), "crew_log.md must be written inside <writable_inbox>/Working/")
        self.assertFalse((self.vault / "50_Memory" / "Working" / "crew_log.md").exists())
        self.assertEqual(before, snapshot_directory(self.vault, exclude_prefixes=("AI-Memory",)))

    def test_r1_empty_crew_log_and_missing_required_fields_handling(self) -> None:
        client = self._start_mcp()
        is_err_empty, empty_txt, _, _ = client.call_tool("brain_read_crew_log", {"limit": 5})
        self.assertFalse(is_err_empty, f"Reading empty crew log should not crash: {empty_txt}")

        is_err_missing, err_txt, _, _ = client.call_tool(
            "brain_log_handoff",
            {"agent": "", "activity": ""},
        )
        self.assertTrue(is_err_missing, f"Empty agent/activity must be rejected: {err_txt}")

    def test_r1_special_characters_pipes_and_multiline_in_crew_log(self) -> None:
        client = self._start_mcp()
        is_err, txt, _, _ = client.call_tool(
            "brain_log_handoff",
            {
                "agent": "Antigravity",
                "activity": "Fixed `foo | bar` parser\nSecond line details -> OK",
                "files": ["mcp-server/server.py", "tools/sync_db.py"],
                "open_items": ["Check edge case [a|b]"],
            },
        )
        self.assertFalse(is_err, txt)

        is_err_r, read_txt, _, _ = client.call_tool("brain_read_crew_log", {"limit": 1})
        self.assertFalse(is_err_r, read_txt)
        self.assertIn("foo | bar", read_txt)
        self.assertIn("mcp-server/server.py", read_txt)


class TestTier2R2WikilinkBoundaries(_Tier2Base):
    """R2 boundary, deduplication, self-link exclusion, and inbox isolation tests."""

    def test_r2_repeated_complementary_updates_never_duplicate_wikilinks(self) -> None:
        proj_dir = self.vault / "10_Projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "Nexus.md").write_text(
            "---\ntitle: Nexus\ntype: project\nstatus: active\n---\n# Nexus\nGateway.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err1, t1, d1, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Nexus Rate Limiter",
                "content": "Token bucket rate limiter per API key.",
                "project": "Nexus",
                "tags": ["nexus", "rate-limit"],
            },
        )
        self.assertFalse(is_err1, t1)

        for idx in range(1, 4):
            is_err_u, tu, du, _ = client.call_tool(
                "brain_auto_capture",
                {
                    "title": f"Nexus Rate Limiter - Additional Notes {idx}",
                    "content": f"Additional notes: burst limit set to {idx * 100} requests per second.",
                    "project": "Nexus",
                    "tags": ["nexus", "rate-limit"],
                },
            )
            self.assertFalse(is_err_u, tu)
            self.assertEqual(du["deduplication"]["level"], "COMPLEMENTARY_UPDATE")

        note_text = (self.vault / d1["path"]).read_text(encoding="utf-8")
        links = extract_wikilinks(note_text)
        self.assertIn("Nexus", links)
        self.assertEqual(links.count("Nexus"), 1, f"Duplicate [[Nexus]] after 3 updates:\n{note_text}")

    def test_r2_self_link_excluded_from_generated_wikilinks(self) -> None:
        client = self._start_mcp()
        is_err, txt, data, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Vector Cache Design",
                "content": "Vector cache design using LRU eviction.",
                "tags": ["vector", "cache"],
            },
        )
        self.assertFalse(is_err, txt)
        self_stem = Path(data["path"]).stem
        note_text = (self.vault / data["path"]).read_text(encoding="utf-8")
        self.assertNotIn(self_stem, extract_wikilinks(note_text))

    def test_r2_no_spurious_links_when_no_project_or_related_note_exists(self) -> None:
        client = self._start_mcp()
        is_err, txt, data, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Standalone Isolated Fact",
                "content": "Isolated observation with no matching project note or related memory in vault.",
                "project": "NonExistentProjectXYZ",
                "tags": ["isolated"],
            },
        )
        self.assertFalse(is_err, txt)
        note_text = (self.vault / data["path"]).read_text(encoding="utf-8")
        self.assertEqual(extract_wikilinks(note_text), [])

    def test_r2_writable_inbox_links_external_notes_without_modifying_them(self) -> None:
        (self.vault / "10_Projects").mkdir(parents=True, exist_ok=True)
        proj_note = self.vault / "10_Projects" / "Cobalt.md"
        proj_note.write_text("---\ntitle: Cobalt\ntype: project\n---\n# Cobalt\nCore.\n", encoding="utf-8")
        (self.vault / "Notes").mkdir(parents=True, exist_ok=True)
        domain_note = self.vault / "Notes" / "Cobalt Architecture.md"
        domain_note.write_text(
            "---\ntitle: Cobalt Architecture\ntype: memory/semantic\nproject: Cobalt\n---\n"
            "# Cobalt Architecture\nCore entities: Stream, Partition, Broker.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        before = snapshot_directory(self.vault, exclude_prefixes=("AI-Memory", "90_System"))

        client = self._start_mcp(writable_inbox="AI-Memory")
        is_err, txt, data, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Cobalt Architecture Extensions",
                "content": "Core entities: Stream, Partition, Broker. Additional notes: added ConsumerGroup.",
                "project": "Cobalt",
            },
        )
        self.assertFalse(is_err, txt)
        self.assertTrue(data["path"].startswith("AI-Memory/"))
        self.assertEqual(data["deduplication"]["action"], "created_scoped_reference")
        self.assertEqual(before, snapshot_directory(self.vault, exclude_prefixes=("AI-Memory", "90_System")))

        note_text = (self.vault / data["path"]).read_text(encoding="utf-8")
        self.assertIn("Cobalt", extract_wikilinks(note_text))

    def test_r2_multi_word_project_stem_used_for_wikilink(self) -> None:
        (self.vault / "10_Projects").mkdir(parents=True, exist_ok=True)
        (self.vault / "10_Projects" / "Alpha Beta Pipeline.md").write_text(
            "---\ntitle: Alpha Beta Overview\ntype: project\nstatus: active\n---\n# Alpha Beta\nETL.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp()
        is_err, txt, data, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Pipeline Checkpoint Format",
                "content": "Store ETL checkpoints as newline-delimited JSON.",
                "project": "Alpha Beta Overview",
            },
        )
        self.assertFalse(is_err, txt)
        note_text = (self.vault / data["path"]).read_text(encoding="utf-8")
        self.assertIn("Alpha Beta Pipeline", extract_wikilinks(note_text))


class TestTier2R3RecencyArchiveBoundaries(_Tier2Base):
    """R3 boundary, dry-run, protected-path, and isolation tests."""

    def test_r3_strict_read_only_retrieval_never_mutates_disk_files(self) -> None:
        sem_dir = self.vault / "50_Memory" / "Semantic"
        sem_dir.mkdir(parents=True, exist_ok=True)
        note = sem_dir / "Immutable Note.md"
        note.write_text(
            "---\ntitle: Immutable Note\ntype: memory/semantic\nstatus: active\n---\n"
            "# Immutable Note\nDeterministic routing table invariant.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        snap_before = snapshot_directory(self.vault, exclude_prefixes=("90_System",))

        client = self._start_mcp(read_only=True, writable_inbox=None)
        is_err_s, s_txt, s_res, _ = client.call_tool("brain_auto_retrieve", {"query": "routing table invariant"})
        self.assertFalse(is_err_s, s_txt)
        self.assertGreater(len(s_res), 0)

        is_err_r, r_txt, _, _ = client.call_tool("brain_read_memory", {"path": "50_Memory/Semantic/Immutable Note.md"})
        self.assertFalse(is_err_r, r_txt)

        self.assertEqual(snap_before, snapshot_directory(self.vault, exclude_prefixes=("90_System",)))

    def test_r3_writable_inbox_retrieval_does_not_mutate_external_md_frontmatter(self) -> None:
        (self.vault / "Notes").mkdir(parents=True, exist_ok=True)
        ext_note = self.vault / "Notes" / "External Fact.md"
        ext_note.write_text(
            "---\ntitle: External Fact\ntype: memory/semantic\n---\n# External Fact\nExternal immutable fact.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        before = snapshot_directory(self.vault, exclude_prefixes=("AI-Memory", "90_System"))

        client = self._start_mcp(writable_inbox="AI-Memory")
        is_err_a, a_txt, added, _ = client.call_tool(
            "brain_auto_capture",
            {"title": "Inbox Fact", "content": "Mutable inbox memory note."},
        )
        self.assertFalse(is_err_a, a_txt)

        client.call_tool("brain_read_memory", {"path": "Notes/External Fact.md"})
        client.call_tool("brain_read_memory", {"path": added["path"]})
        client.call_tool("brain_auto_retrieve", {"query": "immutable fact"})

        self.assertEqual(before, snapshot_directory(self.vault, exclude_prefixes=("AI-Memory", "90_System")))
        fm_inbox, _ = parse_md_frontmatter((self.vault / added["path"]).read_text(encoding="utf-8"))
        self.assertTrue(fm_inbox.get("last_accessed"))

    def test_r3_archive_dry_run_makes_zero_disk_or_db_changes(self) -> None:
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        cold = epi_dir / "2020-01-01 DryRun Cold.md"
        cold.write_text(
            "---\ntitle: DryRun Cold\ntype: memory/episodic\nstatus: active\n"
            "created_at: 2020-01-01 00:00:00\nupdated_at: 2020-01-01 00:00:00\nlast_accessed: 2020-01-01 00:00:00\n---\n"
            "# DryRun Cold\nOld cold memory.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        snap_before = snapshot_directory(self.vault, exclude_prefixes=("90_System",))

        client = self._start_mcp()
        is_err, txt, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 90, "max_access_count": 0, "dry_run": True},
        )
        self.assertFalse(is_err, txt)
        self.assertEqual(snap_before, snapshot_directory(self.vault, exclude_prefixes=("90_System",)))
        rows = query_db_readonly(self.db_path, "SELECT status FROM memories WHERE path = ?", ("50_Memory/Episodic/2020-01-01 DryRun Cold.md",))
        self.assertEqual(rows[0]["status"], "active")

    def test_r3_archive_refuses_projects_crew_log_and_read_only_mode(self) -> None:
        (self.vault / "10_Projects").mkdir(parents=True, exist_ok=True)
        proj = self.vault / "10_Projects" / "CoreProject.md"
        proj.write_text(
            "---\ntitle: CoreProject\ntype: project\nstatus: active\n"
            "created_at: 2020-01-01 00:00:00\nupdated_at: 2020-01-01 00:00:00\nlast_accessed: 2020-01-01 00:00:00\n---\n"
            "# CoreProject\nProtected project.\n",
            encoding="utf-8",
        )
        (self.vault / "50_Memory" / "Working").mkdir(parents=True, exist_ok=True)
        crew = self.vault / "50_Memory" / "Working" / "crew_log.md"
        crew.write_text(
            "---\ntitle: Crew Log\ntype: memory/working\nstatus: active\n"
            "created_at: 2020-01-01 00:00:00\nupdated_at: 2020-01-01 00:00:00\nlast_accessed: 2020-01-01 00:00:00\n---\n"
            "# Crew Log\nProtected crew log.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client_rw = self._start_mcp()
        client_rw.call_tool(
            "brain_archive_memories",
            {
                "older_than_days": 1,
                "max_access_count": 100,
                "paths": ["10_Projects/CoreProject.md", "50_Memory/Working/crew_log.md"],
            },
        )
        self.assertTrue(proj.is_file(), "10_Projects/ notes must NEVER be archived")
        self.assertTrue(crew.is_file(), "crew_log.md must NEVER be archived")

        client_ro = self._start_mcp(read_only=True, writable_inbox=None)
        is_err_ro, err_txt, _, _ = client_ro.call_tool("brain_archive_memories", {"older_than_days": 90})
        self.assertTrue(is_err_ro, f"Strict read-only must block archive: {err_txt}")

    def test_r3_archive_respects_writable_inbox_scope(self) -> None:
        (self.vault / "Notes").mkdir(parents=True, exist_ok=True)
        ext_cold = self.vault / "Notes" / "External Old.md"
        ext_cold.write_text(
            "---\ntitle: External Old\ntype: memory/episodic\nstatus: active\n"
            "created_at: 2020-01-01 00:00:00\nupdated_at: 2020-01-01 00:00:00\nlast_accessed: 2020-01-01 00:00:00\n---\n"
            "# External Old\nProtected external note.\n",
            encoding="utf-8",
        )
        inbox_epi = self.vault / "AI-Memory" / "Episodic"
        inbox_epi.mkdir(parents=True, exist_ok=True)
        in_cold = inbox_epi / "Inbox Old.md"
        in_cold.write_text(
            "---\ntitle: Inbox Old\ntype: memory/episodic\nstatus: active\n"
            "created_at: 2020-01-01 00:00:00\nupdated_at: 2020-01-01 00:00:00\nlast_accessed: 2020-01-01 00:00:00\n---\n"
            "# Inbox Old\nCold inbox note.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])

        client = self._start_mcp(writable_inbox="AI-Memory")
        is_err, txt, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 90, "max_access_count": 0},
        )
        self.assertFalse(is_err, txt)
        self.assertTrue(ext_cold.is_file(), "External cold note outside writable_inbox must NEVER be moved")

    def test_r3_archive_no_qualifying_notes_returns_cleanly(self) -> None:
        client = self._start_mcp()
        client.call_tool("brain_auto_capture", {"title": "Fresh Note", "content": "Created right now."})
        is_err, txt, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 3650, "max_access_count": 0},
        )
        self.assertFalse(is_err, txt)


class TestTier2R4VaultSynthesisBoundaries(_Tier2Base):
    """R4 boundary, dry-run, zero-leak, min_group_size, filtering, and inbox containment."""

    def test_r4_vault_synthesis_dry_run_makes_zero_disk_changes(self) -> None:
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        for i in (1, 2):
            (epi_dir / f"2026-11-25 DryRun Note {i}.md").write_text(
                f"---\ntitle: DryRun Note {i}\ntype: memory/episodic\nstatus: active\n"
                "project: DryProj\ntags: [dry, test]\ncreated_at: 2026-11-25\nupdated_at: 2026-11-25\n---\n"
                f"# DryRun Note {i}\nRepeated dry run pattern detail {i}.\n",
                encoding="utf-8",
            )
        before = snapshot_directory(self.vault)

        res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30", "--dry-run"])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(before, snapshot_directory(self.vault))

    def test_r4_vault_synthesis_redacts_secrets_in_source_episodic_notes(self) -> None:
        secrets = make_fake_secrets()
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        for idx, tok in enumerate((secrets["openai"], secrets["github"]), 1):
            (epi_dir / f"2026-11-25 Secret Incident {idx}.md").write_text(
                f"---\ntitle: Secret Incident {idx}\ntype: memory/episodic\nstatus: active\n"
                "project: SecProj\ntags: [security, incident]\ncreated_at: 2026-11-25\nupdated_at: 2026-11-25\n---\n"
                f"# Secret Incident {idx}\nCaught secret {tok} in build log.\n",
                encoding="utf-8",
            )

        res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30"])
        self.assertEqual(res.returncode, 0, res.stderr)

        sem_files = list((self.vault / "50_Memory" / "Semantic").glob("*.md"))
        self.assertGreater(len(sem_files), 0)
        synth_text = "\n".join(p.read_text(encoding="utf-8") for p in sem_files)
        self.assertNotIn(secrets["openai"], synth_text)
        self.assertNotIn(secrets["github"], synth_text)

    def test_r4_vault_synthesis_respects_min_group_size(self) -> None:
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        (epi_dir / "2026-11-25 Lone Episodic.md").write_text(
            "---\ntitle: Lone Episodic\ntype: memory/episodic\nstatus: active\n"
            "project: SoloProj\ntags: [solo]\ncreated_at: 2026-11-25\nupdated_at: 2026-11-25\n---\n"
            "# Lone Episodic\nSingle isolated event.\n",
            encoding="utf-8",
        )

        res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30", "--min-group-size", "2"])
        self.assertEqual(res.returncode, 0, res.stderr)
        sem_dir = self.vault / "50_Memory" / "Semantic"
        sem_files = list(sem_dir.glob("*.md")) if sem_dir.exists() else []
        self.assertEqual(sem_files, [])

    def test_r4_vault_synthesis_skips_archived_superseded_and_old_notes(self) -> None:
        epi_dir = self.vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        (epi_dir / "2020-01-01 Ancient Note.md").write_text(
            "---\ntitle: Ancient Note\ntype: memory/episodic\nstatus: active\n"
            "project: OldProj\ncreated_at: 2020-01-01\nupdated_at: 2020-01-01\n---\n# Ancient\nOld.\n",
            encoding="utf-8",
        )
        (epi_dir / "2026-11-25 Archived Note.md").write_text(
            "---\ntitle: Archived Note\ntype: memory/episodic\nstatus: archived\n"
            "project: OldProj\ncreated_at: 2026-11-25\nupdated_at: 2026-11-25\n---\n# Archived\nArchived.\n",
            encoding="utf-8",
        )
        (epi_dir / "2026-11-25 Superseded Note.md").write_text(
            "---\ntitle: Superseded Note\ntype: memory/episodic\nstatus: superseded\n"
            "project: OldProj\ncreated_at: 2026-11-25\nupdated_at: 2026-11-25\n---\n# Superseded\nSuperseded.\n",
            encoding="utf-8",
        )

        res = run_vault_synthesis(["--vault", str(self.vault), "--days", "30", "--min-group-size", "2"])
        self.assertEqual(res.returncode, 0, res.stderr)
        sem_dir = self.vault / "50_Memory" / "Semantic"
        sem_files = list(sem_dir.glob("*.md")) if sem_dir.exists() else []
        self.assertEqual(sem_files, [])

    def test_r4_vault_synthesis_writable_inbox_containment(self) -> None:
        (self.vault / "Notes").mkdir(parents=True, exist_ok=True)
        (self.vault / "Notes" / "UserJournal.md").write_text("# User Journal\nDo not modify.\n", encoding="utf-8")
        inbox_epi = self.vault / "AI-Memory" / "Episodic"
        inbox_epi.mkdir(parents=True, exist_ok=True)
        for i in (1, 2):
            (inbox_epi / f"2026-11-25 Inbox Event {i}.md").write_text(
                f"---\ntitle: Inbox Event {i}\ntype: memory/episodic\nstatus: active\n"
                "project: InboxProj\ntags: [inbox]\ncreated_at: 2026-11-25\nupdated_at: 2026-11-25\n---\n"
                f"# Inbox Event {i}\nDetail {i} for synthesis.\n",
                encoding="utf-8",
            )
        before = snapshot_directory(self.vault, exclude_prefixes=("AI-Memory",))

        res = run_vault_synthesis(
            ["--vault", str(self.vault), "--days", "30", "--writable-inbox", "AI-Memory"]
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(before, snapshot_directory(self.vault, exclude_prefixes=("AI-Memory",)))
        self.assertFalse((self.vault / "50_Memory" / "Semantic").exists())
        inbox_sem = list((self.vault / "AI-Memory" / "Semantic").glob("*.md"))
        self.assertGreater(len(inbox_sem), 0)


class TestTier2R5ProtocolAndIsolationBoundaries(_Tier2Base):
    """R5 setup failure paths, MCP framing/errors, strict read-only, sync dry-run, traversal."""

    def test_r5_setup_index_fails_on_missing_or_empty_vault(self) -> None:
        idx_dest = self.root / "idx_dest"
        missing_vault = self.root / "does_not_exist"
        r1 = run_setup(["--mode", "index", "--vault", str(missing_vault), "--dest", str(idx_dest), "--non-interactive"])
        self.assertNotEqual(r1.returncode, 0)

        empty_vault = self.root / "empty_vault"
        empty_vault.mkdir()
        r2 = run_setup(["--mode", "index", "--vault", str(empty_vault), "--dest", str(idx_dest), "--non-interactive"])
        self.assertNotEqual(r2.returncode, 0)

    def test_r5_mcp_content_length_framing_and_error_responses(self) -> None:
        client = self._start_mcp()

        ping_resp = client.send_content_length_request("ping", {})
        self.assertEqual(ping_resp.get("result"), {})

        unk_method = client.send_request("nonexistent/method", {})
        self.assertEqual(unk_method["error"]["code"], -32601)

        is_err, err_txt, _, _ = client.call_tool("brain_nonexistent_tool", {})
        self.assertTrue(is_err)
        self.assertIn("Unknown tool", err_txt)

        bad_json_resp = client.send_raw("{not valid json\n", expect_response=True)
        assert bad_json_resp is not None
        self.assertEqual(bad_json_resp["error"]["code"], -32700)

    def test_r5_strict_read_only_blocks_all_mutating_mcp_tools(self) -> None:
        (self.vault / "Notes").mkdir(parents=True, exist_ok=True)
        (self.vault / "Notes" / "Seed.md").write_text("# Seed\nRead only content.\n", encoding="utf-8")
        run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path)])
        snap_before = snapshot_directory(self.vault, exclude_prefixes=("90_System",))

        client = self._start_mcp(read_only=True, writable_inbox=None)
        for tool_name, args in (
            ("brain_add_memory", {"title": "T1", "content": "Blocked write"}),
            ("brain_auto_capture", {"title": "T2", "content": "Blocked auto capture"}),
            ("brain_log_handoff", {"agent": "Cursor", "activity": "Blocked handoff"}),
            ("brain_archive_memories", {"older_than_days": 90}),
        ):
            is_err, err_txt, _, _ = client.call_tool(tool_name, args)
            self.assertTrue(is_err, f"{tool_name} must fail in strict --read-only mode: {err_txt}")

        self.assertEqual(snap_before, snapshot_directory(self.vault, exclude_prefixes=("90_System",)))

    def test_r5_sync_db_dry_run_and_ignored_directory_exclusion(self) -> None:
        (self.vault / ".git").mkdir(parents=True, exist_ok=True)
        (self.vault / ".git" / "ignored.md").write_text("# Git Ignored\n", encoding="utf-8")
        (self.vault / ".obsidian").mkdir(parents=True, exist_ok=True)
        (self.vault / ".obsidian" / "ignored.md").write_text("# Obsidian Ignored\n", encoding="utf-8")
        (self.vault / "50_Memory" / "Semantic").mkdir(parents=True, exist_ok=True)
        (self.vault / "50_Memory" / "Semantic" / "Valid.md").write_text("# Valid\nIndexed.\n", encoding="utf-8")

        dry = run_sync_db(["--vault", str(self.vault), "--db", str(self.db_path), "--dry-run"])
        self.assertEqual(dry.returncode, 0, dry.stderr)
        summary = json.loads(dry.stdout)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["total_scanned"], 1)
        self.assertEqual(len(summary["added"]), 1)

        rows = query_db_readonly(self.db_path, "SELECT count(*) as cnt FROM memories")
        self.assertEqual(rows[0]["cnt"], 0, "sync_db --dry-run must not write rows to SQLite")

    def test_r5_path_traversal_contained_in_add_memory_and_writable_inbox(self) -> None:
        client = self._start_mcp(writable_inbox="AI-Memory")
        is_err, txt, data, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Traversal Probe",
                "content": "Attempted path escape.",
                "path": "../../Outside/Escape.md",
            },
        )
        self.assertFalse(is_err, txt)
        self.assertTrue(data["path"].startswith("AI-Memory/"))
        self.assertFalse((self.root / "Outside" / "Escape.md").exists())
        self.assertFalse((self.vault / "Outside" / "Escape.md").exists())
        self.assertTrue((self.vault / data["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
