#!/usr/bin/env python3
"""Optional Vault Synthesis tool — consolidates recent Episodic memories into Semantic or Procedural notes.

Standalone CLI utility separated from the core MCP server. Zero external dependencies.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT_PARENT = Path(__file__).resolve().parent.parent
for _candidate in (_ROOT_PARENT / "mcp-server", _ROOT_PARENT / "scripts"):
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

try:
    from sanitizer import Sanitizer
except ImportError:
    class Sanitizer:  # type: ignore[no-redef]
        _PATTERNS = [
            re.compile(r"(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{16,}|glpat-[A-Za-z0-9_-]{12,}|xoxb-[A-Za-z0-9-]+)"),
            re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
        ]
        def clean(self, text: str) -> str:
            for pat in self._PATTERNS:
                text = pat.sub("[REDACTED]", text)
            return text


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Extracts simple YAML frontmatter key-value pairs and the markdown body."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, parts[2].lstrip("\n")


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:19], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def synthesize_vault(
    vault_path: Path,
    days: int = 30,
    target_type: str = "memory/semantic",
    min_group_size: int = 2,
    dry_run: bool = False,
    writable_inbox: str | None = None,
) -> list[Path]:
    """Consolidates recent active Episodic notes into Semantic or Procedural notes."""
    episodic_dirs = [vault_path / "50_Memory" / "Episodic"]
    if writable_inbox:
        episodic_dirs.append(vault_path / writable_inbox / "Episodic")
    episodic_dirs = [d for d in episodic_dirs if d.is_dir()]
    if not episodic_dirs:
        return []

    target_subdir = "Procedural" if "procedural" in target_type.lower() else "Semantic"
    if writable_inbox:
        out_dir = vault_path / writable_inbox / target_subdir
    else:
        out_dir = vault_path / "50_Memory" / target_subdir

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    groups: dict[str, list[tuple[Path, dict[str, str], str]]] = {}

    for episodic_dir in episodic_dirs:
      for md_file in sorted(episodic_dir.glob("*.md")):
        raw = md_file.read_text(encoding="utf-8", errors="replace")
        meta, body = _parse_frontmatter(raw)
        status = meta.get("status", "active").lower()
        if status in ("archived", "superseded"):
            continue

        dt = _parse_date(meta.get("created_at") or meta.get("updated_at"))
        if dt is None:
            # Try date prefix in filename e.g. 2026-11-10
            m = re.match(r"^(\d{4}-\d{2}-\d{2})", md_file.stem)
            if m:
                dt = _parse_date(m.group(1))
        if dt is not None and dt < cutoff:
            continue

        project = meta.get("project") or "General"
        groups.setdefault(project, []).append((md_file, meta, body))

    created_files: list[Path] = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sanitizer = Sanitizer()

    for project, items in groups.items():
        if len(items) < min_group_size:
            continue

        safe_proj = re.sub(r'[<>:"/\\|?*]', "", project).strip() or "General"
        title = f"{safe_proj} Synthesis"
        out_path = out_dir / f"{title}.md"

        provenance_links = [f"[[{p.stem}]]" for p, _, _ in items]
        sections: list[str] = []
        for p, meta, body in items:
            note_title = meta.get("title") or p.stem
            clean_body = sanitizer.clean(body.strip())
            sections.append(f"### {note_title}\nSource: [[{p.stem}]]\n\n{clean_body}")

        rel_type = "memory/procedural" if target_subdir == "Procedural" else "memory/semantic"
        content = (
            f"---\n"
            f"title: {title}\n"
            f"type: {rel_type}\n"
            f"status: active\n"
            f"project: {safe_proj}\n"
            f"created_at: {now_str}\n"
            f"updated_at: {now_str}\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"Consolidated from {len(items)} episodic memories: {', '.join(provenance_links)}\n\n"
            + "\n\n".join(sections)
            + "\n"
        )

        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content, encoding="utf-8")
        created_files.append(out_path)

    return created_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Consolidate recent Episodic memories.")
    parser.add_argument("--vault", required=True, help="Path to the Obsidian vault")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--target-type", default="memory/semantic", help="Target memory type for synthesized notes")
    parser.add_argument("--db", default=None, help="Optional SQLite database path")
    parser.add_argument("--min-group-size", type=int, default=2, help="Minimum notes per group")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--writable-inbox", default=None, help="Optional writable inbox subfolder")
    args = parser.parse_args()

    created = synthesize_vault(
        vault_path=Path(args.vault).expanduser().resolve(),
        days=args.days,
        target_type=args.target_type,
        min_group_size=args.min_group_size,
        dry_run=args.dry_run,
        writable_inbox=args.writable_inbox,
    )
    print(f"Synthesized {len(created)} note(s).")


if __name__ == "__main__":
    main()
