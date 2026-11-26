# Contributing to AI-BRAIN

Thank you for your interest in contributing to AI-BRAIN.

AI-BRAIN is an open-source, local-first persistent memory vault and Model Context Protocol (MCP) server for AI coding assistants. Our mission is to provide seamless, cross-agent cognitive memory while maintaining total privacy, sub-millisecond retrieval speeds, and zero cloud dependencies.

---

## 1. Core Architectural Laws

Every contribution to AI-BRAIN must adhere to the following non-negotiable principles:

### A. Zero External Dependencies Law (Python Standard Library Only)
- The core runtime (`mcp-server/`), administration tools (`tools/`), onboarding wizard (`setup.py`), and test suite (`tests/`) must rely strictly on the **Python 3.9+ Standard Library**.
- Under no circumstances may third-party pip dependencies (e.g., `requests`, `numpy`, `fastapi`, `pydantic`, `pyyaml`) be introduced into the core codebase.
- **Why?** This guarantees instant installation in under 60 seconds, eliminates virtual environment rot, ensures cross-platform determinism, and prevents software supply-chain vulnerabilities.

### B. Sub-15ms Latency Guarantee
- All SQLite FTS5 search queries must execute in under 15ms, even under concurrent multi-agent read/write loads.
- SQLite must always operate in Write-Ahead Logging (`WAL`) mode with `PRAGMA busy_timeout = 5000`.

### C. Zero-Leak Security & Privacy Policy
- AI-BRAIN runs 100% locally. No code, telemetry, or note content may ever be transmitted over external networks.
- All code and templates submitted to the repository must be completely free of personal machine paths, author usernames, private keys, secret API tokens, or hardcoded credentials.

---

## 2. Development Setup & TDD Workflow

AI-BRAIN practices strict Test-Driven Development (TDD).

### Prerequisites
- Python 3.9 or higher with SQLite FTS5 support (included in official Python distributions).
- Git.

### Setup Steps
```bash
# 1. Clone the repository
git clone https://github.com/robertomontella/obsidian-ai-brain.git
cd obsidian-ai-brain

# 2. Run the full test suite
python3 -m unittest discover tests
```

### The TDD Cycle
1. **Write the Test First (Red):** Before implementing any feature or fixing any bug, add a corresponding test case under `tests/test_<feature>.py`.
2. **Implement the Minimal Code (Green):** Write the minimal standard library code required to satisfy the test.
3. **Refactor & Verify:** Ensure 100% test pass rate across the repository:
   ```bash
   python3 -m unittest discover tests
   ```
4. **Isolate Test State:** Always use `tempfile.TemporaryDirectory()` or in-memory SQLite (`:memory:`) in unit tests. Never create persistent artifacts in the repository root.

---

## 3. Step-by-Step Guide: Adding a New AI Agent or IDE

We welcome contributions adding support for new coding assistants, editors, and CLI agents. Follow this 4-step process:

### Step 1: Create the Agent Directory
Create a dedicated folder under `agents/<agent_name>/`:
```bash
mkdir -p agents/<agent_name>
```

### Step 2: Create the Agent Rule File
Create the instructions or system prompt file that the agent reads in user workspaces (e.g., `CLAUDE.md`, `.cursorrules`, `.windsurfrules`, `CONVENTIONS.md`).

The rule file **must** explain the following 5 protocols:
1. **Silent Auto-Retrieval Protocol:** The agent must silently query `brain_search_memory` at session startup without verbose chat commentary ("Searching memory...").
2. **Autonomous Auto-Capture Protocol:** The agent must autonomously save decisions and bug solutions via `brain_add_memory` without asking the user for confirmation.
3. **Zero-Leak Security Policy:** The agent must sanitize secrets (API keys, secret tokens, Bearer tokens, passwords) before persisting memory.
4. **PARA Framework Guidance:** Explain the `00_Inbox`, `10_Projects`, `20_Areas`, `30_Resources`, and `40_Archive` folders.
5. **4 Cognitive Memory Layers:** Explain `50_Memory/` subfolders (`Episodic`, `Semantic`, `Procedural`, `Working`).

### Step 3: Create the MCP Configuration Snippet
Add an MCP snippet file (e.g., `<agent_name>_snippet.json`) containing the standard command and arguments, using exact template tokens:
- `{{PYTHON_PATH}}`: Path to the python3 executable.
- `{{SERVER_PATH}}`: Path to `server.py`.
- `{{VAULT_PATH}}`: Path to the user's active Obsidian vault root.

Example template:
```json
{
  "mcpServers": {
    "ai-brain": {
      "command": "{{PYTHON_PATH}}",
      "args": [
        "{{SERVER_PATH}}",
        "--vault",
        "{{VAULT_PATH}}"
      ],
      "env": {
        "BRAIN_VAULT_PATH": "{{VAULT_PATH}}"
      }
    }
  }
}
```

### Step 4: Register the Agent in `setup.py` & Test Suite
1. Update `AGENT_RULE_MAPPING` in `setup.py`:
   ```python
   AGENT_RULE_MAPPING["<agent_name>"] = ("<agent_name>", "<rule_source>", "<rule_destination>")
   ```
2. Add the agent to `tests/test_agent_configs.py`:
   - Append to `REQUIRED_AGENT_DIRS`.
   - Append to `REQUIRED_RULE_FILES`.
   - Append to `REQUIRED_SNIPPET_FILES`.
3. Run `python3 -m unittest discover tests` to confirm all tests pass.

---

### Reference Implementations: Windsurf, Continue, and Aider

#### Windsurf (Codeium)
- **Directory:** `agents/windsurf/`
- **Rule File:** `agents/windsurf/.windsurfrules`
- **Snippet File:** `agents/windsurf/windsurf_mcp_snippet.json`
- **Target Configuration Path:**
  - macOS/Linux: `~/.codeium/windsurf/mcp_config.json`
  - Windows: `%USERPROFILE%\.codeium\windsurf\mcp_config.json`
- **Setup:** Add the JSON snippet to the `mcpServers` object in `mcp_config.json`, then place `.windsurfrules` in the root of your project workspace.

#### Continue (continue.dev)
- **Directory:** `agents/continue/`
- **Rule File:** `agents/continue/continue_prompt.md`
- **Snippet File:** `agents/continue/continue_mcp_snippet.json`
- **Target Configuration Path:**
  - JSON Config: `~/.continue/config.json`
  - YAML Config: `~/.continue/config.yaml`
- **Setup:**
  - For `config.json`, insert into `experimental.modelContextProtocolServers` or `mcpServers`.
  - For `config.yaml`, add under root key `mcpServers:`.
  - Place `continue_prompt.md` in `.continue/prompts/` or include its contents in `customInstructions`.

#### Aider (aider.chat)
- **Directory:** `agents/aider/`
- **Conventions File:** `agents/aider/CONVENTIONS.md`
- **Configuration File:** `agents/aider/.aider.conf.yml`
- **Target Configuration Path:** Project root `.aider.conf.yml` or `~/.aider.conf.yml`.
- **Setup:**
  Configure `.aider.conf.yml` to automatically read the memory conventions into session context:
  ```yaml
  read:
    - CONVENTIONS.md
  ```
  Aider can also consult active notes in `50_Memory/` via `read: [ "50_Memory/Semantic/..." ]` or connect to AI-BRAIN through an MCP bridge server.

---

## 4. Code Standards & Integrity Guidelines

- **Python Standard:** Code must target Python 3.9+ using modern typing (`from __future__ import annotations`).
- **Formatting:** Follow PEP 8 guidelines. Indent with 4 spaces for Python, 2 spaces for JSON and YAML.
- **No Tabs:** Tabs are strictly prohibited in code, markdown, and YAML files.
- **Docstrings:** All public functions, classes, and CLI commands must have concise docstrings describing purpose, arguments, and return types.
- **Clean Repository:** Do not commit `.DS_Store`, `*.pyc`, `__pycache__`, or `*.sqlite*` files.

---

## 5. Pull Request Checklist

Before submitting a Pull Request, verify each item:

- [ ] All unit tests pass: `python3 -m unittest discover tests` returns code 0.
- [ ] No external pip dependencies were added to `mcp-server/`, `tools/`, or `setup.py`.
- [ ] No personal machine paths or usernames are included in code, tests, or documentation.
- [ ] New agent additions include a valid rule file and an MCP snippet using standard placeholders.
- [ ] Documentation contains no emojis, icons, or external graphical badges.
