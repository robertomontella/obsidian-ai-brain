# Claude Code & Claude Desktop — AI-BRAIN Memory Integration Guidelines

> **Target:** Claude Code CLI & Claude Desktop  
> **MCP Server:** `ai-brain` (Model Context Protocol STDIO)  
> **Vault Root:** Configured via `BRAIN_VAULT_PATH` or `--vault`

---

## 1. Silent Auto-Retrieval Protocol (Always On)

At the beginning of **every task, session, or prompt**:
- You **MUST** query long-term memory via MCP tool `brain_search_memory` before planning or generating code.
- Retrieve architectural context, past design decisions, user preferences, and project guidelines relevant to the query.
- **Zero Narration / Silent Operation:** Execute the retrieval silently in the background. Do **NOT** output verbose commentary such as "Searching AI-BRAIN memory..." or "Let me check the vault...".
- Integrate retrieved memories seamlessly into your reasoning, code generation, and responses. If no relevant memories exist, proceed normally without interrupting the user.

```json
// Example MCP Call:
{
  "name": "brain_search_memory",
  "arguments": {
    "query": "<task topic or architectural concept>",
    "limit": 5
  }
}
```

---

## 2. Autonomous Auto-Capture Protocol (Zero User Friction)

Whenever you:
1. Make or adopt an architectural or technical decision (e.g. library choice, pattern, interface design).
2. Establish or discover project conventions or user preferences (e.g. formatting, workflows, testing style).
3. Solve a non-trivial bug, edge case, or performance issue with a reusable fix.
4. Define or update an operational procedure, runbook, or recurring workflow.

You **MUST** autonomously save this knowledge to the vault:
- Call `brain_add_memory` immediately and autonomously.
- **Zero User Friction:** NEVER ask the user: "Would you like me to remember this?" or "Should I save this to memory?". Capture it directly.
- **Automatic Deduplication & Updates:** When updating or replacing an existing decision, populate the `supersedes` parameter with the title or ID of the superseded memory. The backend automatically marks previous notes as superseded and registers the replacement.

```json
// Example MCP Call:
{
  "name": "brain_add_memory",
  "arguments": {
    "title": "TaskFlow SQLite Concurrency Strategy",
    "content": "Description of decision, rationale, and implementation details.",
    "memory_type": "memory/semantic",
    "tags": ["architecture", "sqlite", "concurrency"],
    "priority": "Alta",
    "project": "TaskFlow"
  }
}
```

---

## 3. Zero-Leak Security Policy (Strict Sanitization)

Security and credential confidentiality are mandatory and absolute:
- **Zero-Leak Enforcement:** NEVER capture or persist secrets, API keys, private tokens, passwords, or authentication credentials.
- Specifically prohibit persisting patterns matching:
  - OpenAI / Anthropic / AI API keys (`sk-...`)
  - GitHub Personal Access Tokens (`ghp_...`, `gho_...`)
  - Authorization headers (`Bearer ...`)
  - Passwords, secret keys, or private certificates
- When capturing memory from logs, diffs, or environment files, redact credentials before calling `brain_add_memory`. The MCP backend also runs a deterministic anti-leak sanitizer as a secondary safety guardrail.

---

## 4. Crew Handoff Protocol

Use `brain_log_handoff` to record context when transitioning work to another agent or session. This tool writes structured handoff entries to `crew_log.md` with agent identity, activity summary, file references, and open items.

For multi-agent workflows with continuity:
- Use `brain_log_handoff` to record context when transitioning work to another agent or session. This tool writes structured handoff entries to `crew_log.md` with agent identity, activity summary, file references, and open items.
- Use `brain_read_crew_log` to retrieve recent handoff history for context continuity. The crew log preserves chronological context across agent boundaries.
- Use `brain_archive_memories` to explicitly move cold or completed notes to `40_Archive/` with proper metadata (status: archived, archived_at timestamp).

```json
// Example brain_log_handoff call:
{
  "name": "brain_log_handoff",
  "arguments": {
    "agent": "Claude",
    "activity": "Implemented webhook signature verification",
    "files": ["src/webhooks.py", "TaskFlow Webhook Signature Verification.md"],
    "open_items": ["Add replay window timestamp check"],
    "project": "TaskFlow"
  }
}
```

### Crew Handoff Tools Reference

| Tool | Purpose | Key Parameters |
|------|---------|----------------|
| `brain_log_handoff` | Record handoff context to `crew_log.md` | `agent`, `activity`, `files`, `open_items`, `project` |
| `brain_read_crew_log` | Read recent handoff history | `limit`, `project` |
| `brain_archive_memories` | Move cold memories to archive | `older_than_days`, `max_access_count`, `dry_run` |

---

## 5. Vault Structure Guidance (PARA + 4 Cognitive Layers)

The AI-BRAIN vault is structured according to the PARA method complemented by 4 cognitive memory layers:

### PARA Framework
- `00_Inbox`: Temporary landing zone for raw ideas, transitory notes, code snippets, and unclassified items.
- `10_Projects`: Active initiatives with defined scopes and deadlines (e.g., active deliverables, release milestones).
- `20_Areas`: Long-term domains of responsibility maintained continuously (e.g., `Backend Architecture`, `DevOps`, `Security Standards`).
- `30_Resources`: Topics of interest, reference libraries, handbooks, cheat sheets, and reusable guidelines (e.g., `Coding Guidelines`).
- `40_Archive`: Completed, inactive, or historical notes preserved for retrospective consultation.

### 4 Cognitive Memory Layers (`50_Memory/`)
All durable agent memories reside under `50_Memory/`, organized by cognitive tier:
- `50_Memory/Episodic`: Historical log of dated architectural decisions, design choices, sprint retrospectives, and incident post-mortems (format: `YYYY-MM-DD <Title>.md`). Set `memory_type: "memory/episodic"`.
- `50_Memory/Semantic`: Core architectural principles, domain entities, design invariants, and long-term knowledge graphs. Set `memory_type: "memory/semantic"`.
- `50_Memory/Procedural`: Standard Operating Procedures (SOPs), repeatable runbooks, troubleshooting steps, and step-by-step guides. Set `memory_type: "memory/procedural"`.
- `50_Memory/Working`: Active sprint context, scratchpads, work-in-progress state, and task ledgers. Set `memory_type: "memory/working"`.

---

## 6. Claude-Specific Execution Best Practices

- **Concise Outputs:** Deliver direct, high-signal technical solutions without unnecessary conversational filler.
- **Git Safety:** Respect local branch boundaries. Do not execute destructive git commands (`push --force`, branch deletion) unless explicitly commanded by the user.
- **Context Priming:** Use retrieved memories to avoid re-asking questions already answered in past sessions.