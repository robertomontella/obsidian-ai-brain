# Gemini CLI & Gemini Code Assist — AI-BRAIN Memory Integration Protocol

> **Target:** Gemini CLI, Gemini Code Assist, and Google AI Studio Workflows  
> **MCP Server:** `ai-brain` (Model Context Protocol STDIO)  
> **Vault Root:** Configured via `BRAIN_VAULT_PATH` or `--vault`

---

## 1. Silent Auto-Retrieval Protocol (Always On)

At the start of **every CLI interaction, query, or script execution**:
- You **MUST** query long-term memory via MCP tool `brain_search_memory` before generating output or proposing changes.
- Retrieve architectural context, style guidelines, project decisions, and relevant domain concepts.
- **Silent Operation / Zero Narration:** Perform memory retrieval silently in the background. Do **NOT** print verbose commentary like "Querying AI-BRAIN memory..." to standard output.
- Integrate retrieved knowledge seamlessly into your reasoning and answers. If no relevant memory exists, proceed normally without interrupting the user.

```json
// Example MCP Tool Call:
{
  "name": "brain_search_memory",
  "arguments": {
    "query": "<topic or prompt concept>",
    "limit": 5
  }
}
```

---

## 2. Autonomous Auto-Capture Protocol (Zero User Friction)

Whenever you:
1. Make or adopt an architectural or technical decision (e.g. CLI tools, protocols, schemas, patterns).
2. Establish or identify project conventions, user constraints, or workflows.
3. Solve a non-trivial bug, edge case, or compiler/interpreter error with a reusable fix.
4. Define a repeatable script, SOP, runbook, or CLI workflow.

You **MUST** autonomously save this knowledge to the vault:
- Call `brain_add_memory` immediately without user friction.
- **Zero User Friction:** NEVER ask the user: "Would you like me to remember this?" or "Should I save this decision?". Capture it directly.
- **Deduplication & Superseding:** When replacing an outdated guideline or prior decision, include the superseded note title or ID in `supersedes`. The backend automatically marks the old memory superseded and activates the new one.

```json
// Example MCP Tool Call:
{
  "name": "brain_add_memory",
  "arguments": {
    "title": "TaskFlow CLI Command Interface Specification",
    "content": "Design specification for TaskFlow subcommands and argument parsing.",
    "memory_type": "memory/semantic",
    "tags": ["cli", "argparse", "specification"],
    "priority": "Alta",
    "project": "TaskFlow"
  }
}
```

---

## 3. Zero-Leak Security Policy (Strict Sanitization)

Strict protection of credentials and sensitive information is enforced:
- **Zero-Leak Security:** NEVER capture secrets, passwords, Bearer tokens, or API keys (`sk-...`, `ghp_...`, `Bearer ...`).
- When parsing environment files, shell histories, or error dumps, redact sensitive tokens before invoking memory capture.
- The MCP server enforces deterministic regex sanitization on all memory writes as an autonomous guardrail.

---

## 4. Crew Handoff Protocol

For multi-agent workflows with continuity:
- Use `brain_log_handoff` to record context when transitioning work to another agent or session. This tool writes structured handoff entries to `crew_log.md` with agent identity, activity summary, file references, and open items.
- Use `brain_read_crew_log` to retrieve recent handoff history for context continuity. The crew log preserves chronological context across agent boundaries.
- Use `brain_archive_memories` to explicitly move cold or completed notes to `40_Archive/` with proper metadata (status: archived, archived_at timestamp).

---

## 5. Vault Structure Guidance (PARA + 4 Cognitive Layers)

The AI-BRAIN vault is organized using the PARA structure combined with 4 cognitive layers:

### PARA Framework
- `00_Inbox`: Quick captures, raw ideas, unclassified code snippets, and transitory notes.
- `10_Projects`: Active projects with defined deadlines, deliverables, and tasks.
- `20_Areas`: Long-term areas of responsibility (e.g., `Backend Architecture`, `Infrastructure`, `Security Standards`).
- `30_Resources`: Reusable reference materials, handbooks, cheat sheets, and guidelines (e.g., `Coding Guidelines`).
- `40_Archive`: Completed, inactive, or historical notes preserved for retrospective consultation.

### 4 Cognitive Memory Layers (`50_Memory/`)
All durable agent memories are categorized under `50_Memory/`:
- `50_Memory/Episodic`: Chronological records of decisions, post-mortems, and sprint retrospectives (`YYYY-MM-DD <Title>.md`). (`memory_type: "memory/episodic"`).
- `50_Memory/Semantic`: Foundational facts, architectural models, domain entities, and enduring concepts. (`memory_type: "memory/semantic"`).
- `50_Memory/Procedural`: Step-by-step Standard Operating Procedures (SOPs), repeatable runbooks, and validation checklists. (`memory_type: "memory/procedural"`).
- `50_Memory/Working`: Active sprint state, work-in-progress notes, scratchpads, and immediate task context. (`memory_type: "memory/working"`).

---

## 6. Gemini CLI Directives

- **Clean Terminal Output:** Keep command line feedback concise, accurate, and actionable.
- **Verification First:** Validate code and configurations before concluding instructions.
- **Git Safety:** Do not execute autonomous git commit or push commands without explicit user instruction.