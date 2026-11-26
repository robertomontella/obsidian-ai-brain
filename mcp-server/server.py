"""Model Context Protocol (MCP) STDIO JSON-RPC 2.0 Server for AI-BRAIN."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from deduplication import DeduplicationEngine
from storage import StorageManager, upsert_frontmatter_fields
try:
    from sync_db import sync_vault
except ImportError:
    sync_vault = None

SERVER_NAME = "ai-brain-mcp"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


def resolve_db_path(
    vault_path: str | Path | None = None,
    db_path: str | Path | None = None,
    read_only: bool = False,
    writable_inbox: str | None = None,
) -> Path:
    """Dynamically resolves the SQLite database path from parameters, environment,

    or filesystem conventions.
    """
    if db_path:
        return Path(db_path).resolve()

    env_db = os.environ.get("BRAIN_DB_PATH")
    if env_db:
        return Path(env_db).resolve()

    env_vault = vault_path or os.environ.get("BRAIN_VAULT_PATH")
    if env_vault:
        v_path = Path(env_vault).resolve()
        candidate1 = v_path / "90_System" / "db" / "brain_memory.sqlite"
        if candidate1.exists():
            return candidate1
        candidate2 = v_path / "brain_memory.sqlite"
        if candidate2.exists():
            return candidate2
        if read_only or writable_inbox:
            import tempfile
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", v_path.name) or "vault"
            return Path(tempfile.gettempdir()) / "ai-brain-indexes" / f"{safe_name}_{abs(hash(str(v_path)))}.sqlite"
        return candidate1

    # Local fallback candidates
    cwd = Path.cwd().resolve()
    candidates = [
        cwd / "vault-template" / "90_System" / "db" / "brain_memory.sqlite",
        cwd / "90_System" / "db" / "brain_memory.sqlite",
        cwd / "brain_memory.sqlite",
        Path.home() / "AI-BRAIN" / "90_System" / "db" / "brain_memory.sqlite",
        Path.home() / ".ai-brain" / "brain_memory.sqlite",
    ]
    for c in candidates:
        if c.exists():
            return c

    # Default location in current directory
    return cwd / "brain_memory.sqlite"


class McpServer:
    """Model Context Protocol (MCP) server managing memory tools via STDIO JSON-RPC."""

    def __init__(
        self,
        storage: StorageManager,
        vault_path: str | Path | None = None,
        writable_inbox: str | Path | None = None,
        read_only: bool = False,
    ) -> None:
        self.storage = storage
        self.vault_path = Path(vault_path).resolve() if vault_path else None
        self.writable_inbox = str(writable_inbox).strip("/").strip("\\") if writable_inbox else None
        self.read_only = bool(read_only)
        self.deduplication = DeduplicationEngine(storage=self.storage, writable_inbox=self.writable_inbox)

    def _format_markdown_note(self, record: dict[str, Any]) -> str:
        """Formats a memory record into standard Markdown with YAML frontmatter."""
        tags = record.get("tags")
        if isinstance(tags, str):
            try:
                tags_list = json.loads(tags)
            except Exception:
                tags_list = [tags]
        elif isinstance(tags, (list, tuple, set)):
            tags_list = list(tags)
        else:
            tags_list = []

        title = record.get("title", "")
        clean_title = re.sub(r'[\r\n"]+', " ", title).strip()

        lines = [
            "---",
            f'title: "{clean_title}"',
            f'type: {record.get("type", "memory/semantic")}',
            f'status: {record.get("status", "active")}',
            f'priority: {record.get("priority", "Media")}',
        ]
        if tags_list:
            lines.append("tags:")
            for t in tags_list:
                lines.append(f"  - {t}")
        if record.get("project"):
            clean_proj = re.sub(r'[\r\n"]+', " ", str(record["project"])).strip()
            lines.append(f'project: "{clean_proj}"')
        if record.get("summary"):
            clean_sum = re.sub(r'[\r\n"]+', " ", str(record["summary"])).strip()
            lines.append(f'summary: "{clean_sum}"')
        if record.get("supersedes"):
            lines.append(f'supersedes: "{record["supersedes"]}"')
        if record.get("superseded_by"):
            lines.append(f'superseded_by: "{record["superseded_by"]}"')
        lines.append(f'created_at: {record.get("created_at", "")}')
        lines.append(f'updated_at: {record.get("updated_at", "")}')
        if record.get("last_accessed"):
            lines.append(f'last_accessed: {record["last_accessed"]}')
        if record.get("archived_at"):
            lines.append(f'archived_at: {record["archived_at"]}')
        lines.append("---")
        lines.append("")
        lines.append(f"# {clean_title}")
        lines.append("")
        lines.append(record.get("content", ""))
        # Inject project-based wikilink only if a matching project note exists in 10_Projects/
        if record.get("project") and self.vault_path:
            proj_name = str(record["project"]).strip()
            proj_dir = self.vault_path / "10_Projects"
            matched_stem = None
            if proj_dir.is_dir():
                for p in proj_dir.glob("*.md"):
                    if p.stem.lower() == proj_name.lower():
                        matched_stem = p.stem
                        break
                    try:
                        head = p.read_text(encoding="utf-8", errors="replace")[:500]
                        m_title = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', head, re.MULTILINE)
                        if m_title and m_title.group(1).strip().lower() == proj_name.lower():
                            matched_stem = p.stem
                            break
                    except OSError:
                        pass
            if matched_stem and f"[[{matched_stem}]]" not in record.get("content", ""):
                lines.append("")
                lines.append(f"**Related:** [[{matched_stem}]]")
        lines.append("")
        return "\n".join(lines)

    def _resolve_scoped_path(
        self,
        requested_path: str | None,
        title: str,
        memory_type: str,
        content_hash: str,
    ) -> str:
        """Resolves and enforces relative memory note path.

        If writable_inbox is configured, guarantees that target path is strictly
        confined inside the designated writable subfolder.
        """
        type_folder_map = {
            "memory/episodic": "Episodic",
            "memory/semantic": "Semantic",
            "memory/procedural": "Procedural",
            "memory/working": "Working",
        }
        subfolder = type_folder_map.get(memory_type, "Semantic")

        clean_title = re.sub(r"[^\w\-\s]", "", title).strip()
        safe_slug = re.sub(r"[-\s]+", " ", clean_title)[:60]
        if not safe_slug:
            safe_slug = f"memory-{content_hash[:8]}"

        if self.writable_inbox:
            inbox = self.writable_inbox
            if requested_path:
                clean_p = requested_path.replace("\\", "/").strip("/")
                parts = [p for p in clean_p.split("/") if p and p != ".."]
                clean_p = "/".join(parts)
                if clean_p.startswith(f"{inbox}/"):
                    return clean_p
                if clean_p.startswith("50_Memory/"):
                    sub_p = clean_p[len("50_Memory/"):]
                    return f"{inbox}/{sub_p}"
                return f"{inbox}/{clean_p}"
            return f"{inbox}/{subfolder}/{safe_slug}.md"

        if requested_path:
            clean_p = requested_path.replace("\\", "/").strip("/")
            parts = [p for p in clean_p.split("/") if p and p != ".."]
            return "/".join(parts)
        return f"50_Memory/{subfolder}/{safe_slug}.md"

    def _is_writable_path(self, rel_path: str) -> bool:
        """Returns True iff rel_path is a safe, writable path inside the vault (and writable_inbox if set)."""
        if not self.vault_path or not rel_path:
            return False
        if self.read_only and not self.writable_inbox:
            return False

        clean_rel = str(rel_path).replace("\\", "/").strip("/")
        if not clean_rel:
            return False

        parts = clean_rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            return False

        vault_resolved = self.vault_path.resolve()
        target_file = (vault_resolved / clean_rel).resolve()
        try:
            target_file.relative_to(vault_resolved)
        except ValueError:
            return False

        if self.writable_inbox:
            inbox = self.writable_inbox
            if not clean_rel.startswith(f"{inbox}/"):
                return False
            inbox_resolved = (vault_resolved / inbox).resolve()
            try:
                target_file.relative_to(inbox_resolved)
            except ValueError:
                return False

        return True

    def _write_file_to_vault(self, record: dict[str, Any]) -> Path | None:
        """Safely writes a memory Markdown note to disk within the vault."""
        if not self.vault_path:
            return None
        if self.read_only and not self.writable_inbox:
            raise PermissionError("Access denied: Vault is configured in strict read-only mode without a writable inbox.")

        rel_path = record.get("path", "").replace("\\", "/").strip("/")
        if not rel_path:
            return None

        if self.writable_inbox:
            inbox = self.writable_inbox
            if not rel_path.startswith(f"{inbox}/") and rel_path != inbox:
                raise PermissionError(
                    f"Access denied: Path '{rel_path}' is outside designated writable inbox '{inbox}'."
                )

        target_file = (self.vault_path / rel_path).resolve()
        vault_resolved = self.vault_path.resolve()

        try:
            target_file.relative_to(vault_resolved)
        except ValueError:
            raise PermissionError(
                f"Security error: Target path '{target_file}' escapes vault root '{vault_resolved}'."
            )

        if self.writable_inbox:
            inbox_resolved = (self.vault_path / self.writable_inbox).resolve()
            try:
                target_file.relative_to(inbox_resolved)
            except ValueError:
                raise PermissionError(
                    f"Security error: Target path '{target_file}' escapes designated writable inbox '{inbox_resolved}'."
                )

        target_file.parent.mkdir(parents=True, exist_ok=True)
        md_text = self._format_markdown_note(record)
        target_file.write_text(md_text, encoding="utf-8")
        return target_file

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Returns the JSON schema definitions for supported MCP tools."""
        return [
            {
                "name": "brain_search_memory",
                "description": (
                    "Cerca nella memoria a lungo termine usando FTS5 BM25 per trovare "
                    "decisioni, architetture o fatti rilevanti."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Termini di ricerca full-text o semantica.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Numero massimo di memorie restituite (default: 5).",
                            "default": 5,
                        },
                        "memory_type": {
                            "type": "string",
                            "description": (
                                "Filtro facoltativo per tier cognitivo (memory/episodic, "
                                "memory/semantic, memory/procedural, memory/working)."
                            ),
                        },
                        "tag": {
                            "type": "string",
                            "description": "Filtro facoltativo per tag associato.",
                        },
                        "project": {
                            "type": "string",
                            "description": "Filtro facoltativo per progetto associato.",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "brain_auto_retrieve",
                "description": (
                    "Alias per brain_search_memory per auto-retrieval contestuale "
                    "usando prompt/query con ranking FTS5 BM25 + recency."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Prompt o contesto del task corrente.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Termini di ricerca full-text (alternativo a prompt).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Numero massimo di memorie restituite (default: 5).",
                            "default": 5,
                        },
                        "memory_type": {
                            "type": "string",
                            "description": "Filtro facoltativo per tier cognitivo.",
                        },
                        "tag": {
                            "type": "string",
                            "description": "Filtro facoltativo per tag associato.",
                        },
                        "project": {
                            "type": "string",
                            "description": "Filtro facoltativo per progetto associato.",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "brain_read_memory",
                "description": (
                    "Legge il contenuto completo e i metadati di una memoria dato il "
                    "suo percorso (path) nel vault o ID deterministico."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Percorso relativo nel vault o ID della memoria.",
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "brain_add_memory",
                "description": (
                    "Salva una nuova memoria nel vault con sanitizzazione anti-leak automatica "
                    "e indicizzazione FTS5 immediata."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Titolo descrittivo e sintetico della nota.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Contenuto Markdown della memoria.",
                        },
                        "memory_type": {
                            "type": "string",
                            "description": (
                                "Tier cognitivo: memory/episodic, memory/semantic, "
                                "memory/procedural, memory/working."
                            ),
                            "default": "memory/semantic",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista di tag associati.",
                            "default": [],
                        },
                        "priority": {
                            "type": "string",
                            "description": "Livello di priorità: Alta, Media, Bassa.",
                            "default": "Media",
                        },
                        "project": {
                            "type": "string",
                            "description": "Progetto di appartenenza o contesto di lavoro.",
                        },
                        "supersedes": {
                            "type": "string",
                            "description": "ID o titolo della memoria precedente superata o sostituita.",
                        },
                    },
                    "required": ["title", "content"],
                },
            },
            {
                "name": "brain_memory_stats",
                "description": (
                    "Restituisce metriche e statistiche aggregate sul database di memoria "
                    "(conteggio per layer, stato WAL, dimensione DB)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "brain_sync",
                "description": (
                    "Sincronizza bidirezionalmente i file Markdown del vault con l'indice SQLite FTS5 "
                    "(scansione ricorsiva, rilevamento modifiche via hash, rimozione note eliminate)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "vault_path": {
                            "type": "string",
                            "description": "Percorso opzionale della root del vault. Se omesso, viene risolto automaticamente.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Se true, calcola le modifiche senza applicarle al database.",
                            "default": False,
                        },
                    },
                },
            },
            {
                "name": "brain_auto_capture",
                "description": (
                    "Alias per brain_add_memory per salvataggio autonomo di decisioni o conoscenze "
                    "(accetta text o content e deriva il titolo se omesso)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Testo della conoscenza o decisione da salvare.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Contenuto Markdown (alternativo a text).",
                        },
                        "title": {
                            "type": "string",
                            "description": "Titolo opzionale (derivato dal testo se omesso).",
                        },
                        "memory_type": {
                            "type": "string",
                            "description": "Tier cognitivo (default: memory/semantic).",
                            "default": "memory/semantic",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista di tag associati.",
                            "default": [],
                        },
                        "priority": {
                            "type": "string",
                            "description": "Livello di priorità: Alta, Media, Bassa.",
                            "default": "Media",
                        },
                        "project": {
                            "type": "string",
                            "description": "Progetto di appartenenza.",
                        },
                        "supersedes": {
                            "type": "string",
                            "description": "ID o titolo della memoria precedente superata.",
                        },
                        "source_agent": {
                            "type": "string",
                            "description": "Agente sorgente (default: Antigravity).",
                            "default": "Antigravity",
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "brain_archive_memories",
                "description": (
                    "Archivia esplicitamente in 40_Archive/ le memorie inattive o indicate "
                    "(mai invocata implicitamente durante search/read/add/sync)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "older_than_days": {
                            "type": "integer",
                            "description": "Età minima in giorni dall'ultimo accesso (default: 90).",
                            "default": 90,
                        },
                        "max_access_count": {
                            "type": "integer",
                            "description": "Numero massimo di accessi consentito per l'archiviazione automatica per età (default: 0).",
                            "default": 0,
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista esplicita di percorsi relativi da archiviare.",
                        },
                        "ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista esplicita di ID memoria da archiviare.",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Se true, simula l'archiviazione senza spostare file o modificare il DB.",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            },
            {
                "name": "brain_crew_log_write",
                "description": (
                    "Scrive un entry di handoff nel Crew Logbook (50_Memory/Working/crew_log.md). "
                    "Registra agente, attività, file toccati e punti aperti per il prossimo agente. "
                    "Sanitizzazione zero-leak automatica."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "description": "Nome dell'agente (es. Cursor, Claude, Antigravity).",
                        },
                        "activity": {
                            "type": "string",
                            "description": "Descrizione dell'attività svolta.",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista di file toccati (percorsi relativi).",
                            "default": [],
                        },
                        "open_items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista di punti aperti per il prossimo agente.",
                            "default": [],
                        },
                        "project": {
                            "type": "string",
                            "description": "Progetto associato all'handoff.",
                        },
                    },
                    "required": ["agent", "activity"],
                },
            },
            {
                "name": "brain_log_handoff",
                "description": (
                    "Writes a handoff entry to the Crew Logbook (50_Memory/Working/crew_log.md). "
                    "Records agent, activity, files, and open items for the next agent. "
                    "Alias for brain_crew_log_write with English parameter names."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "description": "Name of the agent (e.g., Cursor, Claude, Antigravity).",
                        },
                        "activity": {
                            "type": "string",
                            "description": "Description of the activity performed.",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of files touched (relative paths).",
                            "default": [],
                        },
                        "open_items": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of open items for the next agent.",
                            "default": [],
                        },
                        "project": {
                            "type": "string",
                            "description": "Project associated with the handoff.",
                        },
                    },
                    "required": ["agent", "activity"],
                },
            },
            {
                "name": "brain_read_crew_log",
                "description": (
                    "Reads the most recent entries from the Crew Logbook (50_Memory/Working/crew_log.md). "
                    "Alias for brain_crew_log_read with English parameter names. "
                    "Returns entries in reverse chronological order (most recent first)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of entries to return (default: 10).",
                            "default": 10,
                        },
                    },
                },
            },
            {
                "name": "brain_crew_log_read",
                "description": (
                    "Legge gli entry più recenti dal Crew Logbook (50_Memory/Working/crew_log.md). "
                    "Restituisce entries in ordine cronologico inverso (più recenti prima)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Numero massimo di entry da restituire (default: 10).",
                            "default": 10,
                        },
                    },
                },
            },
            {
                "name": "brain_lint",
                "description": (
                    "Esegue un controllo di integrità read-only sul vault (wikilink rotti, "
                    "note orfane, frontmatter incompleto e disallineamenti superseded)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "vault_path": {
                            "type": "string",
                            "description": "Percorso opzionale della root del vault. Se omesso, usa quello configurato.",
                        },
                        "db_path": {
                            "type": "string",
                            "description": "Percorso opzionale del database SQLite.",
                        },
                    },
                },
            },
        ]

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Processes a single JSON-RPC 2.0 request or notification."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {}) or {}

        # Handle notifications (requests without id)
        if req_id is None:
            if method == "notifications/initialized":
                sys.stderr.write("[MCP] Client initialized successfully.\n")
                sys.stderr.flush()
            return None

        # Handle methods
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": self.get_tool_definitions(),
                },
            }

        if method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {}) or {}
            return self._dispatch_tool_call(req_id, tool_name, args)

        # Method not found
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32601,
                "message": f"Method '{method}' not found",
            },
        }

    def _dispatch_tool_call(self, req_id: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatches an MCP tool call and formats the text content response."""
        try:
            if tool_name in ("brain_search_memory", "brain_auto_retrieve"):
                query = args.get("query") if args.get("query") is not None else args.get("prompt", "")
                limit = int(args.get("limit", 5))
                memory_type = args.get("memory_type")
                tag = args.get("tag")
                project = args.get("project")
                results = self.storage.search_memory(
                    query=query,
                    limit=limit,
                    memory_type=memory_type,
                    tag=tag,
                    update_access=True,
                    project=project,
                )
                if args.get("include_superseded") is False:
                    results = [r for r in results if r.get("status") != "superseded"]

                if self.vault_path:
                    for rec in results:
                        rel_path = rec.get("path", "")
                        if rel_path.endswith(".md") and rec.get("last_accessed") and self._is_writable_path(rel_path):
                            upsert_frontmatter_fields(
                                (self.vault_path / rel_path).resolve(),
                                {"last_accessed": rec["last_accessed"]},
                            )

                formatted = json.dumps(results, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name == "brain_read_memory":
                path = args.get("path", "")
                record = self.storage.read_memory(path, update_access=True)
                if record is None:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Memory not found for path or ID: {path}",
                                }
                            ],
                            "isError": True,
                        },
                    }
                rel_path = record.get("path", "")
                if self.vault_path and rel_path.endswith(".md") and record.get("last_accessed") and self._is_writable_path(rel_path):
                    upsert_frontmatter_fields(
                        (self.vault_path / rel_path).resolve(),
                        {"last_accessed": record["last_accessed"]},
                    )
                formatted = json.dumps(record, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name in ("brain_add_memory", "brain_auto_capture"):
                if self.read_only and not self.writable_inbox:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Error: Vault is configured in strict read-only mode without a writable inbox. "
                                        "Write operations are disabled. Configure --writable-inbox (e.g., 'AI-Memory') "
                                        "to permit safe memory captures."
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    }
                content = args.get("content") if args.get("content") is not None else args.get("text", "")
                content = content if isinstance(content, str) else str(content)
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

                raw_title = (args.get("title") or "").strip()
                if not raw_title:
                    for line in content.splitlines():
                        stripped = re.sub(r"^#+\s*", "", line).strip()
                        if stripped:
                            raw_title = stripped[:60]
                            break
                    if not raw_title:
                        raw_title = f"Memory {content_hash[:8]}"
                title = raw_title

                memory_type = args.get("memory_type") or "memory/semantic"
                tags = args.get("tags") if args.get("tags") is not None else []
                priority = args.get("priority") or "Media"
                project = args.get("project")
                supersedes = args.get("supersedes")
                summary = args.get("summary")
                source_agent = args.get("source_agent") or "Antigravity"
                importance_score = float(args.get("importance_score", 3.0))
                req_path = args.get("path")

                scoped_path = self._resolve_scoped_path(
                    requested_path=req_path,
                    title=title,
                    memory_type=memory_type,
                    content_hash=content_hash,
                )

                record = self.deduplication.process_and_store(
                    title=title,
                    content=content,
                    memory_type=memory_type,
                    tags=tags,
                    path=scoped_path,
                    summary=summary,
                    priority=priority,
                    source_agent=source_agent,
                    project=project,
                    supersedes=supersedes,
                    importance_score=importance_score,
                )

                dedup_action = record.get("deduplication", {}).get("action")
                if self.vault_path and dedup_action != "reconfirmed":
                    try:
                        self.storage._inject_wikilinks(record)
                        self._write_file_to_vault(record)
                    except PermissionError as p_err:
                        return {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "content": [{"type": "text", "text": str(p_err)}],
                                "isError": True,
                            },
                        }

                formatted = json.dumps(record, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name == "brain_archive_memories":
                older_than_days = int(args.get("older_than_days", 90))
                max_access_count = int(args.get("max_access_count", 0))
                paths = args.get("paths")
                ids = args.get("ids")
                dry_run = bool(args.get("dry_run", False))

                if self.read_only and not self.writable_inbox and not dry_run:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Error: Vault is configured in strict read-only mode without a writable inbox. "
                                        "Explicit archiving is disabled unless dry_run=true."
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    }

                summary = self.storage.archive_memories(
                    older_than_days=older_than_days,
                    max_access_count=max_access_count,
                    paths=paths,
                    ids=ids,
                    dry_run=dry_run,
                    vault_path=self.vault_path,
                    writable_inbox=self.writable_inbox,
                    read_only=self.read_only,
                )
                formatted = json.dumps(summary, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name == "brain_memory_stats":
                stats = self.storage.get_stats()
                formatted = json.dumps(stats, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name == "brain_sync":
                if sync_vault is None:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": "Sync tool is unavailable."}],
                            "isError": True,
                        },
                    }
                vault_arg = args.get("vault_path")
                dry_run = bool(args.get("dry_run", False))
                target_vault = vault_arg or self.vault_path or os.environ.get("BRAIN_VAULT_PATH")
                if not target_vault:
                    db_file = self.storage.db_path
                    if "90_System" in db_file.parts:
                        idx = db_file.parts.index("90_System")
                        target_vault = Path(*db_file.parts[:idx])
                    else:
                        target_vault = db_file.parent

                summary = sync_vault(vault_path=target_vault, storage=self.storage, dry_run=dry_run)
                formatted = json.dumps(summary, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name == "brain_crew_log_write":
                if self.read_only and not self.writable_inbox:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Error: Vault is configured in strict read-only mode without a writable inbox. "
                                        "Write operations are disabled. Configure --writable-inbox (e.g., 'AI-Memory') "
                                        "to permit safe crew log writes."
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    }

                agent = str(args.get("agent") or "").strip()
                activity = str(args.get("activity") or "").strip()
                if not agent or not activity:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Error: 'agent' and 'activity' are required and cannot be empty.",
                                }
                            ],
                            "isError": True,
                        },
                    }
                files = args.get("files") if args.get("files") is not None else []
                open_items = args.get("open_items") if args.get("open_items") is not None else []
                project = args.get("project")

                result = self.storage.write_crew_log(
                    agent=agent,
                    activity=activity,
                    files=files,
                    open_items=open_items,
                    project=project,
                    writable_inbox=self.writable_inbox,
                )
                formatted = json.dumps(result, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            # Alias handlers - same implementation as brain_crew_log_write/read
            if tool_name == "brain_log_handoff":
                if self.read_only and not self.writable_inbox:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Error: Vault is configured in strict read-only mode without a writable inbox. "
                                        "Write operations are disabled. Configure --writable-inbox (e.g., 'AI-Memory') "
                                        "to permit safe crew log writes."
                                    ),
                                }
                            ],
                            "isError": True,
                        },
                    }

                agent = str(args.get("agent") or "").strip()
                activity = str(args.get("activity") or "").strip()
                if not agent or not activity:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Error: 'agent' and 'activity' are required and cannot be empty.",
                                }
                            ],
                            "isError": True,
                        },
                    }
                files = args.get("files") if args.get("files") is not None else []
                open_items = args.get("open_items") if args.get("open_items") is not None else []
                project = args.get("project")

                result = self.storage.write_crew_log(
                    agent=agent,
                    activity=activity,
                    files=files,
                    open_items=open_items,
                    project=project,
                    writable_inbox=self.writable_inbox,
                )
                formatted = json.dumps(result, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name in ("brain_read_crew_log", "brain_crew_log_read"):
                limit = args.get("limit")
                if limit is not None:
                    limit = int(limit)
                result = self.storage.read_crew_log(limit=limit, writable_inbox=self.writable_inbox)
                if limit is not None and isinstance(result, dict) and result.get("entries"):
                    result = {k: v for k, v in result.items() if k != "content"}
                formatted = json.dumps(result, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            if tool_name == "brain_lint":
                target_vault_raw = args.get("vault_path")
                target_vault = Path(target_vault_raw).expanduser().resolve() if target_vault_raw else self.vault_path
                if not target_vault:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": "Error: 'vault_path' is required when server is started without --vault."}],
                            "isError": True,
                        },
                    }
                target_db_raw = args.get("db_path")
                target_db = Path(target_db_raw).expanduser().resolve() if target_db_raw else self.storage.db_path
                tools_dir = Path(__file__).resolve().parent.parent / "tools"
                if not tools_dir.exists():
                    tools_dir = Path(__file__).resolve().parent / "tools"
                if str(tools_dir) not in sys.path:
                    sys.path.insert(0, str(tools_dir))
                from lint_engine import lint_vault

                report = lint_vault(vault_path=target_vault, db_path=target_db)
                formatted = json.dumps(report, indent=2, ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": formatted}],
                        "isError": False,
                    },
                }

            # Tool not recognized
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: '{tool_name}'"}],
                    "isError": True,
                },
            }

        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error executing {tool_name}: {exc}"}],
                    "isError": True,
                },
            }

    def run_stdio(self) -> None:
        """Main STDIO loop for handling incoming JSON-RPC lines."""
        sys.stderr.write(f"[MCP] {SERVER_NAME} v{SERVER_VERSION} listening on STDIO...\n")
        sys.stderr.flush()

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                line_str = line.strip()
                if not line_str:
                    continue

                # Handle optional Content-Length header framing
                if line_str.startswith("Content-Length:"):
                    parts = line_str.split(":", 1)
                    content_length = int(parts[1].strip())
                    # Consume subsequent empty lines
                    while True:
                        separator = sys.stdin.readline()
                        if separator in ("\r\n", "\n", ""):
                            break
                    body = sys.stdin.read(content_length)
                    req_data = json.loads(body)
                else:
                    req_data = json.loads(line_str)

                response = self.handle_request(req_data)
                if response is not None:
                    out = json.dumps(response, ensure_ascii=False) + "\n"
                    sys.stdout.write(out)
                    sys.stdout.flush()

            except json.JSONDecodeError as err:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {err}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
            except KeyboardInterrupt:
                break
            except Exception as err:
                sys.stderr.write(f"[MCP Error] {err}\n")
                sys.stderr.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI-BRAIN Model Context Protocol STDIO Server")
    parser.add_argument("--vault", help="Path to Obsidian vault root directory")
    parser.add_argument("--db", help="Direct path to SQLite database file")
    parser.add_argument(
        "--writable-inbox",
        help="Designated writable subfolder in vault (e.g. AI-Memory) for agent memory notes",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Enforce strict read-only mode (all notes outside writable inbox are strictly protected)",
    )
    args = parser.parse_args()

    writable_inbox = args.writable_inbox or os.environ.get("BRAIN_WRITABLE_INBOX")
    read_only = args.read_only or (os.environ.get("BRAIN_READ_ONLY", "").lower() in ("1", "true", "yes"))

    db_path = resolve_db_path(
        vault_path=args.vault,
        db_path=args.db,
        read_only=read_only,
        writable_inbox=writable_inbox,
    )
    storage = StorageManager(db_path=db_path, vault_path=args.vault)
    storage.init_db()

    server = McpServer(
        storage=storage,
        vault_path=args.vault,
        writable_inbox=writable_inbox,
        read_only=read_only,
    )
    server.run_stdio()


if __name__ == "__main__":
    main()
