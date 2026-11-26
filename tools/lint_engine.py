#!/usr/bin/env python3
"""tools/lint_engine.py — Vault Integrity Linting Engine.

Pure Python 3.9+ Standard Library for read-only verification of Markdown vault integrity.
Detects broken wikilinks, orphan notes, incomplete frontmatter, and superseded mismatches.

Zero external dependencies. Designed to run as a standalone tool separate from
the MCP server core or as a verification utility for CI/CD pipelines.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# YAML frontmatter patterns
FRONTMATTER_PATTERN = re.compile(r'^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n(.*))?$', re.DOTALL)
WIKILINK_PATTERN = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')
WAKILINK_PATTERN = WIKILINK_PATTERN  # Backward-compatible alias
YAML_KEY_PATTERN = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)$', re.MULTILINE)
_FENCED_CODE_RE = re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~')
_INLINE_CODE_RE = re.compile(r'`[^`\n]+`')
_NON_MEMORY_FILENAMES = {'README.md', 'MEMORY_SCHEMA.md', 'crew_log.md'}

# Required frontmatter fields from MEMORY_SCHEMA.md
REQUIRED_FRONTMATTER_FIELDS = [
    'title', 'type', 'status', 'created_at', 'updated_at',
    'project', 'tags', 'priority', 'importance_score', 'source_agent'
]

# Optional but commonly expected fields
COMMONLY_EXPECTED_FIELDS = [
    'last_accessed', 'archived_at', 'supersedes', 'superseded_by',
    'memory_layer', 'summary', 'notes_count'
]


def parse_yaml_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parses YAML frontmatter from markdown content."""
    if not content.startswith('---'):
        return {}, content

    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content

    yaml_text = match.group(1)
    body_text = match.group(2) or ''
    metadata: dict[str, Any] = {}

    for raw_line in yaml_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        # Handle list items
        if line.startswith('- '):
            continue

        # Handle key: value pairs
        key_match = YAML_KEY_PATTERN.match(line)
        if key_match:
            key = key_match.group(1)
            val = key_match.group(2).strip()

            # Parse different value types
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            elif val.lower() == 'true':
                val = True
            elif val.lower() == 'false':
                val = False
            elif val.startswith('['):
                # Simple list parsing
                inner = val[1:-1].strip()
                if inner:
                    items = []
                    for item in inner.split(','):
                        item = item.strip().strip('"').strip("'")
                        if item and item.lower() not in ('true', 'false', 'null'):
                            try:
                                if '.' in item:
                                    items.append(float(item))
                                else:
                                    items.append(int(item))
                            except ValueError:
                                items.append(item)
                    val = items
                else:
                    val = []
            elif val.lower() in ('null', '~'):
                val = None

            metadata[key] = val

    return metadata, body_text


def extract_wikilinks(content: str) -> list[str]:
    """Extracts all wikilink targets from markdown content, ignoring code blocks/spans."""
    masked = _FENCED_CODE_RE.sub('', content)
    masked = _INLINE_CODE_RE.sub('', masked)
    return WIKILINK_PATTERN.findall(masked)


def get_all_md_files(vault_path: Path, exclude_dirs: set[str] | None = None) -> list[Path]:
    """Recursively finds all .md files in vault, excluding specified directories."""
    if exclude_dirs is None:
        exclude_dirs = {'.git', '.obsidian', '__pycache__', '.pytest_cache', '90_System/db'}

    md_files = []
    for path in vault_path.rglob('*.md'):
        # Check if any excluded dir is in the path
        rel_parts = set(path.relative_to(vault_path).parts)
        if not rel_parts.intersection(exclude_dirs):
            md_files.append(path)

    return md_files


def check_broken_wikilinks(vault_path: Path, md_files: list[Path]) -> dict[str, list[str]]:
    """Checks for wikilinks pointing to non-existent files."""
    # Build set of all note stems (filename without extension)
    note_stems: dict[str, Path] = {}
    for md_file in md_files:
        stem = md_file.stem
        note_stems[stem.lower()] = md_file

    broken_wikilinks: dict[str, list[str]] = {}

    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8', errors='replace')
        wikilinks = extract_wikilinks(content)

        for link in wikilinks:
            # Handle wikilinks with aliases (e.g., [[Note|Alias]])
            link_target = link.split('|')[0].strip()
            link_stem = link_target.lower()

            if link_stem not in note_stems:
                rel_path = md_file.relative_to(vault_path)
                if str(rel_path) not in broken_wikilinks:
                    broken_wikilinks[str(rel_path)] = []
                broken_wikilinks[str(rel_path)].append(link_target)

    return broken_wikilinks


def _rel_posix(md_file: Path, vault_path: Path | None) -> str:
    if vault_path is not None:
        try:
            return md_file.relative_to(vault_path).as_posix()
        except ValueError:
            pass
    return md_file.as_posix()


def check_orphan_notes(
    md_files: list[Path], db_path: Path | None = None, vault_path: Path | None = None
) -> list[str]:
    """Detects notes with no incoming/outgoing links and no memory_relations entry."""
    linked_notes: dict[str, set[str]] = {}
    linked_from: dict[str, set[str]] = {}

    note_stems: dict[str, str] = {}
    for md_file in md_files:
        note_stems[md_file.stem.lower()] = _rel_posix(md_file, vault_path)

    for md_file in md_files:
        content = md_file.read_text(encoding='utf-8', errors='replace')
        wikilinks = extract_wikilinks(content)

        rel_path = _rel_posix(md_file, vault_path)
        linked_notes.setdefault(rel_path, set())

        for link in wikilinks:
            link_target = link.split('|')[0].strip()
            target_path = note_stems.get(link_target.lower())
            if target_path:
                linked_notes[rel_path].add(target_path)
                linked_from.setdefault(target_path, set()).add(rel_path)

    orphans = []
    conn = None
    if db_path and db_path.exists():
        try:
            conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
        except Exception:
            conn = None

    try:
        for md_file in md_files:
            if md_file.name in _NON_MEMORY_FILENAMES:
                continue
            rel_path = _rel_posix(md_file, vault_path)
            has_outgoing = bool(linked_notes.get(rel_path, set()))
            has_incoming = bool(linked_from.get(rel_path, set()))

            if not has_outgoing and not has_incoming:
                has_db_entry = False
                if conn is not None:
                    try:
                        mem_row = conn.execute(
                            'SELECT id FROM memories WHERE path = ?', (rel_path,)
                        ).fetchone()
                        if mem_row:
                            mem_id = mem_row['id']
                            rel_row = conn.execute(
                                'SELECT COUNT(*) as cnt FROM memory_relations WHERE source_id = ? OR target_id = ?',
                                (mem_id, mem_id),
                            ).fetchone()
                            has_db_entry = rel_row['cnt'] > 0 if rel_row else False
                    except Exception:
                        pass

                if not has_db_entry:
                    orphans.append(rel_path)
    finally:
        if conn is not None:
            conn.close()

    return orphans


def check_incomplete_frontmatter(
    md_files: list[Path], vault_path: Path | None = None
) -> dict[str, dict[str, list[str]]]:
    """Checks for notes with incomplete frontmatter."""
    incomplete: dict[str, dict[str, list[str]]] = {}

    for md_file in md_files:
        if md_file.name in _NON_MEMORY_FILENAMES:
            continue
        content = md_file.read_text(encoding='utf-8', errors='replace')
        metadata, _ = parse_yaml_frontmatter(content)

        missing = []
        for field in REQUIRED_FRONTMATTER_FIELDS:
            if field not in metadata or metadata[field] in ('', None):
                missing.append(field)

        if missing:
            rel_path = _rel_posix(md_file, vault_path)
            incomplete[rel_path] = {'missing_fields': missing}

    return incomplete


def check_superseded_mismatches(
    md_files: list[Path], db_path: Path | None = None, vault_path: Path | None = None
) -> dict[str, Any]:
    """Detects mismatches between superseded_by references in DB vs Markdown."""
    mismatches: dict[str, Any] = {
        'db_says_superseded_but_no_ref': [],
        'md_says_superseded_but_no_in_db': [],
        'circular_references': []
    }

    if not db_path or not db_path.exists():
        return mismatches

    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute(
            'SELECT id, title, path, superseded_by FROM memories '
            'WHERE status = "superseded" OR superseded_by IS NOT NULL'
        )
        superseded_in_db = {row['path']: row['superseded_by'] for row in cursor.fetchall()}

        for md_file in md_files:
            content = md_file.read_text(encoding='utf-8', errors='replace')
            metadata, _ = parse_yaml_frontmatter(content)

            rel_path = _rel_posix(md_file, vault_path)
            has_superseded_ref = metadata.get('superseded_by') not in (None, '')
            db_superseded_by = superseded_in_db.get(rel_path)

            if rel_path in superseded_in_db and db_superseded_by and not has_superseded_ref:
                mismatches['db_says_superseded_but_no_ref'].append({
                    'path': rel_path,
                    'expected_ref': db_superseded_by
                })
            elif has_superseded_ref and rel_path not in superseded_in_db:
                mismatches['md_says_superseded_but_no_in_db'].append(rel_path)

        conn.close()
    except Exception:
        pass

    return mismatches


def lint_vault(vault_path: Path, db_path: Path | None = None, **kwargs) -> dict[str, Any]:
    """Main entry point for vault linting.

    Returns a diagnostic report with all integrity issues found.
    """
    vault_path = vault_path.resolve()
    if db_path:
        db_path = db_path.resolve()

    # Get all MD files
    md_files = get_all_md_files(vault_path, set(kwargs.get('exclude_dirs', [])))

    # Run all checks
    broken_wikilinks = check_broken_wikilinks(vault_path, md_files)
    orphan_notes = check_orphan_notes(md_files, db_path, vault_path=vault_path)
    incomplete_frontmatter = check_incomplete_frontmatter(md_files, vault_path=vault_path)
    superseded_mismatches = check_superseded_mismatches(md_files, db_path, vault_path=vault_path)

    # Build report
    report = {
        'vault_path': str(vault_path),
        'db_path': str(db_path) if db_path else None,
        'scanned_at': datetime.utcnow().isoformat() + 'Z',
        'stats': {
            'total_notes': len(md_files),
            'broken_wikilinks': sum(len(v) for v in broken_wikilinks.values()),
            'orphan_notes': len(orphan_notes),
            'incomplete_frontmatter': len(incomplete_frontmatter),
            'superseded_mismatches': (
                len(superseded_mismatches['db_says_superseded_but_no_ref']) +
                len(superseded_mismatches['md_says_superseded_but_no_in_db'])
            )
        },
        'issues': {
            'broken_wikilinks': broken_wikilinks,
            'orphan_notes': orphan_notes,
            'incomplete_frontmatter': incomplete_frontmatter,
            'superseded_mismatches': superseded_mismatches
        },
        'is_clean': (
            not broken_wikilinks and
            not orphan_notes and
            not incomplete_frontmatter and
            not any(superseded_mismatches.values())
        )
    }

    return report


def main() -> None:
    """CLI entry point for brain_lint."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Lint AI-BRAIN vault for integrity issues (broken links, orphans, incomplete frontmatter)'
    )
    parser.add_argument('--vault', required=True, type=Path,
                        help='Path to Obsidian vault root directory')
    parser.add_argument('--db', type=Path, help='Path to SQLite database (optional)')
    parser.add_argument('--output', type=Path, help='Output JSON file (default: stdout)')
    parser.add_argument('--json', action='store_true', help='Output JSON to stdout (default)')
    parser.add_argument('--exclude-dirs', type=str, nargs='*', default=[
        '.git', '.obsidian', '__pycache__', '.pytest_cache', '90_System/db'
    ], help='Directories to exclude from scanning')
    args = parser.parse_args()

    report = lint_vault(
        vault_path=args.vault,
        db_path=args.db,
        exclude_dirs=args.exclude_dirs
    )

    output_json = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        args.output.write_text(output_json + '\n', encoding='utf-8')
        print(f'Lint report written to {args.output}')
    else:
        print(output_json)

    # Exit with error code if issues found
    sys.exit(0 if report['is_clean'] else 1)


if __name__ == '__main__':
    main()