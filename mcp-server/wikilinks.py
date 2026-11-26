#!/usr/bin/env python3
"""Wikilink injection CLI tool — injects [[wikilinks]] into notes based on related content.

Usage:
    python wikilinks.py --note "path/to/note.md" [--project "ProjectName"]
"""

from __future__ import annotations

import argparse
import sys
import re
from pathlib import Path

# Ensure local packages can be imported
MCP_SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SERVER_DIR))

from storage import StorageManager


WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def extract_wikilinks(content: str) -> list[str]:
    """Extract wikilink targets from markdown content."""
    return WIKILINK_RE.findall(content)


def inject_wikilinks(note_path: str, project: str | None = None) -> dict:
    """Inject related [[wikilinks]] into a note based on related existing notes."""
    note = Path(note_path)

    if not note.exists():
        return {"error": f"Note not found: {note_path}"}

    storage = StorageManager()

    # Read the note content
    content = note.read_text(encoding="utf-8")
    original_content = content

    # Run the internal _inject_wikilinks logic
    # This finds related notes and injects [[wikilinks]]
    record = {
        "path": str(note),
        "content": content,
        "project": project,
    }

    storage._inject_wikilinks(record)

    # Write back the modified content
    if content != original_content:
        note.write_text(content, encoding="utf-8")

    # Extract and return the wikilinks that were injected
    injected_links = extract_wikilinks(content)

    return {
        "path": str(note),
        "injected_wikilinks": injected_links,
        "original_wikilinks": extract_wikilinks(original_content),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inject wikilinks into a markdown note"
    )
    parser.add_argument("--note", required=True, help="Path to markdown note")
    parser.add_argument("--project", default="", help="Project name for scoping")
    args = parser.parse_args()

    result = inject_wikilinks(args.note, args.project)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()