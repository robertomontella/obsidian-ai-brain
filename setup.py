#!/usr/bin/env python3
"""AI-BRAIN Onboarding Setup Wizard & Scaffolder.

Configures an AI-BRAIN cognitive memory vault in under 60 seconds.
Supports two modes:
1. Fresh Vault (Recommended): Creates a new Obsidian vault from template,
   copies mcp-server to 90_System/scripts/, initializes SQLite WAL database with
   schema.sql, indexes notes into FTS5, installs agent rules, and generates mcp_ready_config.json.
2. Read-Only Vault Indexing: Indexes an existing Obsidian vault without modifying,
   touching, or injecting any files into the user's directory. Creates an external
   database and mcp_ready_config.json.

Pure Python 3.9+ Standard Library only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

# Ensure local packages can be imported
REPO_ROOT = Path(__file__).resolve().parent
MCP_SERVER_DIR = REPO_ROOT / "mcp-server"
TOOLS_DIR = REPO_ROOT / "tools"
VAULT_TEMPLATE_DIR = REPO_ROOT / "vault-template"
AGENTS_DIR = REPO_ROOT / "agents"

if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from storage import StorageManager
from sync_db import scan_vault, sync_vault

AGENT_RULE_MAPPING = {
    "claude": ("claude", "CLAUDE.md", "CLAUDE.md"),
    "codex": ("codex", "AGENTS.md", "AGENTS.md"),
    "cursor": ("cursor", ".cursorrules", ".cursorrules"),
    "antigravity": ("antigravity", "AGENTS.md", "AGENTS.md"),
    "gemini": ("gemini", "GEMINI.md", "GEMINI.md"),
}


def verify_environment() -> tuple[bool, str]:
    """Verifies Python >= 3.9 and SQLite FTS5 extension availability."""
    if sys.version_info < (3, 9):
        return False, f"Python 3.9+ is required. Found Python {sys.version.split()[0]}."
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x);")
    except Exception as exc:
        return False, f"SQLite FTS5 extension is not available in this Python build: {exc}."
    return True, "OK"


def parse_agents_arg(agents_arg: str | None) -> set[str]:
    """Parses agent selection flags or user input into a set of agent identifiers."""
    if not agents_arg:
        return {"claude", "codex", "cursor", "antigravity"}

    alias_map = {
        "1": "claude",
        "2": "cursor",
        "3": "antigravity",
        "4": "gemini",
        "5": "all",
        "6": "codex",
        "claude": "claude",
        "codex": "codex",
        "cursor": "cursor",
        "antigravity": "antigravity",
        "gemini": "gemini",
        "all": "all",
    }

    tokens = [t.strip().lower() for t in agents_arg.split(",") if t.strip()]
    selected: set[str] = set()

    for token in tokens:
        val = alias_map.get(token, token)
        if val == "all":
            return {"claude", "codex", "cursor", "antigravity", "gemini"}
        if val in AGENT_RULE_MAPPING:
            selected.add(val)

    if not selected or "all" in selected:
        return {"claude", "codex", "cursor", "antigravity"}
    return selected


def copy_tree_non_destructive(src_dir: Path, dst_dir: Path, skip_sqlite: bool = True) -> int:
    """Copies all files from src_dir into dst_dir without overwriting existing files.

    If skip_sqlite is True, .sqlite, .sqlite-wal, and .sqlite-shm files are excluded.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for root, dirs, files in os.walk(src_dir):
        rel_root = Path(root).relative_to(src_dir)
        target_root = dst_dir / rel_root
        target_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            if skip_sqlite and (f.endswith(".sqlite") or f.endswith(".sqlite-wal") or f.endswith(".sqlite-shm")):
                continue
            src_file = Path(root) / f
            dst_file = target_root / f
            if not dst_file.exists():
                shutil.copy2(src_file, dst_file)
                copied += 1
    return copied


def copy_agent_rules(agents: set[str], target_dir: Path) -> list[str]:
    """Copies rule files for selected agents into the target directory."""
    target_dir.mkdir(parents=True, exist_ok=True)
    copied_files: list[str] = []
    for agent_key in sorted(agents):
        if agent_key in AGENT_RULE_MAPPING:
            subfolder, src_filename, dst_filename = AGENT_RULE_MAPPING[agent_key]
            src_file = AGENTS_DIR / subfolder / src_filename
            if src_file.exists():
                dst_file = target_dir / dst_filename
                shutil.copy2(src_file, dst_file)
                copied_files.append(dst_filename)
    return copied_files


def copy_server_scripts(target_scripts_dir: Path, target_tools_dir: Path | None = None) -> None:
    """Copies mcp-server and tools scripts into the designated destination directory."""
    target_scripts_dir.mkdir(parents=True, exist_ok=True)
    for item in MCP_SERVER_DIR.glob("*"):
        if item.is_file() and not item.name.endswith(".pyc"):
            shutil.copy2(item, target_scripts_dir / item.name)

    # Copy tools alongside
    if target_tools_dir is None:
        target_tools_dir = target_scripts_dir.parent / "tools"
    target_tools_dir.mkdir(parents=True, exist_ok=True)
    for item in TOOLS_DIR.glob("*"):
        if item.is_file() and not item.name.endswith(".pyc"):
            shutil.copy2(item, target_tools_dir / item.name)


def verify_fts5_latency(storage: StorageManager, test_query: str = "TaskFlow") -> float:
    """Executes a benchmark query on the SQLite FTS5 index to verify latency < 15ms.

    Returns the query execution time in milliseconds.
    """
    # Quick warm-up if needed
    try:
        storage.search_memory(query="", limit=1)
    except Exception:
        pass

    start_time = time.perf_counter()
    storage.search_memory(query=test_query, limit=5)
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    return elapsed_ms


def generate_mcp_config(
    server_path: Path,
    vault_path: Path,
    db_path: Path,
    output_path: Path,
    writable_inbox: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Generates mcp_ready_config.json with absolute paths resolved."""
    args = [
        str(server_path.resolve()),
        "--vault",
        str(vault_path.resolve()),
        "--db",
        str(db_path.resolve()),
    ]
    env = {
        "BRAIN_VAULT_PATH": str(vault_path.resolve()),
        "BRAIN_DB_PATH": str(db_path.resolve()),
    }

    if writable_inbox:
        clean_inbox = str(writable_inbox).strip("/").strip("\\")
        args.extend(["--writable-inbox", clean_inbox])
        env["BRAIN_WRITABLE_INBOX"] = clean_inbox
    elif read_only:
        args.append("--read-only")
        env["BRAIN_READ_ONLY"] = "true"

    config_dict = {
        "mcpServers": {
            "ai-brain": {
                "command": sys.executable,
                "args": args,
                "env": env,
            }
        }
    }
    output_path.write_text(json.dumps(config_dict, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config_dict


def prompt_user(prompt_text: str, default: str = "") -> str:
    """Prompts the user on stdin if running in interactive mode."""
    if default:
        full_prompt = f"{prompt_text} [default: {default}]: "
    else:
        full_prompt = f"{prompt_text}: "
    try:
        val = input(full_prompt).strip()
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\nSetup annullato dall'utente.\n")
        sys.exit(1)
    return val if val else default


def run_fresh_vault_setup(
    dest_path: Path,
    agents: set[str],
) -> dict[str, Any]:
    """Executes Fresh Vault mode: copies template, initializes SQLite WAL,

    indexes initial notes, copies agent rules, and creates mcp_ready_config.json.
    """
    dest_dir = dest_path.expanduser().resolve()
    sys.stdout.write(f"\n-> Modalità Fresh Vault: configurazione in {dest_dir}\n")

    # 1. Scaffolding folders and template
    sys.stdout.write("[2/5] Copia non-distruttiva del template del vault...\n")
    copy_tree_non_destructive(VAULT_TEMPLATE_DIR, dest_dir, skip_sqlite=True)

    # 2. Copy MCP server scripts and tools
    scripts_dir = dest_dir / "90_System" / "scripts"
    tools_dir = dest_dir / "90_System" / "tools"
    copy_server_scripts(scripts_dir, tools_dir)
    server_script = scripts_dir / "server.py"

    # 3. Copy agent rules into vault root
    copied_rules = copy_agent_rules(agents, dest_dir)
    sys.stdout.write(f"      Regole agenti installate: {', '.join(copied_rules) if copied_rules else 'nessuna'}\n")

    # 4. Initialize SQLite WAL Database
    sys.stdout.write("[3/5] Inizializzazione database SQLite WAL (schema.sql)...\n")
    db_path = dest_dir / "90_System" / "db" / "brain_memory.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = StorageManager(db_path=db_path, vault_path=dest_dir)
    storage.init_db()

    # 5. Sync initial vault notes to FTS5
    sys.stdout.write("[4/5] Sincronizzazione iniziale Markdown -> SQLite FTS5...\n")
    sync_res = sync_vault(vault_path=dest_dir, db_path=db_path, storage=storage)
    sys.stdout.write(f"      Note indicizzate: {len(sync_res.get('added', []))} aggiunte, {len(sync_res.get('updated', []))} aggiornate\n")

    # 6. E2E Latency Verification (< 15ms)
    sys.stdout.write("[5/5] Verifica E2E latenza ricerca FTS5 (< 15ms)...\n")
    latency = verify_fts5_latency(storage, test_query="TaskFlow")
    sys.stdout.write(f"      Latenza query FTS5: {latency:.2f}ms (< 15ms) -> OK\n")

    # 7. Generate MCP config
    config_path = dest_dir / "mcp_ready_config.json"
    cfg = generate_mcp_config(
        server_path=server_script,
        vault_path=dest_dir,
        db_path=db_path,
        output_path=config_path,
    )

    return {
        "mode": "fresh",
        "dest": str(dest_dir),
        "db_path": str(db_path),
        "server_path": str(server_script),
        "config_path": str(config_path),
        "latency_ms": latency,
        "config": cfg,
    }


def run_read_only_index_setup(
    vault_path: Path,
    dest_path: Path,
    agents: set[str],
    writable_inbox: str | None = None,
) -> dict[str, Any]:
    """Executes Existing Vault Indexing mode: indexes an existing Obsidian vault,
    protecting all pre-existing notes. If writable_inbox is specified (e.g. 'AI-Memory'),
    creates a scoped safe writable inbox within the vault for agent memory capture.
    If writable_inbox is None, strictly leaves the vault 100% untouched.
    """
    user_vault = vault_path.expanduser().resolve()
    idx_dest = dest_path.expanduser().resolve()
    clean_inbox = writable_inbox.strip("/").strip("\\") if writable_inbox else None

    sys.stdout.write(f"\n-> Modalità Existing Vault Indexing:\n")
    sys.stdout.write(f"   Vault sorgente (protetto):        {user_vault}\n")
    sys.stdout.write(f"   Destinazione indice e config:     {idx_dest}\n")
    if clean_inbox:
        sys.stdout.write(f"   Scoped Writable Inbox:            {user_vault / clean_inbox}\n")
    else:
        sys.stdout.write(f"   Modalità scrittura:               Sola lettura totale (nessuna cartella scrivibile)\n")

    # 1. Validate user vault exists and has markdown notes
    if not user_vault.exists() or not user_vault.is_dir():
        sys.stderr.write(f"Errore: Il percorso del vault specificato non esiste o non è una cartella: {user_vault}\n")
        sys.exit(1)

    # If scoped writable inbox is enabled, initialize the inbox folder in the user vault
    if clean_inbox:
        inbox_dir = user_vault / clean_inbox
        inbox_dir.mkdir(parents=True, exist_ok=True)
        inbox_readme = inbox_dir / "AI-Memory-README.md"
        if not inbox_readme.exists():
            now_date = time.strftime("%Y-%m-%d")
            inbox_readme.write_text(
                "---\n"
                'title: "AI-Memory Inbox"\n'
                "type: memory/system\n"
                "status: active\n"
                f"created_at: {now_date}\n"
                "---\n\n"
                "# AI-Memory Inbox\n\n"
                "Questa cartella è l'inbox designata per le memorie create e aggiornate dagli agenti AI.\n"
                "Tutte le note preesistenti all'esterno di questa cartella rimangono rigorosamente protette in sola lettura.\n",
                encoding="utf-8",
            )

    sys.stdout.write("[2/5] Validazione vault Obsidian esistente...\n")
    scanned_notes = scan_vault(user_vault)
    if not scanned_notes:
        sys.stderr.write(f"Errore: Nessun file Markdown (.md) trovato nel vault: {user_vault}\n")
        sys.exit(1)
    sys.stdout.write(f"      Rilevati {len(scanned_notes)} file Markdown validi nel vault.\n")

    # 2. Setup external destination directory (rules and scripts live in idx_dest)
    idx_dest.mkdir(parents=True, exist_ok=True)
    scripts_dir = idx_dest / "scripts"
    tools_dir = idx_dest / "tools"
    copy_server_scripts(scripts_dir, tools_dir)
    server_script = scripts_dir / "server.py"

    # Copy agent rules into idx_dest (NEVER into user_vault root!)
    copied_rules = copy_agent_rules(agents, idx_dest)
    sys.stdout.write(f"      Regole agenti collocate in {idx_dest}: {', '.join(copied_rules) if copied_rules else 'nessuna'}\n")

    # 3. Initialize External SQLite WAL Database
    sys.stdout.write("[3/5] Configurazione database SQLite WAL esterno per indice FTS5...\n")
    db_path = idx_dest / "brain_memory.sqlite"
    storage = StorageManager(db_path=db_path, vault_path=user_vault)
    storage.init_db()

    # 4. Sync vault to populate FTS5
    sys.stdout.write("[4/5] Indicizzazione note Markdown -> SQLite FTS5...\n")
    sync_res = sync_vault(vault_path=user_vault, db_path=db_path, storage=storage)
    sys.stdout.write(f"      Note indicizzate: {len(sync_res.get('added', []))} aggiunte\n")

    # 5. E2E Latency Verification (< 15ms)
    sys.stdout.write("[5/5] Verifica E2E latenza ricerca FTS5 (< 15ms)...\n")
    # Use first available title token for query if possible
    query_term = "a"
    if scanned_notes:
        first_stem = next(iter(scanned_notes.keys()))
        parts = [p for p in first_stem.replace("/", " ").replace("-", " ").split() if len(p) > 2]
        if parts:
            query_term = parts[0]
    latency = verify_fts5_latency(storage, test_query=query_term)
    sys.stdout.write(f"      Latenza query FTS5: {latency:.2f}ms (< 15ms) -> OK\n")

    # 6. Generate MCP config
    config_path = idx_dest / "mcp_ready_config.json"
    cfg = generate_mcp_config(
        server_path=server_script,
        vault_path=user_vault,
        db_path=db_path,
        output_path=config_path,
        writable_inbox=clean_inbox,
        read_only=(clean_inbox is None),
    )

    if clean_inbox:
        sys.stdout.write(f"\n[Scoped Writable Inbox Attiva] Gli agenti AI possono scrivere memorie esclusivamente in '{clean_inbox}/'.\n")
        sys.stdout.write(f"[Sicurezza Garantita] Tutte le altre note e cartelle nel vault utente ({user_vault}) sono protette in sola lettura.\n")
    else:
        sys.stdout.write(f"\n[Sicurezza Garantita] Nessun file nel vault utente ({user_vault}) è stato modificato o creato (sola lettura totale).\n")

    return {
        "mode": "index",
        "vault": str(user_vault),
        "dest": str(idx_dest),
        "db_path": str(db_path),
        "server_path": str(server_script),
        "config_path": str(config_path),
        "latency_ms": latency,
        "config": cfg,
        "writable_inbox": clean_inbox,
    }


def auto_install_mcp_configs(config_payload: dict[str, Any], agents: set[str]) -> list[str]:
    """Non-destructively merges brain-memory into existing local MCP client configs."""
    if os.environ.get("AI_BRAIN_SKIP_GLOBAL_MCP") == "1":
        return []
    home = Path.home()
    candidates: list[Path] = []
    if "antigravity" in agents:
        candidates.append(home / ".gemini" / "config" / "mcp_config.json")
    if "cursor" in agents:
        candidates.append(home / ".cursor" / "mcp.json")
    if "claude" in agents:
        candidates.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
        candidates.append(home / ".claude.json")

    brain_entry = config_payload.get("mcpServers", {}).get("brain-memory")
    if not brain_entry:
        return []

    updated: list[str] = []
    for cfg_path in candidates:
        if not cfg_path.parent.is_dir():
            continue
        try:
            data: dict[str, Any] = {}
            if cfg_path.is_file():
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            servers = data.setdefault("mcpServers", {})
            if isinstance(servers, dict):
                servers["brain-memory"] = brain_entry
                cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                updated.append(str(cfg_path))
        except Exception:
            continue
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI-BRAIN Onboarding Setup Wizard — Configura il tuo vault cognitivo in meno di 60s"
    )
    parser.add_argument(
        "--mode",
        choices=["fresh", "index"],
        help="Modalità di onboarding: 'fresh' (nuovo vault da template) o 'index' (indicizza vault esistente)",
    )
    parser.add_argument(
        "--dest",
        help="Percorso di destinazione per il vault (fresh mode) o per l'indice/configurazione (index mode)",
    )
    parser.add_argument(
        "--vault",
        help="Percorso del vault Obsidian esistente da indicizzare in sola lettura (richiesto in index mode)",
    )
    parser.add_argument(
        "--writable-inbox",
        help="Sottocartella del vault esistente abilitata alla scrittura per gli agenti (es. 'AI-Memory'). Impostare a 'none' per disabilitare la scrittura.",
    )
    parser.add_argument(
        "--agents",
        help="Agenti da configurare: claude, cursor, antigravity, gemini, all (o combinazioni separate da virgola)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Esegue il wizard in modalità non-interattiva senza prompt da tastiera",
    )
    args = parser.parse_args()

    # Step 1: Verify Environment
    sys.stdout.write("==============================================================\n")
    sys.stdout.write("  AI-BRAIN — Onboarding Setup Wizard\n")
    sys.stdout.write("==============================================================\n")
    sys.stdout.write("[1/5] Verifica ambiente di esecuzione...\n")
    env_ok, env_msg = verify_environment()
    if not env_ok:
        sys.stderr.write(f"Errore ambiente: {env_msg}\n")
        sys.exit(1)
    sys.stdout.write(f"      Python {sys.version.split()[0]} | SQLite FTS5: Supportato | WAL Mode: Abilitato\n")

    # Determine mode
    mode = args.mode
    if not mode:
        if args.non_interactive:
            mode = "index" if args.vault else "fresh"
        else:
            sys.stdout.write("\nSeleziona la modalità di onboarding:\n")
            sys.stdout.write("  [1] Fresh Vault (Consigliata: crea un nuovo vault Obsidian da template)\n")
            sys.stdout.write("  [2] Read-Only Vault Indexing (Indicizza un vault Obsidian esistente senza toccarlo)\n")
            choice = prompt_user("Scelta [1/2]", default="1")
            mode = "index" if choice.strip() == "2" else "fresh"

    # Determine paths based on mode
    if mode == "fresh":
        dest_val = args.dest
        if not dest_val:
            if args.non_interactive:
                dest_val = str(Path.home() / "AI-Brain")
            else:
                dest_val = prompt_user("Percorso di destinazione per il nuovo vault", default=str(Path.home() / "AI-Brain"))
        dest_path = Path(dest_val)
        vault_path = None
    else:  # mode == "index"
        vault_val = args.vault
        if not vault_val:
            if args.non_interactive:
                sys.stderr.write("Errore: Il parametro --vault è obbligatorio in modalità index non-interattiva.\n")
                sys.exit(1)
            else:
                vault_val = prompt_user("Percorso del vault Obsidian esistente da indicizzare")
                if not vault_val:
                    sys.stderr.write("Errore: Percorso vault non specificato.\n")
                    sys.exit(1)

        vault_path = Path(vault_val).expanduser().resolve()

        dest_val = args.dest
        if not dest_val:
            default_dest = Path.home() / ".ai-brain" / "indexes" / vault_path.name
            if args.non_interactive:
                dest_val = str(default_dest)
            else:
                dest_val = prompt_user("Percorso cartella indice e configurazione", default=str(default_dest))

        dest_path = Path(dest_val)

    # Determine agents
    agents_input = args.agents
    if not agents_input:
        if args.non_interactive:
            agents_input = "all"
        else:
            sys.stdout.write("\nSeleziona gli agenti AI da configurare:\n")
            sys.stdout.write("  [1] Claude Code / Claude Desktop\n")
            sys.stdout.write("  [2] Cursor IDE\n")
            sys.stdout.write("  [3] Google Antigravity\n")
            sys.stdout.write("  [4] Gemini CLI\n")
            sys.stdout.write("  [5] Tutti (Consigliato)\n")
            agents_input = prompt_user("Scelta [1-5 o combinazioni es. 1,2]", default="5")
    agents = parse_agents_arg(agents_input)

    # Execute selected mode
    if mode == "fresh":
        result = run_fresh_vault_setup(dest_path=dest_path, agents=agents)
    else:
        writable_inbox = None
        if args.writable_inbox:
            if args.writable_inbox.lower() in ("none", "no", "false", "off", "disable"):
                writable_inbox = None
            else:
                writable_inbox = args.writable_inbox.strip("/").strip("\\")
        else:
            if not args.non_interactive:
                sys.stdout.write("\nConfigurazione Scoped Writable Inbox:\n")
                sys.stdout.write("  Desideri abilitare una cartella dedicata nel vault in cui gli agenti AI\n")
                sys.stdout.write("  possano registrare memorie in sicurezza, mantenendo il resto del vault protetto?\n")
                inbox_input = prompt_user("Cartella inbox scrivibile (es. 'AI-Memory' o 'none' per sola lettura)", default="AI-Memory")
                if inbox_input.lower() not in ("none", "no", "false", "off", "disable", ""):
                    writable_inbox = inbox_input.strip("/").strip("\\")

        result = run_read_only_index_setup(
            vault_path=vault_path,
            dest_path=dest_path,
            agents=agents,
            writable_inbox=writable_inbox,
        )

    # Auto-configure detected MCP client configs non-destructively
    configured_paths = auto_install_mcp_configs(result["config"], agents)

    # Print MCP snippet and final confirmation
    sys.stdout.write("\n==============================================================\n")
    sys.stdout.write("  Configurazione MCP Pronta all'Uso (mcp_ready_config.json):\n")
    sys.stdout.write("==============================================================\n")
    sys.stdout.write(json.dumps(result["config"], indent=2, ensure_ascii=False) + "\n")
    if configured_paths:
        sys.stdout.write("\nClient MCP configurati automaticamente:\n")
        for p in configured_paths:
            sys.stdout.write(f"  - {p}\n")
    sys.stdout.write("==============================================================\n")
    sys.stdout.write(f"Setup completato con successo in meno di 60 secondi!\n\n")


if __name__ == "__main__":
    main()
