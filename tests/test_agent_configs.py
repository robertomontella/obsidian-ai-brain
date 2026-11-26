"""Unit tests for AI-BRAIN multi-agent configuration files and MCP snippets.

Validates that rules and snippets for Claude, Cursor, Antigravity, and Gemini:
1. Exist in their respective directories.
2. Contain valid JSON syntax and required placeholder tokens.
3. Contain all mandatory protocols (Silent Auto-Retrieval, Autonomous Auto-Capture, Zero-Leak).
4. Explain PARA vault hierarchy and the 4 cognitive memory layers.
5. Contain zero author personal paths, usernames, or secrets.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"

REQUIRED_AGENT_DIRS = [
    AGENTS_DIR / "claude",
    AGENTS_DIR / "cursor",
    AGENTS_DIR / "antigravity",
    AGENTS_DIR / "gemini",
]

REQUIRED_SNIPPET_FILES = [
    AGENTS_DIR / "claude" / "claude_desktop_snippet.json",
    AGENTS_DIR / "cursor" / "cursor_mcp_snippet.json",
    AGENTS_DIR / "antigravity" / "mcp_config_snippet.json",
]

REQUIRED_RULE_FILES = [
    AGENTS_DIR / "claude" / "CLAUDE.md",
    AGENTS_DIR / "cursor" / ".cursorrules",
    AGENTS_DIR / "antigravity" / "AGENTS.md",
    AGENTS_DIR / "gemini" / "GEMINI.md",
]

REQUIRED_PLACEHOLDERS = [
    "{{PYTHON_PATH}}",
    "{{SERVER_PATH}}",
    "{{VAULT_PATH}}",
]

PARA_FOLDERS = [
    "00_Inbox",
    "10_Projects",
    "20_Areas",
    "30_Resources",
    "40_Archive",
]

COGNITIVE_LAYERS = [
    "50_Memory",
    "Episodic",
    "Semantic",
    "Procedural",
    "Working",
]

FORBIDDEN_PATTERNS = [
    re.compile(r"/Users/\w+", re.IGNORECASE),
    re.compile(r"robertomontella", re.IGNORECASE),
    re.compile(r"montella", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"password\s*[:=]\s*['\"][^'\"]+['\"]", re.IGNORECASE),
]


class TestAgentConfigs(unittest.TestCase):
    """Test suite for agent rule specifications and MCP snippets."""

    def test_agent_directories_exist(self) -> None:
        """Verifica che tutte le directory per i 4 agenti esistano."""
        self.assertTrue(
            AGENTS_DIR.exists() and AGENTS_DIR.is_dir(),
            f"Directory agents/ missing at {AGENTS_DIR}",
        )
        for d in REQUIRED_AGENT_DIRS:
            self.assertTrue(
                d.exists() and d.is_dir(),
                f"Required agent directory missing: {d.relative_to(REPO_ROOT)}",
            )

    def test_required_rule_files_exist(self) -> None:
        """Verifica l'esistenza dei file di regole per ciascun assistente."""
        for rf in REQUIRED_RULE_FILES:
            self.assertTrue(
                rf.exists() and rf.is_file(),
                f"Required rule file missing: {rf.relative_to(REPO_ROOT)}",
            )
            self.assertGreater(
                rf.stat().st_size,
                100,
                f"Rule file {rf.name} is too short or empty",
            )

    def test_required_snippets_exist_and_valid_json(self) -> None:
        """Verifica che gli snippet JSON esistano, siano validi e contengano i placeholder."""
        for sf in REQUIRED_SNIPPET_FILES:
            self.assertTrue(
                sf.exists() and sf.is_file(),
                f"Required snippet file missing: {sf.relative_to(REPO_ROOT)}",
            )
            raw_content = sf.read_text(encoding="utf-8")
            
            # Verifica parsing JSON
            try:
                data = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                self.fail(f"Invalid JSON in {sf.name}: {exc}")

            # Verifica che sia un dict con 'mcpServers'
            self.assertIsInstance(data, dict, f"Snippet {sf.name} must be a JSON object")
            self.assertIn(
                "mcpServers",
                data,
                f"Snippet {sf.name} must contain top-level 'mcpServers' key",
            )

            # Verifica la presenza dei placeholder obbligatori nel testo raw
            for placeholder in REQUIRED_PLACEHOLDERS:
                self.assertIn(
                    placeholder,
                    raw_content,
                    f"Placeholder '{placeholder}' missing in {sf.name}",
                )

    def test_snippets_renderable_with_real_paths(self) -> None:
        """Verifica che la sostituzione dei placeholder produca una configurazione valida."""
        dummy_python = "/usr/bin/python3"
        dummy_server = "/opt/ai-brain/mcp-server/server.py"
        dummy_vault = "/home/user/ObsidianVault"

        for sf in REQUIRED_SNIPPET_FILES:
            content = sf.read_text(encoding="utf-8")
            rendered = (
                content.replace("{{PYTHON_PATH}}", dummy_python)
                .replace("{{SERVER_PATH}}", dummy_server)
                .replace("{{VAULT_PATH}}", dummy_vault)
            )
            config = json.loads(rendered)
            servers = config.get("mcpServers", {})
            self.assertTrue(len(servers) > 0, f"No server entry found in {sf.name}")
            for s_name, s_conf in servers.items():
                self.assertEqual(s_conf.get("command"), dummy_python)
                self.assertIn(dummy_server, s_conf.get("args", []))

    def test_markdown_rules_contain_mandatory_protocols(self) -> None:
        """Verifica che ogni file di regole implementi i protocolli fondamentali."""
        for rf in REQUIRED_RULE_FILES:
            content = rf.read_text(encoding="utf-8")

            # 1. Silent Auto-Retrieval
            self.assertTrue(
                "brain_search_memory" in content or "Auto-Retrieval" in content,
                f"{rf.name} must reference brain_search_memory or Auto-Retrieval",
            )
            self.assertTrue(
                any(term in content.lower() for term in ["silent", "silenzios", "zero narration", "background"]),
                f"{rf.name} must specify silent / non-verbose retrieval behavior",
            )

            # 2. Autonomous Auto-Capture
            self.assertTrue(
                "brain_add_memory" in content or "Auto-Capture" in content,
                f"{rf.name} must reference brain_add_memory or Auto-Capture",
            )
            self.assertTrue(
                any(term in content.lower() for term in ["autonomous", "autonom", "zero user friction", "senza chiedere"]),
                f"{rf.name} must mandate autonomous capture without asking for confirmation",
            )

            # 3. Zero-Leak Security Policy
            self.assertTrue(
                "zero-leak" in content.lower() or "zero leak" in content.lower() or "anti-leak" in content.lower(),
                f"{rf.name} must include a Zero-Leak security policy",
            )
            self.assertTrue(
                any(term in content for term in ["sk-", "ghp_", "API key", "api key", "Bearer", "token", "password"]),
                f"{rf.name} must explicitly forbid leaking tokens or API keys",
            )

            # 4. Vault Structure Guidance (PARA)
            for folder in PARA_FOLDERS:
                self.assertIn(
                    folder,
                    content,
                    f"{rf.name} must explain PARA folder '{folder}'",
                )

            # 5. Cognitive Memory Layers
            for layer in COGNITIVE_LAYERS:
                self.assertIn(
                    layer,
                    content,
                    f"{rf.name} must explain cognitive layer '{layer}'",
                )

    def test_no_personal_paths_or_secrets_in_agents(self) -> None:
        """Verifica che nessun file sotto agents/ contenga percorsi personali, username o segreti."""
        all_files = list(AGENTS_DIR.rglob("*"))
        self.assertTrue(len(all_files) > 0, "No files found under agents/")

        for file_path in all_files:
            if file_path.is_dir():
                continue
            content = file_path.read_text(encoding="utf-8")
            for pattern in FORBIDDEN_PATTERNS:
                matches = pattern.findall(content)
                self.assertEqual(
                    matches,
                    [],
                    f"Forbidden pattern '{pattern.pattern}' found in {file_path.relative_to(REPO_ROOT)}: {matches}",
                )


if __name__ == "__main__":
    unittest.main()
