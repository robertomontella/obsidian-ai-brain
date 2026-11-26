#!/usr/bin/env python3
"""tools/brain_ingest.py — Code and Project Repository Ingestion Scanner for AI-BRAIN.

Deterministic file & repository scanner in pure Python 3.9+ Standard Library.
Extracts project structure, Git history, tech stack, manifests, dependencies,
and entry points, rigorously pruning noise, locks, binary artifacts, and media.
Generates structured Markdown ready for Obsidian 10_Projects/ or AI agents.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

# Directories to strictly prune
IGNORE_DIRS = {
    ".git",
    "node_modules",
    "pods",
    "vendor",
    ".venv",
    "venv",
    "env",
    "build",
    "dist",
    "deriveddata",
    ".next",
    "target",
    "__pycache__",
    ".idea",
    ".vscode",
    ".gradle",
    "bin",
    "obj",
    ".turbo",
    "coverage",
    ".tox",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
}

# Lock files to exclude
IGNORE_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "poetry.lock",
    "pipfile.lock",
    "composer.lock",
    "gemfile.lock",
    ".ds_store",
    "thumbs.db",
}

# Binary, media, and heavy extensions to prune
IGNORE_EXTS = {
    ".pyc",
    ".pyo",
    ".min.js",
    ".min.css",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".bmp",
    ".tiff",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
    ".dmg",
    ".pkg",
    ".a",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".o",
    ".class",
    ".jar",
    ".war",
    ".ear",
    ".wasm",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp3",
    ".mp4",
    ".mov",
    ".wav",
    ".m4a",
    ".avi",
    ".mkv",
    ".flac",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".log",
}

# Recognized code extensions for stack identification
CODE_EXTS = {
    ".swift",
    ".m",
    ".h",
    ".c",
    ".cpp",
    ".cc",
    ".java",
    ".kt",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".py",
    ".rb",
    ".go",
    ".rs",
    ".php",
    ".cs",
    ".sh",
    ".sql",
    ".html",
    ".css",
    ".scss",
    ".vue",
    ".svelte",
}

# Recognized documentation extensions
DOC_EXTS = {".md", ".markdown", ".txt", ".rst"}

# Project manifests
MANIFEST_FILES = {
    "package.json",
    "cargo.toml",
    "pyproject.toml",
    "requirements.txt",
    "pipfile",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "podfile",
    "package.swift",
    "composer.json",
    "gemfile",
    "dockerfile",
    "docker-compose.yml",
    "makefile",
    "cmakelists.txt",
}

ENTRY_POINT_NAMES = {
    "main",
    "app",
    "index",
    "server",
    "cli",
    "entry",
    "program",
    "run",
}


MANIFEST_DISPLAY_NAMES = {
    "package.json": "package.json",
    "cargo.toml": "Cargo.toml",
    "pyproject.toml": "pyproject.toml",
    "requirements.txt": "requirements.txt",
    "pipfile": "Pipfile",
    "go.mod": "go.mod",
    "pom.xml": "pom.xml",
    "build.gradle": "build.gradle",
    "build.gradle.kts": "build.gradle.kts",
    "podfile": "Podfile",
    "package.swift": "Package.swift",
    "composer.json": "composer.json",
    "gemfile": "Gemfile",
    "dockerfile": "Dockerfile",
    "docker-compose.yml": "docker-compose.yml",
    "makefile": "Makefile",
    "cmakelists.txt": "CMakeLists.txt",
}


def inspect_git(root_path: Path) -> dict[str, Any]:
    """Inspects Git repository metadata and captures the last 15 commits."""
    git_info: dict[str, Any] = {
        "is_repo": False,
        "remote": "",
        "branch": "",
        "total_commits": 0,
        "first_commit": "",
        "last_commit": "",
        "recent_commits": [],
    }

    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0 or res.stdout.strip() != "true":
            return git_info

        # Check if root_path or parent is the repo root
        toplevel_res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if toplevel_res.returncode == 0:
            toplevel = Path(toplevel_res.stdout.strip()).resolve()
            # If target is inside a git repo, consider it git-tracked
            git_info["is_repo"] = True
    except Exception:
        return git_info

    try:
        # Branch
        b_res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if b_res.returncode == 0:
            git_info["branch"] = b_res.stdout.strip()

        # Remote URL
        r_res = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if r_res.returncode == 0:
            git_info["remote"] = r_res.stdout.strip()

        # Total commits
        c_res = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if c_res.returncode == 0 and c_res.stdout.strip().isdigit():
            git_info["total_commits"] = int(c_res.stdout.strip())

        # First commit date
        fc_res = subprocess.run(
            ["git", "log", "--reverse", "--format=%ad (%cr)", "--date=short", "-n", "1"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if fc_res.returncode == 0:
            git_info["first_commit"] = fc_res.stdout.strip()

        # Last commit date
        lc_res = subprocess.run(
            ["git", "log", "--format=%ad (%cr)", "--date=short", "-n", "1"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if lc_res.returncode == 0:
            git_info["last_commit"] = lc_res.stdout.strip()

        # Last 15 commits (per task specification: git log -n 15 --oneline)
        rc_res = subprocess.run(
            ["git", "log", "-n", "15", "--oneline"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if rc_res.returncode == 0 and rc_res.stdout.strip():
            git_info["recent_commits"] = rc_res.stdout.strip().splitlines()
    except Exception:
        pass

    return git_info


def parse_manifest_file(file_path: Path) -> dict[str, Any]:
    """Parses manifest files in pure Python standard library."""
    fname = file_path.name.lower()
    info: dict[str, Any] = {
        "file": file_path.name,
        "type": MANIFEST_DISPLAY_NAMES.get(fname, file_path.name),
        "project_name": "",
        "version": "",
        "entry_point": "",
        "dependencies": [],
        "dev_dependencies": [],
        "scripts": {},
    }

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return info

    if fname == "package.json":
        try:
            data = json.loads(content)
            info["project_name"] = data.get("name", "")
            info["version"] = data.get("version", "")
            info["entry_point"] = data.get("main", "")
            info["dependencies"] = sorted(list(data.get("dependencies", {}).keys()))
            info["dev_dependencies"] = sorted(list(data.get("devDependencies", {}).keys()))
            info["scripts"] = data.get("scripts", {})
        except Exception:
            pass

    elif fname == "cargo.toml":
        # Package name and version
        name_match = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', content)
        if name_match:
            info["project_name"] = name_match.group(1)
        ver_match = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', content)
        if ver_match:
            info["version"] = ver_match.group(1)

        # Parse sections
        current_section = None
        for line in content.splitlines():
            line_clean = line.strip()
            if line_clean.startswith("[") and line_clean.endswith("]"):
                current_section = line_clean.strip("[] ").lower()
                continue
            if not line_clean or line_clean.startswith("#"):
                continue
            if "=" in line_clean:
                dep_name = line_clean.split("=")[0].strip()
                if current_section == "dependencies":
                    info["dependencies"].append(dep_name)
                elif current_section in ("dev-dependencies", "build-dependencies"):
                    info["dev_dependencies"].append(dep_name)

    elif fname == "pyproject.toml":
        # Extract project or tool.poetry name
        name_match = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', content)
        if name_match:
            info["project_name"] = name_match.group(1)
        ver_match = re.search(r'(?m)^\s*version\s*=\s*["\']([^"\']+)["\']', content)
        if ver_match:
            info["version"] = ver_match.group(1)

        # Standard PEP 621 dependencies = [...]
        dep_list_match = re.search(r'(?s)dependencies\s*=\s*\[(.*?)\]', content)
        if dep_list_match:
            raw_deps = dep_list_match.group(1)
            for d in re.findall(r'["\']([^"\']+)["\']', raw_deps):
                info["dependencies"].append(d.strip())

        # Poetry-style [tool.poetry.dependencies]
        poetry_dep_match = re.search(r'(?s)\[tool\.poetry\.dependencies\](.*?)(?=\n\[|$)', content)
        if poetry_dep_match:
            for line in poetry_dep_match.group(1).splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k = line.split("=")[0].strip()
                    if k.lower() != "python" and k not in info["dependencies"]:
                        info["dependencies"].append(k)

    elif fname == "requirements.txt":
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                dep = line.split(";")[0].strip()
                if dep:
                    info["dependencies"].append(dep)

    elif fname == "pipfile":
        current_sec = None
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current_sec = line.strip("[]").lower()
                continue
            if "=" in line and current_sec == "packages":
                info["dependencies"].append(line.split("=")[0].strip())
            elif "=" in line and current_sec == "dev-packages":
                info["dev_dependencies"].append(line.split("=")[0].strip())

    elif fname == "go.mod":
        mod_match = re.search(r'(?m)^\s*module\s+([^\s]+)', content)
        if mod_match:
            info["project_name"] = mod_match.group(1)
        go_ver_match = re.search(r'(?m)^\s*go\s+([^\s]+)', content)
        if go_ver_match:
            info["version"] = f"go{go_ver_match.group(1)}"

        # Require block or individual requires
        in_require_block = False
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("require ("):
                in_require_block = True
                continue
            elif in_require_block and line == ")":
                in_require_block = False
                continue
            if in_require_block:
                if line and not line.startswith("//"):
                    parts = line.split()
                    if parts:
                        info["dependencies"].append(parts[0])
            elif line.startswith("require "):
                parts = line.split()
                if len(parts) >= 2:
                    info["dependencies"].append(parts[1])

    elif fname == "pom.xml":
        art_match = re.search(r"<artifactId>([^<]+)</artifactId>", content)
        if art_match:
            info["project_name"] = art_match.group(1).strip()
        ver_match = re.search(r"<version>([^<]+)</version>", content)
        if ver_match:
            info["version"] = ver_match.group(1).strip()
        deps = re.findall(r"<artifactId>([^<]+)</artifactId>", content)
        if len(deps) > 1:
            info["dependencies"] = [d.strip() for d in deps[1:15]]

    elif fname == "podfile":
        pods = re.findall(r"(?m)^\s*pod\s+['\"]([^'\"]+)['\"]", content)
        info["dependencies"] = pods

    elif fname == "composer.json":
        try:
            data = json.loads(content)
            info["project_name"] = data.get("name", "")
            info["dependencies"] = sorted(list(data.get("require", {}).keys()))
            info["dev_dependencies"] = sorted(list(data.get("require-dev", {}).keys()))
        except Exception:
            pass

    return info


def scan_project(target: str | Path, max_depth: int = 4) -> dict[str, Any]:
    """Scans the target repository or directory, extracting project metadata,

    languages, manifests, entry points, docs, and directory tree.
    """
    target_path = Path(target).expanduser().resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Target path does not exist: {target_path}")

    project_name = target_path.name
    git_info = inspect_git(target_path)

    if target_path.is_file():
        # Single file ingestion
        content_preview = ""
        try:
            content_preview = target_path.read_text(encoding="utf-8", errors="ignore")[:2000]
        except Exception:
            pass

        ext = target_path.suffix.lower()
        languages = {ext.lstrip("."): 1} if ext in CODE_EXTS else {}

        return {
            "target_path": str(target_path),
            "project_name": project_name,
            "target_type": "single_file",
            "git_info": git_info,
            "languages": languages,
            "manifests": [],
            "entry_points": [{"file": target_path.name, "snippet": content_preview[:500]}],
            "docs": {"readme": content_preview if ext in DOC_EXTS else ""},
            "subprojects": [],
            "directory_tree": f"📄 {target_path.name}",
        }

    # Directory walk
    ext_counter: Counter[str] = Counter()
    found_manifests: list[dict[str, Any]] = []
    found_docs: dict[str, str] = {}
    entry_point_candidates: list[dict[str, Any]] = []
    subprojects: list[dict[str, Any]] = []
    tree_lines: list[str] = []

    root_depth = len(target_path.parts)

    for root, dirs, files in os.walk(target_path):
        current_depth = len(Path(root).parts) - root_depth

        # Prune noise directories in-place (case-insensitive)
        dirs[:] = [
            d
            for d in dirs
            if d.lower() not in IGNORE_DIRS and not d.startswith(".")
        ]
        dirs.sort()

        if current_depth > max_depth:
            dirs.clear()
            continue

        rel_dir = os.path.relpath(root, target_path)
        indent = "  " * current_depth

        if rel_dir != ".":
            tree_lines.append(f"{indent}📁 {os.path.basename(root)}/")
            # Subproject check (nested manifest)
            nested_manifests = [f for f in files if f.lower() in MANIFEST_FILES]
            if nested_manifests:
                subprojects.append({
                    "name": os.path.basename(root),
                    "rel_path": rel_dir,
                    "manifests": nested_manifests,
                })

        for file in sorted(files):
            file_lower = file.lower()
            file_path = Path(root) / file
            ext = file_path.suffix.lower()

            # Ignore lock files and system junk
            if file_lower in IGNORE_FILES:
                continue

            # Ignore hidden files (except .gitignore)
            if file.startswith(".") and file not in {".gitignore"}:
                continue

            # Ignore binaries and media
            if ext in IGNORE_EXTS:
                continue

            # Count code languages
            if ext in CODE_EXTS:
                ext_counter[ext.lstrip(".")] += 1
            elif ext in DOC_EXTS:
                ext_counter["docs"] += 1

            # Manifest parsing
            if file_lower in MANIFEST_FILES:
                manifest_info = parse_manifest_file(file_path)
                manifest_info["rel_path"] = str(Path(rel_dir) / file) if rel_dir != "." else file
                found_manifests.append(manifest_info)
                # If root manifest has project name, update project_name
                if rel_dir == "." and manifest_info.get("project_name"):
                    project_name = manifest_info["project_name"]

            # Documentation capture
            stem_lower = file_path.stem.lower()
            if stem_lower in {"readme", "architecture", "contributing"} and ext in DOC_EXTS:
                if stem_lower not in found_docs or rel_dir == ".":
                    try:
                        found_docs[stem_lower] = file_path.read_text(encoding="utf-8", errors="ignore")[:3000]
                    except Exception:
                        pass

            # Entry points detection
            is_entry = (
                stem_lower in ENTRY_POINT_NAMES
                or stem_lower.endswith("app")
                or stem_lower.endswith("main")
                or stem_lower.endswith("server")
            )
            if is_entry and ext in CODE_EXTS:
                try:
                    snippet_lines = []
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for _ in range(35):
                            line = f.readline()
                            if not line:
                                break
                            snippet_lines.append(line)
                    entry_point_candidates.append({
                        "file": str(Path(rel_dir) / file) if rel_dir != "." else file,
                        "snippet": "".join(snippet_lines),
                    })
                except Exception:
                    pass

            # Tree building (limit to 250 items to avoid excessive size)
            if len(tree_lines) < 250:
                file_indent = "  " * (current_depth + 1)
                tree_lines.append(f"{file_indent}📄 {file}")

    # Determine classification
    code_count = sum(count for ext, count in ext_counter.items() if ext != "docs")
    target_type = "project" if (git_info["is_repo"] or found_manifests or code_count > 0) else "area"

    lang_dict = {k: v for k, v in ext_counter.items() if k != "docs"}
    sorted_languages = dict(sorted(lang_dict.items(), key=lambda x: x[1], reverse=True))

    return {
        "target_path": str(target_path),
        "project_name": project_name,
        "target_type": target_type,
        "git_info": git_info,
        "languages": sorted_languages,
        "manifests": found_manifests,
        "entry_points": entry_point_candidates[:6],
        "docs": found_docs,
        "subprojects": subprojects,
        "directory_tree": "\n".join(tree_lines),
    }


def format_project_markdown(scan_data: dict[str, Any]) -> str:
    """Formats scanned project metadata into clean, structured Markdown ready for Obsidian."""
    p_name = scan_data["project_name"]
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = []
    lines.append(f"# Progetto: {p_name}")
    lines.append("")
    lines.append(f"- **Target Path:** `{scan_data['target_path']}`")
    lines.append(f"- **Classificazione:** `{scan_data['target_type'].upper()}`")
    lines.append(f"- **Data Scansione:** {now_str}")

    git = scan_data["git_info"]
    if git["is_repo"]:
        branch_info = f" | **Branch:** `{git['branch']}`" if git.get("branch") else ""
        lines.append(
            f"- **Git Repository:** Sì | **Commits:** {git['total_commits']}{branch_info}"
        )
        if git.get("first_commit") or git.get("last_commit"):
            lines.append(f"- **Cronologia Commit:** Primo: `{git['first_commit']}` | Ultimo: `{git['last_commit']}`")
        if git.get("remote"):
            lines.append(f"- **Remote Origin:** `{git['remote']}`")
    else:
        lines.append("- **Git Repository:** No (directory locale non tracciata da git)")

    # Tech Stack
    lines.append("\n## 🛠️ Tech Stack & Linguaggi")
    if scan_data["languages"]:
        lang_parts = [f"**{lang}**: {cnt} file" for lang, cnt in scan_data["languages"].items()]
        lines.append(f"- **Linguaggi rilevati:** {', '.join(lang_parts)}")
    else:
        lines.append("- **Linguaggi:** Nessun file sorgente riconosciuto.")

    if scan_data.get("subprojects"):
        lines.append(f"- **Sottoprogetti identificati:** {len(scan_data['subprojects'])}")
        for sp in scan_data["subprojects"]:
            lines.append(f"  - `{sp['rel_path']}` ({', '.join(sp['manifests'])})")

    # Manifests and Dependencies
    if scan_data["manifests"]:
        lines.append("\n## 📦 Manifests & Dipendenze")
        for m in scan_data["manifests"]:
            m_title = f"`{m['rel_path']}`"
            if m.get("project_name"):
                m_title += f" ({m['project_name']})"
            lines.append(f"### {m_title}")

            if m.get("entry_point"):
                lines.append(f"- **Entry Point Principale:** `{m['entry_point']}`")
            if m.get("scripts"):
                script_keys = ", ".join(f"`{k}`" for k in m["scripts"].keys())
                lines.append(f"- **Scripts NPM:** {script_keys}")
            if m.get("dependencies"):
                dep_str = ", ".join(f"`{d}`" for d in m["dependencies"][:20])
                if len(m["dependencies"]) > 20:
                    dep_str += f" e altri {len(m['dependencies']) - 20}..."
                lines.append(f"- **Dipendenze:** {dep_str}")
            if m.get("dev_dependencies"):
                dev_str = ", ".join(f"`{d}`" for d in m["dev_dependencies"][:15])
                if len(m["dev_dependencies"]) > 15:
                    dev_str += f" e altri {len(m['dev_dependencies']) - 15}..."
                lines.append(f"- **Dev Dipendenze:** {dev_str}")

    # Entry Points
    if scan_data["entry_points"]:
        lines.append("\n## 🚀 Architettura & Entry Points")
        for ep in scan_data["entry_points"]:
            lines.append(f"### `{ep['file']}`")
            lines.append("```")
            lines.append(ep["snippet"].strip())
            lines.append("```")

    # Recent Git Commits
    if git["recent_commits"]:
        lines.append("\n## 🕒 Ultimi Commit Git")
        for c in git["recent_commits"][:15]:
            lines.append(f"- `{c}`")

    # Directory Tree
    lines.append("\n## 🌲 Albero Directory (Sintesi Pulita)")
    lines.append("```text")
    lines.append(scan_data["directory_tree"].strip())
    lines.append("```")

    # README preview
    if scan_data["docs"].get("readme"):
        lines.append("\n## 📖 Documentazione & README Preview")
        lines.append("```markdown")
        lines.append(scan_data["docs"]["readme"].strip()[:1500])
        lines.append("```")

    return "\n".join(lines)


def ingest_project(
    target: str | Path,
    output_path: str | Path | None = None,
    vault_path: str | Path | None = None,
    max_depth: int = 4,
) -> tuple[dict[str, Any], str, Path | None]:
    """Ingests a project target, formatting markdown and optionally saving to destination."""
    scan_data = scan_project(target, max_depth=max_depth)
    md_content = format_project_markdown(scan_data)
    saved_path: Path | None = None

    if output_path:
        saved_path = Path(output_path).resolve()
        saved_path.parent.mkdir(parents=True, exist_ok=True)
        saved_path.write_text(md_content, encoding="utf-8")
    elif vault_path:
        v_path = Path(vault_path).resolve()
        proj_dir = v_path / "10_Projects"
        proj_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-.]", "_", scan_data["project_name"])
        saved_path = proj_dir / f"{safe_name}.md"
        saved_path.write_text(md_content, encoding="utf-8")

    return scan_data, md_content, saved_path


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for brain_ingest."""
    parser = argparse.ArgumentParser(
        description="AI-BRAIN Code and Project Ingestion Scanner"
    )
    parser.add_argument("target", help="Path to project repository or file")
    parser.add_argument("-o", "--output", help="Destination path for generated Markdown")
    parser.add_argument("-v", "--vault", help="Obsidian vault path (saves to 10_Projects/<project>.md)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON analysis")
    parser.add_argument("--max-depth", type=int, default=4, help="Maximum directory traversal depth (default 4)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Quiet mode (suppress stdout when saving to file)")

    args = parser.parse_args(argv)

    try:
        scan_data, md_content, saved_file = ingest_project(
            args.target,
            output_path=args.output,
            vault_path=args.vault,
            max_depth=args.max_depth,
        )

        if args.json:
            print(json.dumps(scan_data, indent=2))
        elif saved_file:
            if not args.quiet:
                print(f"Successfully ingested project '{scan_data['project_name']}'.")
                print(f"Saved to: {saved_file}")
        else:
            print(md_content)

        return 0
    except Exception as e:
        print(f"Error during project ingestion: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
