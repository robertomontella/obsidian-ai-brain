#!/usr/bin/env python3
"""Crew Log CLI tool — appends handoff entries to the crew logbook.

Usage:
    python crew_log.py --agent "AgentName" --activity "Description" [--files "f1.md,f2.md"] [--open-items "item1,item2"] [--project "ProjectName"]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure local packages can be imported
MCP_SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SERVER_DIR))

import json
from storage import StorageManager


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append a handoff entry to the crew logbook"
    )
    parser.add_argument("--agent", required=True, help="Agent name")
    parser.add_argument("--activity", required=True, help="Activity description")
    parser.add_argument("--files", default="", help="Comma-separated list of files touched")
    parser.add_argument("--open-items", default="", help="Comma-separated list of open items")
    parser.add_argument("--project", default="", help="Project name")
    args = parser.parse_args()

    vault_path = Path(".vault")  # Will be resolved by StorageManager
    db_path = Path("90_System/db/brain_memory.sqlite")

    storage = StorageManager(db_path=db_path, vault_path=vault_path)
    result = storage.write_crew_log(
        agent=args.agent,
        activity=args.activity,
        files=args.files.split(",") if args.files else None,
        open_items=args.open_items.split(",") if args.open_items else None,
        project=args.project if args.project else None,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()