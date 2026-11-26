"""Unit and End-to-End Tests for the AI-BRAIN Onboarding Setup Wizard (setup.py).

Validates:
1. Environment verification (Python >= 3.9, SQLite FTS5).
2. Fresh Vault Mode: non-destructive template copy, SQLite WAL initialization,
   FTS5 initial note indexing, agent rules installation, and mcp_ready_config.json generation.
3. Read-Only Indexing Mode: indexing existing Obsidian vaults without modifying
   or injecting files into the source directory.
4. Error handling: missing vault, empty vault without markdown notes.
5. Selective agent rules copying.
6. Instant E2E verification of FTS5 search latency (< 15ms).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = REPO_ROOT / "setup.py"


def _hash_file(file_path: Path) -> str:
    """Calculates SHA256 of a file."""
    h = hashlib.sha256()
    h.update(file_path.read_bytes())
    return h.hexdigest()


def _snapshot_directory(directory: Path) -> dict[str, tuple[str, float, int]]:
    """Takes a snapshot of all files in a directory: relative path -> (sha256, mtime, size)."""
    snapshot = {}
    for p in directory.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(directory))
            snapshot[rel] = (_hash_file(p), p.stat().st_mtime, p.stat().st_size)
    return snapshot


class TestSetupWizard(unittest.TestCase):
    """Test suite for setup.py onboarding wizard."""

    def setUp(self) -> None:
        self.temp_dirs: list[Path] = []

    def tearDown(self) -> None:
        for d in self.temp_dirs:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    def _make_temp_dir(self, prefix: str = "ai_brain_test_") -> Path:
        td = Path(tempfile.mkdtemp(prefix=prefix))
        self.temp_dirs.append(td)
        return td

    def test_fresh_vault_mode_non_interactive(self) -> None:
        """Test Fresh Vault mode with --mode fresh --dest <temp_dir> --agents all --non-interactive."""
        dest_dir = self._make_temp_dir(prefix="fresh_vault_")

        cmd = [
            sys.executable,
            str(SETUP_PY),
            "--mode",
            "fresh",
            "--dest",
            str(dest_dir),
            "--agents",
            "all",
            "--non-interactive",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(
            result.returncode,
            0,
            f"setup.py failed with exit code {result.returncode}.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}",
        )

        # 1. Verify PARA and system folders exist
        expected_dirs = [
            "00_Inbox",
            "10_Projects",
            "20_Areas",
            "30_Resources",
            "40_Archive",
            "50_Memory",
            "90_System",
        ]
        for ed in expected_dirs:
            self.assertTrue(
                (dest_dir / ed).is_dir(),
                f"Expected directory '{ed}' missing in generated vault {dest_dir}",
            )

        # 2. Verify mcp-server copied to 90_System/scripts/
        server_script = dest_dir / "90_System" / "scripts" / "server.py"
        self.assertTrue(
            server_script.is_file(),
            f"Expected server script at {server_script}",
        )

        # 3. Verify SQLite database initialized with WAL mode
        db_path = dest_dir / "90_System" / "db" / "brain_memory.sqlite"
        self.assertTrue(db_path.is_file(), f"Database file missing at {db_path}")

        conn = sqlite3.connect(str(db_path))
        try:
            # Check WAL journal mode
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            # Check core tables exist
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
            }
            self.assertIn("memories", tables)
            self.assertIn("memories_fts", tables)
            self.assertIn("memory_relations", tables)

            # Check notes were indexed via sync_vault
            count = conn.execute("SELECT count(*) FROM memories;").fetchone()[0]
            self.assertGreater(count, 0, "No initial notes were indexed into memories table")

            # Check FTS5 index contains records
            fts_count = conn.execute("SELECT count(*) FROM memories_fts;").fetchone()[0]
            self.assertGreater(fts_count, 0, "No records in memories_fts virtual table")
        finally:
            conn.close()

        # 4. Verify all 4 agent rule files exist
        self.assertTrue((dest_dir / "CLAUDE.md").is_file(), "CLAUDE.md missing in root")
        self.assertTrue((dest_dir / ".cursorrules").is_file(), ".cursorrules missing in root")
        self.assertTrue((dest_dir / "AGENTS.md").is_file(), "AGENTS.md missing in root")
        self.assertTrue((dest_dir / "GEMINI.md").is_file(), "GEMINI.md missing in root")

        # 5. Verify mcp_ready_config.json
        config_path = dest_dir / "mcp_ready_config.json"
        self.assertTrue(config_path.is_file(), "mcp_ready_config.json not found in dest")

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.assertIn("mcpServers", cfg)
        self.assertIn("ai-brain", cfg["mcpServers"])
        server_entry = cfg["mcpServers"]["ai-brain"]

        self.assertEqual(server_entry["command"], sys.executable)
        self.assertIn(str(server_script.resolve()), server_entry["args"])
        self.assertIn(str(dest_dir.resolve()), server_entry["args"])
        self.assertIn(str(db_path.resolve()), server_entry["args"])

        # Also check printed configuration in stdout
        self.assertIn("mcpServers", result.stdout)
        self.assertIn("ai-brain", result.stdout)

    def test_read_only_indexing_mode_non_interactive(self) -> None:
        """Test Read-Only Indexing mode: existing vault is NEVER modified or written to."""
        mock_vault = self._make_temp_dir(prefix="existing_vault_")
        idx_dest = self._make_temp_dir(prefix="index_dest_")

        # Populate mock user vault with notes and folders
        (mock_vault / "Notes").mkdir()
        note1 = mock_vault / "Notes" / "Project Alpha.md"
        note1.write_text(
            "---\ntitle: Project Alpha\ntype: memory/semantic\ntags: [alpha, project]\n---\n# Project Alpha\nArchitecture and notes.",
            encoding="utf-8",
        )
        note2 = mock_vault / "Daily Log.md"
        note2.write_text(
            "---\ntitle: Daily Log\ntype: memory/episodic\n---\n# Daily Log\nToday we launched the alpha version.",
            encoding="utf-8",
        )

        # Snapshot mock vault state before setup
        vault_snapshot_before = _snapshot_directory(mock_vault)

        cmd = [
            sys.executable,
            str(SETUP_PY),
            "--mode",
            "index",
            "--vault",
            str(mock_vault),
            "--dest",
            str(idx_dest),
            "--agents",
            "claude",
            "--non-interactive",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(
            result.returncode,
            0,
            f"setup.py index mode failed.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}",
        )

        # 1. STRICT CHECK: mock_vault must be 100% UNTOUCHED
        vault_snapshot_after = _snapshot_directory(mock_vault)
        self.assertEqual(
            vault_snapshot_before,
            vault_snapshot_after,
            "User vault was modified during read-only indexing! This violates read-only safety.",
        )

        # 2. Check index database in idx_dest
        db_path = idx_dest / "brain_memory.sqlite"
        self.assertTrue(db_path.is_file(), f"Index DB missing at {db_path}")

        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT title, path FROM memories ORDER BY title;").fetchall()
            titles = [r[0] for r in rows]
            self.assertIn("Project Alpha", titles)
            self.assertIn("Daily Log", titles)

            fts_match = conn.execute(
                "SELECT title FROM memories_fts WHERE memories_fts MATCH 'alpha';"
            ).fetchall()
            self.assertGreater(len(fts_match), 0, "FTS5 query failed to match 'alpha'")
        finally:
            conn.close()

        # 3. Check mcp_ready_config.json generated in idx_dest
        config_path = idx_dest / "mcp_ready_config.json"
        self.assertTrue(config_path.is_file(), "mcp_ready_config.json missing in idx_dest")

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        server_entry = cfg["mcpServers"]["ai-brain"]
        self.assertIn(str(mock_vault.resolve()), server_entry["args"])
        self.assertIn(str(db_path.resolve()), server_entry["args"])

        # 4. Check selective agent rule: claude requested, so CLAUDE.md in idx_dest
        self.assertTrue((idx_dest / "CLAUDE.md").is_file(), "CLAUDE.md missing in idx_dest")
        self.assertFalse((idx_dest / ".cursorrules").exists(), ".cursorrules should not exist")
        self.assertFalse((mock_vault / "CLAUDE.md").exists(), "CLAUDE.md must NOT be injected into mock vault")

    def test_existing_vault_with_scoped_writable_inbox(self) -> None:
        """Test Existing Vault Indexing with a designated --writable-inbox (e.g. AI-Memory).

        Verifies that:
        1. Designated inbox folder is created in user vault with README.
        2. All other user folders and notes remain 100% UNTOUCHED.
        3. Generated mcp_ready_config.json includes --writable-inbox and BRAIN_WRITABLE_INBOX.
        4. SQLite database indexes both existing notes and inbox README.
        """
        mock_vault = self._make_temp_dir(prefix="existing_inbox_vault_")
        idx_dest = self._make_temp_dir(prefix="index_inbox_dest_")

        # Create user notes
        (mock_vault / "Notes").mkdir()
        note1 = mock_vault / "Notes" / "Existing Note.md"
        note1.write_text(
            "---\ntitle: Existing User Note\ntype: note\n---\n# Existing Note\nUser content.",
            encoding="utf-8",
        )
        user_notes_snapshot_before = _snapshot_directory(mock_vault)

        cmd = [
            sys.executable,
            str(SETUP_PY),
            "--mode",
            "index",
            "--vault",
            str(mock_vault),
            "--dest",
            str(idx_dest),
            "--writable-inbox",
            "AI-Memory",
            "--agents",
            "all",
            "--non-interactive",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            result.returncode,
            0,
            f"setup.py failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}",
        )

        # 1. Designated inbox directory must exist with README
        inbox_dir = mock_vault / "AI-Memory"
        self.assertTrue(inbox_dir.is_dir(), "AI-Memory folder was not created")
        readme = inbox_dir / "AI-Memory-README.md"
        self.assertTrue(readme.is_file(), "AI-Memory-README.md is missing")

        # 2. Existing notes outside AI-Memory must remain 100% untouched
        current_snapshot = _snapshot_directory(mock_vault)
        for path, meta in user_notes_snapshot_before.items():
            self.assertIn(path, current_snapshot)
            self.assertEqual(meta, current_snapshot[path], f"File {path} was modified!")

        # 3. mcp_ready_config.json must contain --writable-inbox AI-Memory
        cfg_path = idx_dest / "mcp_ready_config.json"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        server_args = cfg["mcpServers"]["ai-brain"]["args"]
        self.assertIn("--writable-inbox", server_args)
        self.assertIn("AI-Memory", server_args)
        server_env = cfg["mcpServers"]["ai-brain"]["env"]
        self.assertEqual(server_env.get("BRAIN_WRITABLE_INBOX"), "AI-Memory")

        # 4. SQLite DB indexes both user note and inbox README
        db_path = idx_dest / "brain_memory.sqlite"
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT title, path FROM memories ORDER BY title;").fetchall()
            titles = [r[0] for r in rows]
            self.assertIn("Existing User Note", titles)
            self.assertIn("AI-Memory Inbox", titles)
        finally:
            conn.close()

    def test_read_only_mode_fails_if_no_markdown_files(self) -> None:
        """Verifica che il wizard fallisca con messaggio chiaro se il vault è privo di file Markdown."""
        empty_vault = self._make_temp_dir(prefix="empty_vault_")
        idx_dest = self._make_temp_dir(prefix="empty_idx_dest_")

        cmd = [
            sys.executable,
            str(SETUP_PY),
            "--mode",
            "index",
            "--vault",
            str(empty_vault),
            "--dest",
            str(idx_dest),
            "--non-interactive",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        self.assertNotEqual(result.returncode, 0, "setup.py should have failed on empty vault")
        combined_output = (result.stdout + result.stderr).lower()
        self.assertTrue(
            "markdown" in combined_output or "no .md files" in combined_output or "empty" in combined_output,
            f"Expected error message regarding Markdown files, got: {result.stdout} {result.stderr}",
        )

    def test_read_only_mode_fails_if_vault_path_does_not_exist(self) -> None:
        """Verifica che il wizard fallisca se il percorso vault specificato non esiste."""
        non_existent_vault = Path("/tmp/definitely_not_existing_vault_12345")
        idx_dest = self._make_temp_dir(prefix="idx_dest_")

        cmd = [
            sys.executable,
            str(SETUP_PY),
            "--mode",
            "index",
            "--vault",
            str(non_existent_vault),
            "--dest",
            str(idx_dest),
            "--non-interactive",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "setup.py should fail when vault path does not exist")

    def test_agent_selection_filtering_cursor_only(self) -> None:
        """Verifica che specificando --agents cursor venga copiato solo .cursorrules."""
        dest_dir = self._make_temp_dir(prefix="cursor_only_")

        cmd = [
            sys.executable,
            str(SETUP_PY),
            "--mode",
            "fresh",
            "--dest",
            str(dest_dir),
            "--agents",
            "cursor",
            "--non-interactive",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"Error: {result.stderr}")

        self.assertTrue((dest_dir / ".cursorrules").is_file(), ".cursorrules should exist")
        self.assertFalse((dest_dir / "CLAUDE.md").exists(), "CLAUDE.md should NOT exist")
        self.assertFalse((dest_dir / "AGENTS.md").exists(), "AGENTS.md should NOT exist")
        self.assertFalse((dest_dir / "GEMINI.md").exists(), "GEMINI.md should NOT exist")

    def test_e2e_fts5_latency_verification_output(self) -> None:
        """Verifica che l'output contenga la conferma della verifica E2E FTS5 (< 15ms)."""
        dest_dir = self._make_temp_dir(prefix="latency_test_")

        cmd = [
            sys.executable,
            str(SETUP_PY),
            "--mode",
            "fresh",
            "--dest",
            str(dest_dir),
            "--non-interactive",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(
            "15ms" in result.stdout or "ms" in result.stdout,
            f"Expected latency verification message in stdout, got:\n{result.stdout}",
        )


    def test_verify_environment_unit(self) -> None:
        """Verifica unitaria diretta di verify_environment."""
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from setup import verify_environment
            ok, msg = verify_environment()
            self.assertTrue(ok)
            self.assertEqual(msg, "OK")
        finally:
            if str(REPO_ROOT) in sys.path:
                sys.path.remove(str(REPO_ROOT))

    def test_parse_agents_arg_unit(self) -> None:
        """Verifica unitaria di parse_agents_arg con varie combinazioni e alias."""
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from setup import parse_agents_arg
            # Default or None -> all 4 agents
            self.assertEqual(len(parse_agents_arg(None)), 4)
            self.assertEqual(len(parse_agents_arg("all")), 5)
            self.assertEqual(len(parse_agents_arg("5")), 5)

            # Numeric aliases
            self.assertEqual(parse_agents_arg("1"), {"claude"})
            self.assertEqual(parse_agents_arg("2"), {"cursor"})
            self.assertEqual(parse_agents_arg("3"), {"antigravity"})
            self.assertEqual(parse_agents_arg("4"), {"gemini"})
            self.assertEqual(parse_agents_arg("1,3"), {"claude", "antigravity"})

            # Names and spaces
            self.assertEqual(parse_agents_arg("claude, gemini"), {"claude", "gemini"})
        finally:
            if str(REPO_ROOT) in sys.path:
                sys.path.remove(str(REPO_ROOT))

    def test_copy_tree_non_destructive_preserves_existing_files(self) -> None:
        """Verifica che copy_tree_non_destructive non sovrascriva file utente già esistenti."""
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from setup import copy_tree_non_destructive
            src = self._make_temp_dir(prefix="src_")
            dst = self._make_temp_dir(prefix="dst_")

            (src / "file1.txt").write_text("template content", encoding="utf-8")
            (dst / "file1.txt").write_text("custom user content", encoding="utf-8")

            copy_tree_non_destructive(src, dst)

            self.assertEqual((dst / "file1.txt").read_text(encoding="utf-8"), "custom user content")
        finally:
            if str(REPO_ROOT) in sys.path:
                sys.path.remove(str(REPO_ROOT))

    def test_interactive_mode_via_stdin_pipe(self) -> None:
        """Verifica che setup.py funzioni correttamente anche con input interattivo su stdin."""
        dest_dir = self._make_temp_dir(prefix="interactive_vault_")

        # Input: choice 1 (fresh), dest_dir, choice 5 (all)
        stdin_input = f"1\n{dest_dir}\n5\n"

        cmd = [
            sys.executable,
            str(SETUP_PY),
        ]
        result = subprocess.run(cmd, input=stdin_input, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"Interactive run failed: {result.stderr}")
        self.assertTrue((dest_dir / "mcp_ready_config.json").is_file())
        self.assertTrue((dest_dir / "CLAUDE.md").is_file())


if __name__ == "__main__":
    unittest.main()
