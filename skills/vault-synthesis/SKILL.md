---
name: vault-synthesis
description: Consolidates Episodic memories into Semantic/Procedural notes via CLI tool
---

## Vault Synthesis Skill

This skill provides the `tools/vault_synthesis.py` CLI tool for consolidating recent Episodic memories into Semantic and Procedural notes within an AI-BRAIN vault.

### Purpose

The vault synthesis tool performs memory consolidation by:
- Reading recent Episodic notes from the SQLite database (last N days)
- Grouping them by project tag
- Generating consolidated Semantic or Procedural notes with distilled key points
- Maintaining zero external dependencies (Python stdlib only)

### Usage

```bash
python3 tools/vault_synthesis.py --vault <path> --target-type <type>

# Options:
# --vault PATH        Path to Obsidian vault root directory (required)
# --db PATH           Path to SQLite database (defaults to <vault>/90_System/db/brain_memory.sqlite)
# --target-type TYPE  Either "memory/semantic" or "memory/procedural"
# --days N            Look back N days (default: 30)
# --project NAME      Filter by project name
# --dry-run           Show what would be created without writing files
```

### Reference

- Implementation: `tools/vault_synthesis.py`
- Tests: `tests/e2e/test_e2e_tier1_features.py` (TestTier1R4VaultSynthesis)
- Related: `vault-template/50_Memory/` structure