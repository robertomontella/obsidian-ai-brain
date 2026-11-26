#!/usr/bin/env python3
"""tools/brain_doctor.py — AI-BRAIN Health Diagnostic Utility.

Checks vault health, database integrity, and MCP server readiness.
Pure Python 3.9+ Standard Library only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Import other tools
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

try:
    from lint_engine import lint_vault
except ImportError:
    lint_vault = None

try:
    from sync_db import sync_vault
except ImportError:
    sync_vault = None


def check_python_version() -> dict[str, Any]:
    """Verifies Python version >= 3.9."""
    version = sys.version_info
    return {
        "python_version": f"{version.major}.{version.minor}.{version.micro}",
        "meets_requirement": version >= (3, 9),
        "requirement": ">= 3.9"
    }


def check_sqlite_fts5() -> dict[str, Any]:
    """Verifies SQLite FTS5 extension availability."""
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE test_fts USING fts5(x);")
        return {
            "fts5_available": True,
            "sqlite_version": sqlite3.sqlite_version,
            "wal_mode": "available"
        }
    except Exception as e:
        return {
            "fts5_available": False,
            "error": str(e)
        }


def check_vault_structure(vault_path: Path) -> dict[str, Any]:
    """Verifies required PARA structure exists in vault."""
    required_paths = [
        "00_Inbox",
        "10_Projects",
        "20_Areas",
        "30_Resources",
        "40_Archive",
        "50_Memory/Episodic",
        "50_Memory/Semantic",
        "50_Memory/Procedural",
        "50_Memory/Working",
        "90_System",
    ]

    missing = []
    for rel_path in required_paths:
        if not (vault_path / rel_path).exists():
            missing.append(rel_path)

    return {
        "vault_exists": vault_path.exists(),
        "missing_paths": missing,
        "structure_complete": len(missing) == 0
    }


def check_database(db_path: Path | None) -> dict[str, Any]:
    """Checks database integrity and content."""
    if not db_path:
        db_path = Path.home() / "AI-Brain" / "90_System" / "db" / "brain_memory.sqlite"

    if not db_path.exists():
        return {
            "db_exists": False,
            "db_path": str(db_path)
        }

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row

        # Check schema
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [t["name"] for t in tables]

        # Count records
        stats = {}
        for table in ["memories", "memory_relations", "crew_log"]:
            if table in table_names:
                count = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()["cnt"]
                stats[table] = count
            else:
                stats[table] = "missing"

        conn.close()

        return {
            "db_exists": True,
            "db_path": str(db_path),
            "tables": table_names,
            "record_counts": stats,
            "status": "healthy"
        }
    except Exception as e:
        return {
            "db_exists": True,
            "db_path": str(db_path),
            "error": str(e),
            "status": "error"
        }


def check_mcp_server(server_path: Path | None) -> dict[str, Any]:
    """Verifies MCP server file exists and is syntactically valid."""
    if not server_path:
        server_path = Path.cwd() / "mcp-server" / "server.py"

    if not server_path.exists():
        return {
            "server_exists": False,
            "server_path": str(server_path)
        }

    # Check syntax
    try:
        source = server_path.read_text(encoding="utf-8")
        compile(source, str(server_path), "exec")
        return {
            "server_exists": True,
            "server_path": str(server_path),
            "syntax_valid": True
        }
    except SyntaxError as e:
        return {
            "server_exists": True,
            "server_path": str(server_path),
            "syntax_valid": False,
            "syntax_error": str(e)
        }


def check_vault_integrity(vault_path: Path) -> dict[str, Any]:
    """Runs vault lint check if available."""
    if not lint_vault:
        return {"status": "lint_engine_not_available"}

    try:
        report = lint_vault(vault_path, db_path=vault_path / "90_System" / "db" / "brain_memory.sqlite")
        return {
            "is_clean": report.get("is_clean", False),
            "stats": report.get("stats", {}),
            "issue_count": sum(len(v) for v in report.get("issues", {}).values())
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


def run_health_check(
    vault_path: Path | None = None,
    db_path: Path | None = None,
    server_path: Path | None = None,
    verbose: bool = False
) -> dict[str, Any]:
    """Runs comprehensive health check of AI-BRAIN setup.

    Returns:
        Dictionary with health report and all check results.
    """
    if not vault_path:
        vault_path = Path.home() / "AI-Brain"

    report = {
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "vault_path": str(vault_path),
        "checks": {}
    }

    # Python version
    report["checks"]["python"] = check_python_version()

    # SQLite/FTS5
    report["checks"]["sqlite"] = check_sqlite_fts5()

    # Vault structure
    report["checks"]["vault_structure"] = check_vault_structure(vault_path)

    # Database
    report["checks"]["database"] = check_database(db_path)

    # MCP server
    report["checks"]["mcp_server"] = check_mcp_server(server_path)

    # Vault integrity
    if vault_path.exists():
        report["checks"]["vault_integrity"] = check_vault_integrity(vault_path)

    # Overall status
    all_healthy = all(
        c.get("meets_requirement", c.get("fts5_available", c.get("structure_complete", c.get("is_clean", c.get("status") == "healthy"))))
        for c in report["checks"].values()
    )
    report["overall_status"] = "healthy" if all_healthy else "needs_attention"

    return report


def main() -> None:
    """CLI entry point for brain_doctor."""
    import argparse

    parser = argparse.ArgumentParser(
        description="AI-BRAIN Health Diagnostic Utility"
    )
    parser.add_argument("--vault", type=Path, default=Path.home() / "AI-Brain",
                        help="Path to Obsidian vault root directory")
    parser.add_argument("--db", type=Path, help="Path to SQLite database")
    parser.add_argument("--server", type=Path, help="Path to MCP server.py")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--fix", action="store_true", help="Attempt to fix common issues")
    args = parser.parse_args()

    report = run_health_check(
        vault_path=args.vault,
        db_path=args.db,
        server_path=args.server
    )

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("=" * 60)
        print("  AI-BRAIN Health Diagnostic Report")
        print("=" * 60)
        print(f"\nChecked at: {report['checked_at']}")
        print(f"Vault path: {report['vault_path']}")
        print(f"Overall status: {report['overall_status'].upper()}")
        print("\n" + "-" * 60)

        for check_name, result in report["checks"].items():
            print(f"\n[Check: {check_name}]")

            for key, value in result.items():
                if isinstance(value, dict):
                    print(f"  {key}:")
                    for k, v in value.items():
                        print(f"    {k}: {v}")
                else:
                    print(f"  {key}: {value}")

        print("\n" + "=" * 60)

    sys.exit(0 if report["overall_status"] == "healthy" else 1)


if __name__ == "__main__":
    main()