"""Tier 4 E2E Tests: Real-World Multi-Step Application Workflows & Release Gate (R1-R5).

Exercises end-to-end user journeys from onboarding through multi-agent handoffs,
vault isolation, lifecycle archiving, episodic synthesis, and repository coherence.
"""

from __future__ import annotations

import ast
import json
import re
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


class TestTier4Workflows(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aibrain_e2e_t4_")
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def test_t4_workflow_1_multi_agent_handoff_and_continuity(self) -> None:
        """Workflow 1: Setup fresh vault -> Agent A captures + hands off -> Agent B continues via crew_log & retrieval."""
        vault = self.root / "team_vault"
        setup_res = run_setup(["--mode", "fresh", "--dest", str(vault), "--agents", "all", "--non-interactive"])
        self.assertEqual(setup_res.returncode, 0, setup_res.stderr)
        cfg_path = vault / "mcp_ready_config.json"

        # Agent A (Cursor) session
        agent_a = McpStdioClient.from_mcp_config(cfg_path)
        self.addCleanup(agent_a.close)
        agent_a.initialize()

        err_a1, txt_a1, rec_a, _ = agent_a.call_tool(
            "brain_auto_capture",
            {
                "title": "TaskFlow Webhook Signature Verification",
                "content": "Verify HMAC-SHA256 webhook headers with constant-time compare before enqueueing tasks.",
                "memory_type": "memory/semantic",
                "project": "TaskFlow",
                "tags": ["taskflow", "webhooks", "security"],
            },
        )
        self.assertFalse(err_a1, txt_a1)
        note_a_text = (vault / rec_a["path"]).read_text(encoding="utf-8")
        self.assertIn("TaskFlow", extract_wikilinks(note_a_text))

        err_h1, txt_h1, _, _ = agent_a.call_tool(
            "brain_log_handoff",
            {
                "agent": "Cursor",
                "activity": "Implemented HMAC-SHA256 webhook signature validator",
                "files": ["api/webhooks.py", rec_a["path"]],
                "open_items": ["Add replay window timestamp check"],
                "project": "TaskFlow",
            },
        )
        self.assertFalse(err_h1, txt_h1)
        agent_a.close()

        # Agent B (Claude) session picks up work
        agent_b = McpStdioClient.from_mcp_config(cfg_path)
        self.addCleanup(agent_b.close)
        agent_b.initialize()

        err_cl, crew_txt, _, _ = agent_b.call_tool("brain_read_crew_log", {"limit": 5})
        self.assertFalse(err_cl, crew_txt)
        self.assertIn("Cursor", crew_txt)
        self.assertIn("Add replay window timestamp check", crew_txt)

        err_ret, ret_txt, recalled, _ = agent_b.call_tool(
            "brain_auto_retrieve",
            {"query": "HMAC-SHA256 webhook signature TaskFlow", "limit": 5},
        )
        self.assertFalse(err_ret, ret_txt)
        self.assertGreater(len(recalled), 0)
        self.assertEqual(recalled[0]["title"], "TaskFlow Webhook Signature Verification")

        err_b1, txt_b1, rec_b, _ = agent_b.call_tool(
            "brain_auto_capture",
            {
                "title": "TaskFlow Webhook Replay Protection",
                "content": "Reject TaskFlow webhook timestamps older than 300 seconds to prevent replay attacks.",
                "memory_type": "memory/procedural",
                "project": "TaskFlow",
                "tags": ["taskflow", "webhooks", "replay"],
            },
        )
        self.assertFalse(err_b1, txt_b1)
        note_b_links = extract_wikilinks((vault / rec_b["path"]).read_text(encoding="utf-8"))
        self.assertIn("TaskFlow", note_b_links)
        self.assertIn(Path(rec_a["path"]).stem, note_b_links)

        err_h2, txt_h2, _, _ = agent_b.call_tool(
            "brain_log_handoff",
            {
                "agent": "Claude",
                "activity": "Added 300s timestamp replay guard and documented SOP",
                "files": ["api/webhooks.py", rec_b["path"]],
                "open_items": ["Ready for QA signoff"],
                "project": "TaskFlow",
            },
        )
        self.assertFalse(err_h2, txt_h2)

        crew_disk = (vault / "50_Memory" / "Working" / "crew_log.md").read_text(encoding="utf-8")
        self.assertLess(
            crew_disk.find("Implemented HMAC-SHA256 webhook signature validator"),
            crew_disk.find("Added 300s timestamp replay guard"),
        )

    def test_t4_workflow_2_existing_obsidian_vault_safe_adoption(self) -> None:
        """Workflow 2: Existing vault + --writable-inbox AI-Memory -> MCP + Synthesis -> zero external mutations."""
        user_vault = self.root / "obsidian_user_vault"
        idx_dest = self.root / "ai_brain_index"
        (user_vault / "10_Projects").mkdir(parents=True, exist_ok=True)
        (user_vault / "Notes").mkdir(parents=True, exist_ok=True)

        (user_vault / "10_Projects" / "Atlas.md").write_text(
            "---\ntitle: Atlas\ntype: project\nstatus: active\n---\n# Atlas\nSearch engine.\n",
            encoding="utf-8",
        )
        (user_vault / "Notes" / "Personal Journal.md").write_text(
            "# Personal Journal\nMy private reflections must never be altered by AI agents.\n",
            encoding="utf-8",
        )
        (user_vault / "Notes" / "Client Architecture.md").write_text(
            "---\ntitle: Client Architecture\ntype: memory/semantic\nproject: Atlas\n---\n"
            "# Client Architecture\nCore entities: Workspace, Document, Query.\n",
            encoding="utf-8",
        )
        before_snapshot = snapshot_directory(user_vault)

        setup_res = run_setup(
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
        self.assertEqual(setup_res.returncode, 0, setup_res.stderr)

        client = McpStdioClient.from_mcp_config(idx_dest / "mcp_ready_config.json")
        self.addCleanup(client.close)
        client.initialize()

        # Search existing user notes
        err_s, txt_s, found, _ = client.call_tool("brain_auto_retrieve", {"query": "Workspace Document Query"})
        self.assertFalse(err_s, txt_s)
        self.assertGreater(len(found), 0)

        # Complementary update against external note must create scoped reference inside AI-Memory/
        err_c, txt_c, scoped_rec, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Client Architecture Extensions",
                "content": "Core entities: Workspace, Document, Query. Additional notes: added IndexShard.",
                "project": "Atlas",
            },
        )
        self.assertFalse(err_c, txt_c)
        self.assertTrue(scoped_rec["path"].startswith("AI-Memory/"))
        self.assertEqual(scoped_rec["deduplication"]["action"], "created_scoped_reference")
        self.assertIn("Atlas", extract_wikilinks((user_vault / scoped_rec["path"]).read_text(encoding="utf-8")))

        # Capture episodic notes inside AI-Memory and run vault_synthesis inside AI-Memory
        today = time.strftime("%Y-%m-%d")
        for i in (1, 2):
            client.call_tool(
                "brain_auto_capture",
                {
                    "title": f"{today} Atlas Query Tuning {i}",
                    "content": f"Atlas query tuning benchmark {i} reduced p99 latency under 10ms.",
                    "memory_type": "memory/episodic",
                    "project": "Atlas",
                    "tags": ["atlas", "tuning"],
                },
            )

        client.call_tool(
            "brain_log_handoff",
            {
                "agent": "Antigravity",
                "activity": "Indexed Atlas and captured query tuning benchmarks",
                "files": [scoped_rec["path"]],
                "open_items": ["Verify synthesis output"],
            },
        )
        client.close()

        synth_res = run_vault_synthesis(
            [
                "--vault",
                str(user_vault),
                "--db",
                str(idx_dest / "brain_memory.sqlite"),
                "--days", "30",
                "--target-type", "memory/semantic",
                "--writable-inbox", "AI-Memory",
            ],
            script_path=idx_dest / "tools" / "vault_synthesis.py",
        )
        self.assertEqual(synth_res.returncode, 0, synth_res.stderr)

        after_snapshot = snapshot_directory(user_vault, exclude_prefixes=("AI-Memory",))
        self.assertEqual(
            before_snapshot,
            after_snapshot,
            "Existing user notes outside AI-Memory were modified!",
        )

    def test_t4_workflow_3_memory_lifecycle_recency_archive_and_rebuild(self) -> None:
        """Workflow 3: Capture -> sync -> retrieve/recency boost -> explicit archive -> DB rebuild round-trip."""
        vault = self.root / "lifecycle_vault"
        setup_res = run_setup(["--mode", "fresh", "--dest", str(vault), "--agents", "claude", "--non-interactive"])
        self.assertEqual(setup_res.returncode, 0, setup_res.stderr)
        db_path = vault / "90_System" / "db" / "brain_memory.sqlite"

        # Seed a stale 2020 episodic note that stays unaccessed and an older comparison note
        cold_path = vault / "50_Memory" / "Episodic" / "2020-01-15 Deprecated Cache Archive.md"
        cold_path.write_text(
            "---\n"
            'title: "Deprecated Cache Archive"\n'
            "type: memory/episodic\n"
            "status: active\n"
            "created_at: 2020-01-15 00:00:00\n"
            "updated_at: 2020-01-15 00:00:00\n"
            "last_accessed: 2020-01-15 00:00:00\n"
            "---\n\n"
            "# Deprecated Cache Archive\n\n"
            "Unaccessed legacy spool topology note from 2020.\n",
            encoding="utf-8",
        )
        older_comp_path = vault / "50_Memory" / "Episodic" / "2020-02-01 Legacy Cluster Cache.md"
        older_comp_path.write_text(
            "---\n"
            'title: "Legacy Cluster Cache"\n'
            "type: memory/episodic\n"
            "status: active\n"
            "created_at: 2020-02-01 00:00:00\n"
            "updated_at: 2020-02-01 00:00:00\n"
            "last_accessed: 2020-02-01 00:00:00\n"
            "---\n\n"
            "# Legacy Cluster Cache\n\n"
            "Memcached shard topology invariant for quorum nodes.\n",
            encoding="utf-8",
        )
        run_sync_db(["--vault", str(vault), "--db", str(db_path)])

        client = McpStdioClient.from_mcp_config(vault / "mcp_ready_config.json")
        self.addCleanup(client.close)
        client.initialize()

        err_add, txt_add, active_rec, _ = client.call_tool(
            "brain_auto_capture",
            {
                "title": "Active Memory Design",
                "content": "Memcached shard topology invariant for quorum nodes.",
                "memory_type": "memory/semantic",
                "project": "TaskFlow",
            },
        )
        self.assertFalse(err_add, txt_add)
        orig_updated = active_rec["updated_at"]
        time.sleep(1.1)

        # Read active_rec to boost last_accessed without changing updated_at
        err_rd, txt_rd, rd_rec, _ = client.call_tool("brain_read_memory", {"path": active_rec["path"]})
        self.assertFalse(err_rd, txt_rd)
        self.assertEqual(rd_rec["updated_at"], orig_updated)
        self.assertTrue(rd_rec["last_accessed"])

        # Retrieval ranks active_rec above older_comp_path and does NOT archive cold_path
        err_ret, txt_ret, results, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "Memcached shard topology invariant quorum nodes", "limit": 5},
        )
        self.assertFalse(err_ret, txt_ret)
        self.assertEqual(results[0]["title"], "Active Memory Design")
        self.assertTrue(cold_path.is_file(), "Retrieval must NOT archive cold note")

        # Dry-run archive leaves cold_path intact
        err_dry, txt_dry, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 90, "max_access_count": 0, "dry_run": True},
        )
        self.assertFalse(err_dry, txt_dry)
        self.assertTrue(cold_path.is_file())

        # Explicit archive moves cold_path to 40_Archive/
        err_arch, txt_arch, _, _ = client.call_tool(
            "brain_archive_memories",
            {"older_than_days": 90, "max_access_count": 0, "dry_run": False},
        )
        self.assertFalse(err_arch, txt_arch)
        self.assertFalse(cold_path.exists())
        archived = list((vault / "40_Archive").rglob("2020-01-15 Deprecated Cache Archive.md"))
        self.assertEqual(len(archived), 1)
        fm_arch, _ = parse_md_frontmatter(archived[0].read_text(encoding="utf-8"))
        self.assertEqual(fm_arch.get("status"), "archived")
        self.assertTrue(fm_arch.get("archived_at"))
        client.close()

        # Rebuild SQLite DB from scratch and confirm persisted states
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()
        rebuild_res = run_sync_db(["--vault", str(vault), "--db", str(db_path)])
        self.assertEqual(rebuild_res.returncode, 0, rebuild_res.stderr)

        active_rows = query_db_readonly(
            db_path,
            "SELECT last_accessed, status FROM memories WHERE path = ?",
            (active_rec["path"],),
        )
        self.assertEqual(active_rows[0]["status"], "active")
        self.assertEqual(active_rows[0]["last_accessed"], rd_rec["last_accessed"])

        arch_rows = query_db_readonly(
            db_path,
            "SELECT status, path FROM memories WHERE title = 'Deprecated Cache Archive'",
        )
        self.assertEqual(arch_rows[0]["status"], "archived")
        self.assertTrue(arch_rows[0]["path"].startswith("40_Archive/"))

    def test_t4_workflow_4_episodic_accumulation_to_synthesis_and_retrieval(self) -> None:
        """Workflow 4: Episodic notes (with secret) -> vault_synthesis Semantic & Procedural -> sync -> retrieve."""
        vault = self.root / "synth_vault"
        db_path = vault / "90_System" / "db" / "brain_memory.sqlite"
        epi_dir = vault / "50_Memory" / "Episodic"
        epi_dir.mkdir(parents=True, exist_ok=True)
        secrets = make_fake_secrets()
        today = time.strftime("%Y-%m-%d")

        episodic_payloads = [
            (f"{today} Deploy Step 1.md", f"Run database migrations safely before rollout (never leak {secrets['openai']})."),
            (f"{today} Deploy Step 2.md", "Execute smoke suite against canary endpoint after database migrations."),
            (f"{today} Deploy Step 3.md", "Promote canary deployment to 100% traffic once p99 error rate is zero."),
        ]
        source_stems: list[str] = []
        for fname, body in episodic_payloads:
            p = epi_dir / fname
            p.write_text(
                f"---\ntitle: \"{p.stem}\"\ntype: memory/episodic\nstatus: active\n"
                f"project: \"ReleaseOps\"\ntags: [deploy, canary, migrations]\ncreated_at: {today}\nupdated_at: {today}\n---\n"
                f"# {p.stem}\n\n{body}\n",
                encoding="utf-8",
            )
            source_stems.append(p.stem)

        # Initialize/sync database before vault_synthesis (which reads from SQLite, not filesystem)
        r_sync = run_sync_db(["--vault", str(vault), "--db", str(db_path)])
        self.assertEqual(r_sync.returncode, 0, r_sync.stderr)

        r_sem = run_vault_synthesis(["--vault", str(vault), "--days", "30", "--target-type", "memory/semantic"])
        self.assertEqual(r_sem.returncode, 0, r_sem.stderr)
        r_proc = run_vault_synthesis(["--vault", str(vault), "--days", "30", "--target-type", "memory/procedural"])
        self.assertEqual(r_proc.returncode, 0, r_proc.stderr)

        sem_files = list((vault / "50_Memory" / "Semantic").glob("*.md"))
        proc_files = list((vault / "50_Memory" / "Procedural").glob("*.md"))
        self.assertGreater(len(sem_files), 0)
        self.assertGreater(len(proc_files), 0)

        for synth_file in sem_files + proc_files:
            content = synth_file.read_text(encoding="utf-8")
            self.assertNotIn(secrets["openai"], content)
            links = extract_wikilinks(content)
            for stem in source_stems:
                self.assertIn(stem, links)

        client = McpStdioClient(vault_path=vault, db_path=db_path)
        self.addCleanup(client.close)
        client.initialize()
        err_sync, txt_sync, _, _ = client.call_tool("brain_sync", {})
        self.assertFalse(err_sync, txt_sync)

        err_ret, txt_ret, results, _ = client.call_tool(
            "brain_auto_retrieve",
            {"query": "database migrations canary deployment", "memory_type": "memory/procedural"},
        )
        self.assertFalse(err_ret, txt_ret)
        self.assertGreater(len(results), 0)

    def test_t4_workflow_5_repository_coherence_and_production_readiness_gate(self) -> None:
        """Workflow 5: Full repository coherence across vault-template, agents/*, skills/, README, CONTRIBUTING, and stdlib AST."""
        vt = REPO_ROOT / "vault-template"
        crew_tmpl = vt / "50_Memory" / "Working" / "crew_log.md"
        self.assertTrue(crew_tmpl.is_file(), "vault-template/50_Memory/Working/crew_log.md missing")
        self.assertGreater(len(crew_tmpl.read_text(encoding="utf-8").strip()), 20)

        schema_doc = (vt / "90_System" / "MEMORY_SCHEMA.md").read_text(encoding="utf-8")
        self.assertIn("last_accessed", schema_doc)

        archive_doc = (vt / "40_Archive" / "README.md").read_text(encoding="utf-8")
        self.assertIn("brain_archive_memories", archive_doc)

        canvas = json.loads((vt / "Neural-Map.canvas").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(canvas.get("nodes", [])), 6)
        self.assertGreaterEqual(len(canvas.get("edges", [])), 5)

        skill_md = REPO_ROOT / "skills" / "vault-synthesis" / "SKILL.md"
        self.assertTrue(skill_md.is_file(), "skills/vault-synthesis/SKILL.md missing")
        self.assertIn("vault_synthesis.py", skill_md.read_text(encoding="utf-8"))

        agent_rule_paths = [
            REPO_ROOT / "agents" / "claude" / "CLAUDE.md",
            REPO_ROOT / "agents" / "cursor" / ".cursorrules",
            REPO_ROOT / "agents" / "antigravity" / "AGENTS.md",
            REPO_ROOT / "agents" / "gemini" / "GEMINI.md",
        ]
        for rule_path in agent_rule_paths:
            txt = rule_path.read_text(encoding="utf-8")
            self.assertIn("brain_log_handoff", txt, f"{rule_path.name} must document brain_log_handoff")
            self.assertIn("brain_read_crew_log", txt, f"{rule_path.name} must document brain_read_crew_log")
            self.assertIn("brain_archive_memories", txt, f"{rule_path.name} must document brain_archive_memories")

        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for heading in (
            "# AI-BRAIN",
            "## What Problem It Solves",
            "## Installation (60 Seconds)",
            "## How AI-BRAIN is Structured",
            "## Privacy & Security",
        ):
            self.assertIn(heading, readme)
        self.assertEqual(len(re.findall(r"^#{1,2}\s+", readme, re.MULTILINE)), 5, "README must have strictly 5 top-level sections")

        # Verify zero external imports across all E2E tests as well as core modules
        local_modules = {
            p.stem
            for folder in ("mcp-server", "tools", "tests/e2e")
            for p in (REPO_ROOT / folder).glob("*.py")
        } | {"setup", "tests"}
        for py_file in sorted((REPO_ROOT / "tests" / "e2e").glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        self.assertTrue(top in local_modules or is_stdlib_module(top), f"Forbidden import {alias.name} in {py_file.name}")
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    top = node.module.split(".")[0]
                    self.assertTrue(top in local_modules or is_stdlib_module(top), f"Forbidden import {node.module} in {py_file.name}")


if __name__ == "__main__":
    unittest.main()
