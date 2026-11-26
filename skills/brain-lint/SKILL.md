---
name: brain-lint
description: Lint AI-BRAIN vault for integrity issues (broken links, orphans, incomplete frontmatter, superseded mismatches)
---

# `/brain-lint` — AI-BRAIN Vault Integrity Linter

## Purpose

This skill provides automated vault integrity checking for AI-BRAIN, detecting common issues that can degrade memory retrieval quality and cross-agent collaboration.

## Usage

```bash
python3 tools/lint_engine.py --vault <path> [--db <path>] [--json]
```

Or via MCP tool: `brain_lint(vault_path, db_path)`

## Capabilities

### 1. Broken Wikilink Detection
Finds all `[[wikilink]]` references pointing to non-existent notes in the vault (ignoring fenced code blocks and inline code spans).

### 2. Orphan Note Detection
Identifies notes with no incoming or outgoing links and no entries in `memory_relations`.

### 3. Incomplete Frontmatter Validation
Validates YAML frontmatter against `vault-template/90_System/MEMORY_SCHEMA.md`.

### 4. Superseded Note Mismatches
Detects inconsistencies between DB `status='superseded'` / `superseded_by` and Markdown `superseded_by` references.

## Exit Codes

- `0`: Vault is clean (no issues found)
- `1`: Issues detected or validation error

## References

- `tools/lint_engine.py` — Standalone linter implementation
- `mcp-server/server.py` — `brain_lint` MCP tool integration
- `vault-template/90_System/MEMORY_SCHEMA.md` — Frontmatter schema
