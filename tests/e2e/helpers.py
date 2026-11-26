"""Opaque-box E2E test harness for ai-brain-oss.

Uses Python 3.9+ Standard Library only. Never imports internal modules from
`mcp-server/` or `tools/`; interacts strictly via STDIO JSON-RPC 2.0, CLI
subprocesses, filesystem inspection, and read-only SQLite URI queries.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_PY = REPO_ROOT / "mcp-server" / "server.py"
SETUP_PY = REPO_ROOT / "setup.py"
SYNC_DB_PY = REPO_ROOT / "tools" / "sync_db.py"
VAULT_SYNTHESIS_PY = REPO_ROOT / "tools" / "vault_synthesis.py"
BRAIN_INGEST_PY = REPO_ROOT / "tools" / "brain_ingest.py"

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def clean_python_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Builds a subprocess environment that suppresses Python bytecode emission."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def run_python_cli(
    script_path: Path,
    args: list[str] | None = None,
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Runs a Python CLI script with -B and PYTHONDONTWRITEBYTECODE=1."""
    cmd = [sys.executable, "-B", str(script_path)]
    if args:
        cmd.extend(args)
    return subprocess.run(
        cmd,
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=clean_python_env(env),
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
    )


def run_setup(
    args: list[str],
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Executes setup.py via subprocess."""
    return run_python_cli(SETUP_PY, args, input_text=input_text, env=env, timeout=timeout)


def run_sync_db(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Executes tools/sync_db.py via subprocess."""
    return run_python_cli(SYNC_DB_PY, args, env=env, timeout=timeout)


def run_vault_synthesis(
    args: list[str],
    *,
    script_path: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Executes tools/vault_synthesis.py (or installed copy) via subprocess."""
    target = script_path or VAULT_SYNTHESIS_PY
    return run_python_cli(target, args, env=env, timeout=timeout)


def make_fake_secrets() -> dict[str, str]:
    """Dynamically constructs synthetic fake secrets so static scanners never flag test source."""
    return {
        "openai": "sk-proj-" + "AbCdEf123456" * 3,
        "anthropic": "sk-ant-" + "XyZ987654321" * 3,
        "github": "ghp_" + "9AbCdEf012" * 4,
        "gitlab": "glpat-" + "aB1cD2eF3gH4" * 2,
        "slack": "xoxb-" + "1234567890" * 3,
        "google": "AIza" + "SyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q",
        "bearer": "Bearer " + "tok_secret_" * 3,
        "db_url": "postgresql://appuser:" + "fakepass_9876" + "@db.example.local:5432/app",
        "password": 'db_pass="' + "SuperSecretPass99!" + '"',
        "jwt": "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiJ0ZXN0In0." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    }


def is_stdlib_module(top_name: str) -> bool:
    """Python 3.9+ compatible check that a top-level import resolves to the Python stdlib."""
    if top_name in sys.builtin_module_names or top_name == "__future__":
        return True
    if hasattr(sys, "stdlib_module_names") and top_name in sys.stdlib_module_names:
        return True
    spec = importlib.util.find_spec(top_name)
    if spec is None:
        return False
    origin = spec.origin or ""
    if origin in ("built-in", "frozen"):
        return True
    if not origin:
        # Namespace package check
        return False
    stdlib_dir = Path(sysconfig.get_paths()["stdlib"]).resolve()
    origin_path = Path(origin).resolve()
    if "site-packages" in origin_path.parts or "dist-packages" in origin_path.parts:
        return False
    try:
        origin_path.relative_to(stdlib_dir)
        return True
    except ValueError:
        return False


def parse_md_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Pure-stdlib YAML frontmatter parser for test oracle inspection of vault Markdown files."""
    if not content.startswith("---"):
        return {}, content

    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n(.*))?$", content, re.DOTALL)
    if not match:
        return {}, content

    yaml_text = match.group(1)
    body_text = match.group(2) or ""
    metadata: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[Any] | None = None

    def _scalar(val: str) -> Any:
        v = val.strip()
        if not v:
            return ""
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            return v[1:-1]
        low = v.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low in ("null", "~"):
            return None
        try:
            if "." in v:
                return float(v)
            return int(v)
        except ValueError:
            return v

    for raw_line in yaml_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- ") and current_key is not None:
            if current_list is None:
                current_list = []
                metadata[current_key] = current_list
            current_list.append(_scalar(line[2:]))
            continue
        if ":" in line:
            key, val_str = line.split(":", 1)
            key = key.strip()
            val_str = val_str.strip()
            if val_str.startswith("[") and val_str.endswith("]"):
                inner = val_str[1:-1].strip()
                metadata[key] = [_scalar(item) for item in inner.split(",") if item.strip()] if inner else []
                current_key = None
                current_list = None
            elif not val_str:
                current_key = key
                current_list = []
                metadata[key] = current_list
            else:
                metadata[key] = _scalar(val_str)
                current_key = None
                current_list = None

    return metadata, body_text


def extract_wikilinks(text: str) -> list[str]:
    """Extracts Obsidian [[target]] link targets (preserving order and duplicates)."""
    return WIKILINK_RE.findall(text)


def snapshot_directory(directory: Path, *, exclude_prefixes: tuple[str, ...] = ()) -> dict[str, tuple[str, int, int]]:
    """Returns {rel_posix: (sha256, mtime_ns, size)} for all files under directory."""
    snapshot: dict[str, tuple[str, int, int]] = {}
    if not directory.exists():
        return snapshot
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        if any(rel == pref or rel.startswith(pref.rstrip("/") + "/") for pref in exclude_prefixes):
            continue
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot[rel] = (digest, stat.st_mtime_ns, stat.st_size)
    return snapshot


def query_db_readonly(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Executes a read-only SQLite query against db_path using URI mode=ro."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class McpStdioClient:
    """Opaque-box JSON-RPC 2.0 STDIO client driving mcp-server/server.py as a subprocess."""

    def __init__(
        self,
        *,
        server_py: Path | None = None,
        vault_path: Path | None = None,
        db_path: Path | None = None,
        writable_inbox: str | None = None,
        read_only: bool = False,
        env: dict[str, str] | None = None,
        extra_args: list[str] | None = None,
        default_timeout: float = 10.0,
    ) -> None:
        self.server_py = server_py or MCP_SERVER_PY
        self.default_timeout = default_timeout
        self._next_id = 1
        self._stdout_queue: queue.Queue[str | None] = queue.Queue()
        self._stderr_lines: list[str] = []

        cmd = [sys.executable, "-B", str(self.server_py)]
        if vault_path is not None:
            cmd.extend(["--vault", str(vault_path)])
        if db_path is not None:
            cmd.extend(["--db", str(db_path)])
        if writable_inbox is not None:
            cmd.extend(["--writable-inbox", writable_inbox])
        if read_only:
            cmd.append("--read-only")
        if extra_args:
            cmd.extend(extra_args)

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=clean_python_env(env),
        )

        self._stdout_thread = threading.Thread(target=self._pump_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    @classmethod
    def from_mcp_config(
        cls,
        config_path: Path,
        *,
        server_key: str = "ai-brain",
        default_timeout: float = 10.0,
    ) -> "McpStdioClient":
        """Spawns an MCP STDIO client using the exact args and env from mcp_ready_config.json."""
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        entry = cfg["mcpServers"][server_key]
        args = list(entry["args"])
        server_py = Path(args[0])
        extra_args = args[1:]
        env = dict(entry.get("env", {}))
        return cls(
            server_py=server_py,
            env=env,
            extra_args=extra_args,
            default_timeout=default_timeout,
        )

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self._stdout_queue.put(line)
        finally:
            self._stdout_queue.put(None)

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr_lines.append(line)

    def stderr_text(self) -> str:
        return "".join(self._stderr_lines)

    def _read_json_line(self, timeout: float | None = None) -> dict[str, Any]:
        eff_timeout = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + eff_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timed out waiting for MCP server response after {eff_timeout}s. "
                    f"Stderr: {self.stderr_text()}"
                )
            try:
                line = self._stdout_queue.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(
                    f"Timed out waiting for MCP server response after {eff_timeout}s. "
                    f"Stderr: {self.stderr_text()}"
                )
            if line is None:
                raise EOFError(f"MCP server closed stdout unexpectedly. Stderr: {self.stderr_text()}")
            stripped = line.strip()
            if stripped:
                return json.loads(stripped)

    def send_raw(self, raw_payload: str, *, expect_response: bool = True, timeout: float | None = None) -> dict[str, Any] | None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(raw_payload)
        self.proc.stdin.flush()
        if not expect_response:
            return None
        return self._read_json_line(timeout=timeout)

    def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        req_id: int | str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if req_id is None:
            req_id = self._next_id
            self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        resp = self.send_raw(json.dumps(payload, ensure_ascii=False) + "\n", expect_response=True, timeout=timeout)
        assert resp is not None
        return resp

    def send_content_length_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        req_id: int | str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if req_id is None:
            req_id = self._next_id
            self._next_id += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            },
            ensure_ascii=False,
        )
        framed = f"Content-Length: {len(body)}\r\n\r\n{body}"
        resp = self.send_raw(framed, expect_response=True, timeout=timeout)
        assert resp is not None
        return resp

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self.send_raw(json.dumps(payload, ensure_ascii=False) + "\n", expect_response=False)

    def initialize(self) -> dict[str, Any]:
        resp = self.send_request("initialize", {"protocolVersion": "2024-11-05"})
        self.send_notification("notifications/initialized", {})
        return resp

    def ping(self) -> dict[str, Any]:
        return self.send_request("ping", {})

    def list_tools(self) -> list[dict[str, Any]]:
        resp = self.send_request("tools/list", {})
        return resp["result"]["tools"]

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> tuple[bool, str, Any, dict[str, Any]]:
        """Calls an MCP tool and returns (is_error, text, parsed_json_or_none, raw_resp)."""
        resp = self.send_request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )
        result = resp.get("result", {})
        is_error = bool(result.get("isError", False))
        content_items = result.get("content", [])
        text = content_items[0].get("text", "") if content_items else ""
        parsed: Any = None
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
        return is_error, text, parsed, resp

    def close(self) -> None:
        """Cleanly shuts down the MCP STDIO subprocess with bounded wait and kill fallback."""
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass

        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2.0)

        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass
