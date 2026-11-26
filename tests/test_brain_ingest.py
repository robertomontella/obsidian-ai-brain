"""Unit and integration tests for tools/brain_ingest.py.

Tests noise filtering, manifest parsing, git inspection, entry point detection,
Markdown generation, and CLI interface.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

# Import brain_ingest module under test
import brain_ingest


class TestBrainIngest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_noise_filtering_directories(self):
        """Verifies that noise directories like node_modules, Pods, .venv, dist, etc. are pruned."""
        # Create normal files
        src_dir = self.root_path / "src"
        src_dir.mkdir(parents=True)
        (src_dir / "index.ts").write_text("console.log('hello');\n", encoding="utf-8")
        (src_dir / "utils.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

        # Create ignored noise directories
        noise_dirs = [
            "node_modules/fake-package",
            "Pods/Pods.xcodeproj",
            ".venv/lib/python3.10",
            "venv/bin",
            ".git/objects",
            "__pycache__",
            "dist/bundle",
            "build/outputs",
            "DerivedData/Build",
            ".idea",
            ".vscode",
            ".gradle",
            "bin",
            "obj",
            ".turbo",
            "coverage",
        ]
        for nd in noise_dirs:
            p = self.root_path / nd
            p.mkdir(parents=True, exist_ok=True)
            (p / "garbage.js").write_text("throw new Error('noise');\n", encoding="utf-8")

        scan = brain_ingest.scan_project(self.root_path)

        # Structure tree should NOT contain any ignored directory or noise files
        tree = scan["directory_tree"]
        self.assertIn("src", tree)
        self.assertIn("index.ts", tree)
        self.assertIn("utils.py", tree)

        for nd_name in ["node_modules", "Pods", ".venv", "__pycache__", "dist", "build", "DerivedData", "garbage.js"]:
            self.assertNotIn(nd_name, tree)

        # Languages should only count ts and py
        languages = scan["languages"]
        self.assertIn("ts", languages)
        self.assertIn("py", languages)
        self.assertEqual(languages["ts"], 1)
        self.assertEqual(languages["py"], 1)
        self.assertNotIn("js", languages)  # from garbage.js

    def test_noise_filtering_files_and_locks(self):
        """Verifies lock files, binaries, media, and heavy files are pruned."""
        (self.root_path / "app.py").write_text("print('core app')\n", encoding="utf-8")
        (self.root_path / "package-lock.json").write_text("{}", encoding="utf-8")
        (self.root_path / "yarn.lock").write_text("# yarn lockfile", encoding="utf-8")
        (self.root_path / "Cargo.lock").write_text("# cargo lockfile", encoding="utf-8")
        (self.root_path / "poetry.lock").write_text("# poetry lockfile", encoding="utf-8")
        (self.root_path / "app.pyc").write_bytes(b"\x00\x00\x00\x00")
        (self.root_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (self.root_path / "demo.mp4").write_bytes(b"\x00\x00\x00\x20ftyp")
        (self.root_path / "archive.zip").write_bytes(b"PK\x03\x04")
        (self.root_path / "compiled.so").write_bytes(b"\x7fELF")
        (self.root_path / ".DS_Store").write_bytes(b"\x00\x00\x00\x01")

        scan = brain_ingest.scan_project(self.root_path)
        tree = scan["directory_tree"]

        self.assertIn("app.py", tree)
        self.assertNotIn("package-lock.json", tree)
        self.assertNotIn("yarn.lock", tree)
        self.assertNotIn("Cargo.lock", tree)
        self.assertNotIn("poetry.lock", tree)
        self.assertNotIn("app.pyc", tree)
        self.assertNotIn("logo.png", tree)
        self.assertNotIn("demo.mp4", tree)
        self.assertNotIn("archive.zip", tree)
        self.assertNotIn("compiled.so", tree)
        self.assertNotIn(".DS_Store", tree)

    def test_manifest_parsing_package_json(self):
        """Tests parsing of package.json for dependencies, scripts, and entry point."""
        pkg_json = {
            "name": "super-service",
            "version": "1.2.0",
            "main": "src/server.js",
            "scripts": {
                "start": "node src/server.js",
                "test": "jest",
                "build": "tsc"
            },
            "dependencies": {
                "express": "^4.19.2",
                "cors": "^2.8.5"
            },
            "devDependencies": {
                "typescript": "^5.4.0",
                "jest": "^29.7.0"
            }
        }
        (self.root_path / "package.json").write_text(json.dumps(pkg_json, indent=2), encoding="utf-8")
        src = self.root_path / "src"
        src.mkdir()
        (src / "server.js").write_text("const express = require('express');\nconst app = express();\n", encoding="utf-8")

        scan = brain_ingest.scan_project(self.root_path)

        manifests = scan["manifests"]
        self.assertEqual(len(manifests), 1)
        m = manifests[0]
        self.assertEqual(m["type"], "package.json")
        self.assertEqual(m["project_name"], "super-service")
        self.assertIn("express", m["dependencies"])
        self.assertIn("cors", m["dependencies"])
        self.assertIn("typescript", m["dev_dependencies"])
        self.assertEqual(m["entry_point"], "src/server.js")
        self.assertIn("start", m["scripts"])

    def test_manifest_parsing_python_and_cargo(self):
        """Tests parsing of Cargo.toml, pyproject.toml, and requirements.txt."""
        cargo_content = """[package]
name = "rusty-core"
version = "0.3.1"
edition = "2021"

[dependencies]
tokio = { version = "1.0", features = ["full"] }
serde = "1.0"
serde_json = "1.0"

[dev-dependencies]
criterion = "0.5"
"""
        (self.root_path / "Cargo.toml").write_text(cargo_content, encoding="utf-8")

        pyproject_content = """[project]
name = "py-engine"
version = "0.1.0"
dependencies = [
    "pydantic>=2.0.0",
    "fastapi>=0.100.0",
    "uvicorn>=0.22.0",
]
"""
        (self.root_path / "pyproject.toml").write_text(pyproject_content, encoding="utf-8")

        reqs_content = """# Requirements
requests>=2.28.0
flask==3.0.0
# Comment
pytest>=7.0.0
"""
        (self.root_path / "requirements.txt").write_text(reqs_content, encoding="utf-8")

        scan = brain_ingest.scan_project(self.root_path)

        manifest_types = {m["type"]: m for m in scan["manifests"]}
        self.assertIn("Cargo.toml", manifest_types)
        self.assertIn("pyproject.toml", manifest_types)
        self.assertIn("requirements.txt", manifest_types)

        cargo_m = manifest_types["Cargo.toml"]
        self.assertEqual(cargo_m["project_name"], "rusty-core")
        self.assertIn("tokio", cargo_m["dependencies"])
        self.assertIn("serde", cargo_m["dependencies"])

        py_m = manifest_types["pyproject.toml"]
        self.assertEqual(py_m["project_name"], "py-engine")
        self.assertTrue(any("pydantic" in d for d in py_m["dependencies"]))

        req_m = manifest_types["requirements.txt"]
        self.assertTrue(any("requests" in d for d in req_m["dependencies"]))
        self.assertTrue(any("flask" in d for d in req_m["dependencies"]))

    def test_manifest_parsing_go_mod(self):
        """Tests parsing of go.mod."""
        go_mod_content = """module github.com/user/apigateway

go 1.22

require (
    github.com/gin-gonic/gin v1.9.1
    github.com/google/uuid v1.6.0
)
"""
        (self.root_path / "go.mod").write_text(go_mod_content, encoding="utf-8")

        scan = brain_ingest.scan_project(self.root_path)
        manifest_types = {m["type"]: m for m in scan["manifests"]}
        self.assertIn("go.mod", manifest_types)
        go_m = manifest_types["go.mod"]
        self.assertEqual(go_m["project_name"], "github.com/user/apigateway")
        self.assertTrue(any("github.com/gin-gonic/gin" in d for d in go_m["dependencies"]))

    def test_git_inspection_with_commits(self):
        """Tests git inspection on a git repo with commits."""
        try:
            subprocess.run(["git", "init"], cwd=str(self.root_path), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(self.root_path), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(self.root_path), check=True, capture_output=True)

            (self.root_path / "init.txt").write_text("first commit\n", encoding="utf-8")
            subprocess.run(["git", "add", "init.txt"], cwd=str(self.root_path), check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat: initial commit"], cwd=str(self.root_path), check=True, capture_output=True)

            (self.root_path / "init.txt").write_text("second commit\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "fix: minor update"], cwd=str(self.root_path), check=True, capture_output=True)
        except Exception as e:
            self.skipTest(f"Git CLI not working: {e}")

        scan = brain_ingest.scan_project(self.root_path)
        git_info = scan["git_info"]
        self.assertTrue(git_info["is_repo"])
        self.assertEqual(git_info["total_commits"], 2)
        self.assertEqual(len(git_info["recent_commits"]), 2)
        self.assertTrue(any("feat: initial commit" in c for c in git_info["recent_commits"]))
        self.assertTrue(any("fix: minor update" in c for c in git_info["recent_commits"]))

    def test_git_inspection_without_repo(self):
        """Tests that scanning a non-git directory handles git gracefully."""
        scan = brain_ingest.scan_project(self.root_path)
        git_info = scan["git_info"]
        self.assertFalse(git_info["is_repo"])
        self.assertEqual(git_info["total_commits"], 0)
        self.assertEqual(git_info["recent_commits"], [])

    def test_entry_points_detection(self):
        """Tests detection of common entry point files and snippet extraction."""
        (self.root_path / "main.py").write_text(
            "import sys\n\ndef main():\n    print('Starting server...')\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8"
        )
        (self.root_path / "README.md").write_text("# Project Docs\nThis is a sample project.\n", encoding="utf-8")

        scan = brain_ingest.scan_project(self.root_path)
        entry_points = scan["entry_points"]
        self.assertTrue(any(ep["file"] == "main.py" for ep in entry_points))
        ep = next(ep for ep in entry_points if ep["file"] == "main.py")
        self.assertIn("Starting server...", ep["snippet"])

        # Check doc extraction
        self.assertIn("readme", scan["docs"])
        self.assertIn("This is a sample project.", scan["docs"]["readme"])

    def test_markdown_formatting(self):
        """Tests generating structured markdown for Obsidian 10_Projects/."""
        (self.root_path / "package.json").write_text(json.dumps({
            "name": "order-service",
            "version": "1.0.0",
            "dependencies": {"fastify": "^4.0.0"}
        }), encoding="utf-8")
        (self.root_path / "README.md").write_text("# Order Service\nMicroservice for handling orders.", encoding="utf-8")
        (self.root_path / "index.js").write_text("console.log('Order Service active');", encoding="utf-8")

        scan = brain_ingest.scan_project(self.root_path)
        md = brain_ingest.format_project_markdown(scan)

        self.assertIn("# Progetto: order-service", md)
        self.assertIn("Tech Stack", md)
        self.assertIn("Manifests & Dipendenze", md)
        self.assertIn("Albero Directory", md)
        self.assertIn("order-service", md)
        self.assertIn("fastify", md)

    def test_cli_execution_with_output_file(self):
        """Tests CLI --output argument saves markdown to destination."""
        (self.root_path / "app.py").write_text("print('cli test')\n", encoding="utf-8")
        output_file = self.root_path / "output_summary.md"

        ret = brain_ingest.main([str(self.root_path), "--output", str(output_file)])
        self.assertEqual(ret, 0)
        self.assertTrue(output_file.exists())
        content = output_file.read_text(encoding="utf-8")
        self.assertIn("# Progetto:", content)

    def test_cli_execution_with_vault_option(self):
        """Tests CLI --vault argument puts markdown into <vault>/10_Projects/<project>.md."""
        vault_dir = self.root_path / "MyVault"
        vault_dir.mkdir()
        target_dir = self.root_path / "project_x"
        target_dir.mkdir()
        (target_dir / "index.js").write_text("console.log('x');\n", encoding="utf-8")

        ret = brain_ingest.main([str(target_dir), "--vault", str(vault_dir)])
        self.assertEqual(ret, 0)

        expected_file = vault_dir / "10_Projects" / "project_x.md"
        self.assertTrue(expected_file.exists(), f"Expected file not found: {expected_file}")
        content = expected_file.read_text(encoding="utf-8")
        self.assertIn("# Progetto: project_x", content)

    def test_cli_json_output(self):
        """Tests CLI --json outputs valid JSON to stdout."""
        (self.root_path / "test.py").write_text("x = 1\n", encoding="utf-8")

        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        try:
            ret = brain_ingest.main([str(self.root_path), "--json"])
            self.assertEqual(ret, 0)
            output = buffer.getvalue()
            parsed = json.loads(output)
            self.assertEqual(parsed["target_path"], str(self.root_path.resolve()))
            self.assertIn("languages", parsed)
        finally:
            sys.stdout = old_stdout

    def test_cli_invalid_path(self):
        """Tests CLI handles invalid path by returning non-zero code."""
        ret = brain_ingest.main(["/path/that/definitely/does/not/exist_12345"])
        self.assertNotEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
