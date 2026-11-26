"""Unit tests for community contributor infrastructure and GitHub issue templates.

Validates:
1. CONTRIBUTING.md exists, non-empty, contains zero-dependency rule,
   TDD command ('python3 -m unittest discover tests'), and step-by-step
   instructions covering Windsurf, Continue, and Aider.
2. GitHub issue templates exist under .github/ISSUE_TEMPLATE/:
   - bug_report.yml, feature_request.yml, new_agent_support.yml
   - Valid YAML syntax parsed via pure Python stdlib (zero third-party dependencies)
   - Mandatory form fields and control types
3. Zero personal paths (/Users/...) or author usernames across all contributor files.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
GITHUB_DIR = REPO_ROOT / ".github"
ISSUE_TEMPLATE_DIR = GITHUB_DIR / "ISSUE_TEMPLATE"
CONTRIBUTING_FILE = REPO_ROOT / "CONTRIBUTING.md"

REQUIRED_TEMPLATES = [
    ISSUE_TEMPLATE_DIR / "bug_report.yml",
    ISSUE_TEMPLATE_DIR / "feature_request.yml",
    ISSUE_TEMPLATE_DIR / "new_agent_support.yml",
]

FORBIDDEN_LEAK_PATTERNS = [
    re.compile(r"/Users/\w+", re.IGNORECASE),
    re.compile(r"robertomontella", re.IGNORECASE),
    re.compile(r"montella", re.IGNORECASE),
    re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"),
    re.compile(r"\bghp_[a-zA-Z0-9_\-]{20,}\b"),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE),
]


def parse_yaml_form(yaml_text: str) -> dict[str, Any]:
    """Pure Python standard library parser for GitHub Issue Form YAML files."""
    lines = yaml_text.splitlines()
    for idx, line in enumerate(lines, 1):
        if "\t" in line:
            raise ValueError(f"Tab character found on line {idx}; YAML requires spaces.")

    data: dict[str, Any] = {}
    current_section: str | None = None
    body_items: list[dict[str, Any]] = []
    current_item: dict[str, Any] | None = None
    current_subdict_key: str | None = None
    in_multiline = False
    multiline_target_dict: dict[str, Any] | None = None
    multiline_key: str | None = None
    multiline_indent = 0
    multiline_lines: list[str] = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if in_multiline:
            line_indent = len(raw_line) - len(raw_line.lstrip(" "))
            if stripped and line_indent <= multiline_indent:
                if multiline_target_dict is not None and multiline_key is not None:
                    multiline_target_dict[multiline_key] = "\n".join(multiline_lines).strip()
                in_multiline = False
                multiline_lines = []
                multiline_target_dict = None
                multiline_key = None
            else:
                multiline_lines.append(
                    raw_line[multiline_indent + 2 :]
                    if len(raw_line) > multiline_indent + 2
                    else stripped
                )
                continue

        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))

        if indent == 0:
            current_subdict_key = None
            current_item = None
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                k = k.strip()
                v = v.strip()
                current_section = k
                if v.startswith("[") and v.endswith("]"):
                    data[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                elif not v:
                    if k == "body":
                        data["body"] = body_items
                    else:
                        data[k] = {}
                else:
                    data[k] = v.strip("'\"")
            continue

        if current_section == "body":
            if stripped.startswith("- "):
                current_item = {}
                body_items.append(current_item)
                current_subdict_key = None
                rest = stripped[2:].strip()
                if ":" in rest:
                    k, v = rest.split(":", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    current_item[k] = v
                continue

            if current_item is not None and ":" in stripped:
                k, v = stripped.split(":", 1)
                k = k.strip()
                v = v.strip()
                if v in ("|", "|-", "|+"):
                    in_multiline = True
                    multiline_indent = indent
                    multiline_lines = []
                    target = (
                        current_item[current_subdict_key]
                        if current_subdict_key and current_subdict_key in current_item
                        else current_item
                    )
                    multiline_target_dict = target
                    multiline_key = k
                elif not v:
                    subdict: dict[str, Any] = {}
                    current_item[k] = subdict
                    current_subdict_key = k
                else:
                    val: Any = v.strip("'\"")
                    if str(val).lower() == "true":
                        val = True
                    elif str(val).lower() == "false":
                        val = False
                    target = (
                        current_item[current_subdict_key]
                        if current_subdict_key and current_subdict_key in current_item
                        else current_item
                    )
                    target[k] = val

    if in_multiline and multiline_target_dict is not None and multiline_key is not None:
        multiline_target_dict[multiline_key] = "\n".join(multiline_lines).strip()

    return data


class TestContributorInfrastructure(unittest.TestCase):
    """Test suite validating CONTRIBUTING.md guidelines and GitHub issue forms."""

    def test_contributing_file_exists_and_non_empty(self) -> None:
        """Verifica che CONTRIBUTING.md esista alla radice e abbia un contenuto consistente."""
        self.assertTrue(CONTRIBUTING_FILE.is_file(), "CONTRIBUTING.md must exist at repository root")
        content = CONTRIBUTING_FILE.read_text(encoding="utf-8").strip()
        self.assertGreater(len(content), 500, "CONTRIBUTING.md is too short or empty")

    def test_contributing_contains_guidelines_and_rules(self) -> None:
        """Verifica le regole architetturali chiave: standard library only e TDD."""
        content = CONTRIBUTING_FILE.read_text(encoding="utf-8")

        # Zero-dependency rule
        self.assertTrue(
            re.search(r"zero\s*(external\s*)?dependenc", content, re.IGNORECASE)
            or re.search(r"standard\s+library\s+only", content, re.IGNORECASE),
            "CONTRIBUTING.md must mandate zero external dependencies (Python standard library only)",
        )
        self.assertTrue(
            re.search(r"python\s*(3\.\d+\+?\s*)?standard\s+library", content, re.IGNORECASE),
            "CONTRIBUTING.md must explicitly reference Python standard library",
        )

        # TDD testing command
        self.assertIn(
            "python3 -m unittest discover tests",
            content,
            "CONTRIBUTING.md must document the TDD command 'python3 -m unittest discover tests'",
        )

    def test_contributing_covers_windsurf_continue_and_aider(self) -> None:
        """Verifica che siano presenti istruzioni dettagliate per Windsurf, Continue e Aider."""
        content = CONTRIBUTING_FILE.read_text(encoding="utf-8")
        for agent in ["Windsurf", "Continue", "Aider"]:
            self.assertIn(agent, content, f"CONTRIBUTING.md must provide instructions for {agent}")

        # Step-by-step guidance
        self.assertTrue(
            re.search(r"step\s+1|step\s*-\s*by\s*-\s*step", content, re.IGNORECASE),
            "CONTRIBUTING.md must provide step-by-step instructions for adding new AI agents",
        )

    def test_issue_template_files_exist(self) -> None:
        """Verifica l'esistenza della cartella ISSUE_TEMPLATE e dei 3 template obbligatori."""
        self.assertTrue(ISSUE_TEMPLATE_DIR.is_dir(), ".github/ISSUE_TEMPLATE directory missing")
        for template in REQUIRED_TEMPLATES:
            self.assertTrue(
                template.is_file(),
                f"Required issue template missing: {template.relative_to(REPO_ROOT)}",
            )
            self.assertGreater(template.stat().st_size, 50, f"{template.name} is too short or empty")

    def test_bug_report_template_syntax_and_fields(self) -> None:
        """Verifica campi obbligatori del bug report (OS, Python version, MCP client, stderr, behavior)."""
        path = ISSUE_TEMPLATE_DIR / "bug_report.yml"
        data = parse_yaml_form(path.read_text(encoding="utf-8"))
        self.assertIn("name", data)
        self.assertIn("description", data)
        self.assertIn("body", data)

        body = data["body"]
        body_text = str(body).lower()

        # OS
        self.assertTrue(
            any("os" in str(item).lower() or "operating system" in str(item).lower() for item in body),
            "bug_report.yml missing OS field",
        )
        # Python version
        self.assertTrue(
            any("python" in str(item).lower() for item in body),
            "bug_report.yml missing Python version field",
        )
        # MCP client
        self.assertTrue(
            any("mcp" in str(item).lower() or "client" in str(item).lower() for item in body),
            "bug_report.yml missing MCP client field",
        )
        # Stderr logs
        self.assertTrue(
            any("stderr" in str(item).lower() or "log" in str(item).lower() for item in body),
            "bug_report.yml missing stderr logs field",
        )
        # Expected vs actual behavior
        self.assertTrue("expected" in body_text, "bug_report.yml missing expected behavior field")
        self.assertTrue("actual" in body_text, "bug_report.yml missing actual behavior field")

    def test_feature_request_template_syntax_and_fields(self) -> None:
        """Verifica campi del feature request (use case, zero-dependency alignment)."""
        path = ISSUE_TEMPLATE_DIR / "feature_request.yml"
        data = parse_yaml_form(path.read_text(encoding="utf-8"))
        self.assertIn("name", data)
        self.assertIn("description", data)
        self.assertIn("body", data)

        body_text = str(data["body"]).lower()
        self.assertTrue(
            "use case" in body_text or "problem" in body_text or "proposal" in body_text,
            "feature_request.yml missing use case field",
        )
        self.assertTrue(
            "zero" in body_text or "dependenc" in body_text or "standard library" in body_text,
            "feature_request.yml missing zero-dependency core alignment field",
        )

    def test_new_agent_support_template_syntax_and_fields(self) -> None:
        """Verifica campi del template new agent support (agent/IDE name, config location, rule file)."""
        path = ISSUE_TEMPLATE_DIR / "new_agent_support.yml"
        data = parse_yaml_form(path.read_text(encoding="utf-8"))
        self.assertIn("name", data)
        self.assertIn("description", data)
        self.assertIn("body", data)

        body_text = str(data["body"]).lower()
        self.assertTrue(
            "agent" in body_text or "ide" in body_text or "editor" in body_text,
            "new_agent_support.yml missing agent/IDE name field",
        )
        self.assertTrue(
            "protocol" in body_text or "mcp" in body_text,
            "new_agent_support.yml missing MCP protocol support field",
        )
        self.assertTrue(
            "rule" in body_text or "file" in body_text,
            "new_agent_support.yml missing workspace rule file field",
        )

    def test_zero_personal_data_or_leak_in_contributor_files(self) -> None:
        """Verifica assenza assoluta di dati personali, path /Users/... o token nei file contributore."""
        files_to_check = [CONTRIBUTING_FILE] + REQUIRED_TEMPLATES
        for f in files_to_check:
            text = f.read_text(encoding="utf-8")
            for pat in FORBIDDEN_LEAK_PATTERNS:
                matches = pat.findall(text)
                self.assertEqual(
                    matches,
                    [],
                    f"Forbidden leak pattern '{pat.pattern}' in {f.name}: {matches}",
                )


if __name__ == "__main__":
    unittest.main()
