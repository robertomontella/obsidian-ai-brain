# Google Antigravity — Global Autonomous Memory Protocol (AGENTS.md)

> **Target:** Google Antigravity Agent Runtime & Subagents  
> **MCP Server:** `ai-brain` (Model Context Protocol STDIO)  
> **Vault Root:** Configured in `~/.gemini/config/mcp_config.json` via `BRAIN_VAULT_PATH` or `--vault`

---

## 1. Silent Auto-Retrieval Protocol (Always On)

At the start of **ANY** task, user query, or session across all workspaces:
- You **MUST** automatically and silently query AI-BRAIN memory before planning or responding.
- Call MCP tool `brain_search_memory` (or `brain_auto_retrieve` if aliased) with the user task or architectural topic.
- **Zero Narration / Silent Operation:** Do **NOT** output verbose narration like "Searching AI-BRAIN memory...". Execute the recall in the background.
- If relevant past decisions, preferences, architectures, or solutions are returned, seamlessly use them in your thinking, recommendations, and execution.
- If no relevant memories exist, continue normally without interruption.

```json
// Example MCP Tool Call:
{
  "name": "brain_search_memory",
  "arguments": {
    "query": "<user task / topic>",
    "limit": 5
  }
}
```

---

## 2. Autonomous Auto-Capture Protocol (Zero User Friction)

Whenever you:
1. Make or adopt an architectural or technical decision (e.g. tools, libraries, design patterns, file formats).
2. Discover or apply user preferences (e.g. style, constraints, languages, workflows).
3. Create, update, or advance project status/milestones.
4. Solve a non-trivial bug, error, or performance issue with a reusable fix.
5. Define a procedure, SOP, runbook, or repeatable commands.

You **MUST** autonomously capture or update the knowledge in AI-BRAIN:
- Call MCP tool `brain_add_memory` (or `brain_auto_capture` if aliased).
- **Zero User Friction:** NEVER ask the user: "Would you like me to remember this information?" or "Can I save this decision?". Capture it directly.
- **Deduplication & Resolution:** When a decision supersedes a previous note, supply the superseded title/ID in `supersedes`. The backend automatically marks older decisions superseded and registers the active one.

```json
// Example MCP Tool Call:
{
  "name": "brain_add_memory",
  "arguments": {
    "title": "TaskFlow State Machine Design",
    "content": "Rationale and state transition invariants for TaskFlow tasks.",
    "memory_type": "memory/semantic",
    "tags": ["state-machine", "architecture", "taskflow"],
    "priority": "Alta",
    "project": "TaskFlow"
  }
}
```

---

## 3. Zero-Leak Security Policy (Strict Sanitization)

Confidentiality and credentials protection are non-negotiable:
- **Zero-Leak Security:** NEVER capture secrets, passwords, Bearer tokens, or API keys (`sk-...`, `ghp_...`, `Bearer ...`).
- If inspecting environment files, connection strings, or system logs, sanitize and redact credentials before calling memory tools.
- The MCP server includes an automated regex sanitizer providing defense-in-depth against accidental leakage.

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
- `00_Inbox`: Unprocessed notes, quick captures, code snippets, and incoming tasks.
- `10_Projects`: Active projects with defined deadlines and scopes.
- `20_Areas`: Ongoing domains of responsibility (e.g., `Backend Architecture`, `Infrastructure`, `Security Standards`).
- `30_Resources`: Reusable references, handbooks, cheat sheets, and guidelines (e.g., `Coding Guidelines`).
- `40_Archive`: Inactive, completed, or superseded material preserved for audit and historical context.

### 4 Cognitive Memory Layers (`50_Memory/`)
All durable agent memories are categorized under `50_Memory/`:
- `50_Memory/Episodic`: Historical log of dated decisions, post-mortems, and sprint retrospectives (`YYYY-MM-DD <Title>.md`). (`memory_type: "memory/episodic"`).
- `50_Memory/Semantic`: Foundational facts, architectural models, domain entities, and enduring concepts. (`memory_type: "memory/semantic"`).
- `50_Memory/Procedural`: Step-by-step Standard Operating Procedures (SOPs), repeatable runbooks, and validation checklists. (`memory_type: "memory/procedural"`).
- `50_Memory/Working`: Active sprint state, work-in-progress notes, scratchpads, and immediate task context. (`memory_type: "memory/working"`).

---

## 6. Strict Git & Execution Safety Policy

- **No Autonomous Commit or Push:** In any chat, session, or workspace, NEVER run `git commit` or `git push` autonomously on remote repositories without explicit user instruction.
- **Evidence Before Assertion:** Always verify changes and run tests locally before claiming completion.