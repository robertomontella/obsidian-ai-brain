"""Unit tests for Markdown <-> SQLite FTS5 bidirectional sync tool."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mcp-server"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from storage import StorageManager
from sync_db import parse_frontmatter, scan_vault, sync_vault


class TestSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault_path = Path(self.temp_dir.name).resolve() / "vault"
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.vault_path / "90_System" / "db" / "brain_memory.sqlite"

        # Initialize storage
        self.storage = StorageManager(db_path=self.db_path)
        self.storage.init_db()

        # Create dummy notes in vault
        self.note1_path = self.vault_path / "50_Memory" / "Semantic" / "TaskFlow Domain.md"
        self.note1_path.parent.mkdir(parents=True, exist_ok=True)
        self.note1_content = (
            "---\n"
            "title: TaskFlow Domain Model\n"
            "type: memory/semantic\n"
            "tags:\n"
            "  - domain\n"
            "  - entities\n"
            "status: active\n"
            "priority: Alta\n"
            "project: TaskFlow\n"
            "importance_score: 4.0\n"
            "---\n"
            "\n"
            "# TaskFlow Domain Model\n"
            "\n"
            "This document describes the workflow DAG and task scheduling model.\n"
        )
        self.note1_path.write_text(self.note1_content, encoding="utf-8")

        self.note2_path = self.vault_path / "10_Projects" / "Project Alpha.md"
        self.note2_path.parent.mkdir(parents=True, exist_ok=True)
        self.note2_content = (
            "---\n"
            "title: Project Alpha Overview\n"
            "type: project\n"
            "status: active\n"
            "tags: [alpha, internal]\n"
            "---\n"
            "# Alpha Overview\n"
            "Project Alpha focuses on high-throughput microservices.\n"
        )
        self.note2_path.write_text(self.note2_content, encoding="utf-8")

        # Create ignored folder (.git, .obsidian)
        (self.vault_path / ".git").mkdir()
        (self.vault_path / ".git" / "IGNORE.md").write_text("Should be ignored", encoding="utf-8")
        (self.vault_path / ".obsidian").mkdir()
        (self.vault_path / ".obsidian" / "workspace.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    def test_parse_frontmatter(self):
        """Validates pure-Python YAML frontmatter parsing and body extraction."""
        meta, body = parse_frontmatter(self.note1_content)
        self.assertEqual(meta.get("title"), "TaskFlow Domain Model")
        self.assertEqual(meta.get("type"), "memory/semantic")
        self.assertEqual(meta.get("priority"), "Alta")
        self.assertEqual(meta.get("importance_score"), 4.0)
        self.assertIn("domain", meta.get("tags", []))
        self.assertIn("entities", meta.get("tags", []))
        self.assertIn("# TaskFlow Domain Model", body)

        # Test note with inline tag array and no quotes
        meta2, body2 = parse_frontmatter(self.note2_content)
        self.assertEqual(meta2.get("title"), "Project Alpha Overview")
        self.assertIn("alpha", meta2.get("tags", []))
        self.assertIn("internal", meta2.get("tags", []))

        # Test note ending immediately at closing --- without trailing newline or body
        no_newline_content = "---\ntitle: Immediate Ending\ntype: note\n---"
        meta3, body3 = parse_frontmatter(no_newline_content)
        self.assertEqual(meta3.get("title"), "Immediate Ending")
        self.assertEqual(meta3.get("type"), "note")
        self.assertEqual(body3, "")

    def test_scan_vault_ignores_system_and_hidden_dirs(self):
        """Vault scanner ignores .git, .obsidian, and 90_System/db."""
        files = scan_vault(self.vault_path)
        rel_paths = list(files.keys())
        self.assertIn("50_Memory/Semantic/TaskFlow Domain.md", rel_paths)
        self.assertIn("10_Projects/Project Alpha.md", rel_paths)
        for p in rel_paths:
            self.assertFalse(p.startswith(".git"))
            self.assertFalse(p.startswith(".obsidian"))
            self.assertFalse(p.startswith("90_System/db"))

    def test_initial_sync_and_fts5_indexing(self):
        """Initial sync indexes notes in SQLite and makes them searchable via FTS5."""
        summary = sync_vault(vault_path=self.vault_path, storage=self.storage)
        self.assertEqual(summary["total_scanned"], 2)
        self.assertEqual(len(summary["added"]), 2)
        self.assertEqual(len(summary["updated"]), 0)
        self.assertEqual(len(summary["deleted"]), 0)

        # Verify FTS5 search works
        results = self.storage.search_memory("workflow DAG scheduling")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["title"], "TaskFlow Domain Model")

    def test_modified_note_incremental_sync(self):
        """Modified note is detected and updated in DB via UPSERT, updating FTS5 index."""
        sync_vault(vault_path=self.vault_path, storage=self.storage)

        # Modify note1
        updated_content = (
            "---\n"
            "title: TaskFlow Domain Model\n"
            "type: memory/semantic\n"
            "status: active\n"
            "---\n"
            "# TaskFlow Domain Model\n"
            "Updated architecture with distributed EventStore and Kafka streaming.\n"
        )
        self.note1_path.write_text(updated_content, encoding="utf-8")

        summary2 = sync_vault(vault_path=self.vault_path, storage=self.storage)
        self.assertEqual(len(summary2["updated"]), 1)
        self.assertEqual(len(summary2["added"]), 0)
        self.assertIn("50_Memory/Semantic/TaskFlow Domain.md", summary2["updated"])

        # FTS5 search reflects the update
        results = self.storage.search_memory("Kafka streaming EventStore")
        self.assertEqual(len(results), 1)
        self.assertIn("Kafka streaming", results[0]["content"])

    def test_deleted_note_sync(self):
        """Deleted markdown file is removed from SQLite database and FTS5 index."""
        sync_vault(vault_path=self.vault_path, storage=self.storage)

        # Delete note2
        self.note2_path.unlink()

        summary = sync_vault(vault_path=self.vault_path, storage=self.storage)
        self.assertEqual(len(summary["deleted"]), 1)
        self.assertIn("10_Projects/Project Alpha.md", summary["deleted"])

        # Search should not find note2
        results = self.storage.search_memory("Project Alpha microservices")
        self.assertEqual(len(results), 0)

    def test_moved_note_sync(self):
        """Moving or renaming a note without altering content reconciles path."""
        sync_vault(vault_path=self.vault_path, storage=self.storage)

        # Move note2 to 40_Archive
        dest_dir = self.vault_path / "40_Archive"
        dest_dir.mkdir(parents=True, exist_ok=True)
        new_path = dest_dir / "Project Alpha Archived.md"
        shutil.move(str(self.note2_path), str(new_path))

        summary = sync_vault(vault_path=self.vault_path, storage=self.storage)
        self.assertTrue(len(summary["updated"]) > 0 or len(summary["added"]) > 0)

        # Verify record exists under new path
        rec = self.storage.read_memory("40_Archive/Project Alpha Archived.md")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["title"], "Project Alpha Overview")


if __name__ == "__main__":
    unittest.main()
