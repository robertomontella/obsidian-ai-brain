import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mcp-server"))

from server import McpServer
from storage import StorageManager


class TestMcpServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "brain_memory.sqlite"
        self.storage = StorageManager(db_path=self.db_path)
        self.storage.init_db()
        self.server = McpServer(storage=self.storage)

    def tearDown(self):
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    def test_initialize_handshake(self):
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}
        }
        res = self.server.handle_request(req)
        self.assertEqual(res["jsonrpc"], "2.0")
        self.assertEqual(res["id"], 1)
        self.assertIn("capabilities", res["result"])
        self.assertIn("serverInfo", res["result"])

    def test_tools_list(self):
        req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        res = self.server.handle_request(req)
        tools = res["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("brain_search_memory", tool_names)
        self.assertIn("brain_read_memory", tool_names)
        self.assertIn("brain_add_memory", tool_names)
        self.assertIn("brain_memory_stats", tool_names)

    def test_tools_call_add_and_search_and_read(self):
        # 1. Add memory
        add_req = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "brain_add_memory",
                "arguments": {
                    "title": "Protocol MCP",
                    "content": "MCP connects LLMs with external tools over STDIO.",
                    "memory_type": "memory/semantic",
                    "tags": ["mcp", "protocol"],
                    "priority": "Alta",
                    "project": "TaskFlow"
                }
            }
        }
        add_res = self.server.handle_request(add_req)
        self.assertFalse(add_res["result"].get("isError", False))
        content_text = add_res["result"]["content"][0]["text"]
        self.assertIn("Protocol MCP", content_text)
        add_data = json.loads(content_text)
        self.assertEqual(add_data.get("priority"), "Alta")
        self.assertEqual(add_data.get("project"), "TaskFlow")

        # 2. Search memory
        search_req = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "brain_search_memory",
                "arguments": {
                    "query": "connects LLMs external tools",
                    "limit": 5
                }
            }
        }
        search_res = self.server.handle_request(search_req)
        search_text = search_res["result"]["content"][0]["text"]
        self.assertIn("Protocol MCP", search_text)

        # 3. Read memory
        read_req = {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "brain_read_memory",
                "arguments": {
                    "path": add_data["path"]
                }
            }
        }
        read_res = self.server.handle_request(read_req)
        self.assertFalse(read_res["result"].get("isError", False))
        self.assertIn("Protocol MCP", read_res["result"]["content"][0]["text"])

        # 4. Memory stats
        stats_req = {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {
                "name": "brain_memory_stats",
                "arguments": {}
            }
        }
        stats_res = self.server.handle_request(stats_req)
        self.assertIn("total_memories", stats_res["result"]["content"][0]["text"])

    def test_tools_call_brain_sync(self):
        vault_dir = Path(self.temp_dir.name) / "vault_sync_test"
        vault_dir.mkdir(parents=True, exist_ok=True)
        note_file = vault_dir / "TestNote.md"
        note_file.write_text("# Test Note\nContenuto di sincronizzazione.", encoding="utf-8")

        sync_req = {
            "jsonrpc": "2.0",
            "id": 20,
            "method": "tools/call",
            "params": {
                "name": "brain_sync",
                "arguments": {
                    "vault_path": str(vault_dir)
                }
            }
        }
        res = self.server.handle_request(sync_req)
        self.assertFalse(res["result"].get("isError", False))
        summary = json.loads(res["result"]["content"][0]["text"])
        self.assertEqual(summary["total_scanned"], 1)
        self.assertEqual(len(summary["added"]), 1)

    def test_deduplication_via_mcp_exact_and_contradiction(self):
        # 1. Add base memory
        req1 = {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "brain_add_memory",
                "arguments": {
                    "title": "Auth Architecture",
                    "content": "Using session cookies for state management."
                }
            }
        }
        res1 = self.server.handle_request(req1)
        data1 = json.loads(res1["result"]["content"][0]["text"])
        self.assertEqual(data1["deduplication"]["level"], "NEW_MEMORY")

        # 2. Add exact duplicate
        res2 = self.server.handle_request(req1)
        data2 = json.loads(res2["result"]["content"][0]["text"])
        self.assertEqual(data2["deduplication"]["level"], "EXACT_DUPLICATE")
        self.assertEqual(data2["id"], data1["id"])

        # 3. Add contradiction
        req3 = {
            "jsonrpc": "2.0",
            "id": 22,
            "method": "tools/call",
            "params": {
                "name": "brain_add_memory",
                "arguments": {
                    "title": "Switch Auth to JWT",
                    "content": "Decisione ribaltata: sostituito session cookies con JWT stateless token invece di cookies."
                }
            }
        }
        res3 = self.server.handle_request(req3)
        data3 = json.loads(res3["result"]["content"][0]["text"])
        self.assertEqual(data3["deduplication"]["level"], "CONTRADICTION")
        self.assertEqual(data3["status"], "active")
        self.assertEqual(data3["supersedes"], data1["id"])

    def test_unknown_method_returns_error(self):
        req = {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "unknown/method",
            "params": {}
        }
        res = self.server.handle_request(req)
        self.assertIn("error", res)
        self.assertEqual(res["error"]["code"], -32601)

    def test_stdio_subprocess_pipe_handshake(self):
        import subprocess
        server_py = REPO_ROOT / "mcp-server" / "server.py"
        test_db = Path(self.temp_dir.name) / "subprocess_brain.sqlite"
        proc = subprocess.Popen(
            [sys.executable, str(server_py), "--db", str(test_db)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        init_req = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}
        }) + "\n"
        proc.stdin.write(init_req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline()
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()
        proc.terminate()
        proc.wait(timeout=2.0)

        self.assertTrue(resp_line)
        resp_obj = json.loads(resp_line)
        self.assertEqual(resp_obj["id"], 1)
        self.assertEqual(resp_obj["result"]["serverInfo"]["name"], "ai-brain-mcp")

    def test_scoped_writable_inbox_writes_markdown_note(self):
        """Verifies that with a scoped writable inbox, notes are safely written to disk inside the inbox."""
        vault_dir = Path(self.temp_dir.name) / "existing_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "Notes").mkdir(parents=True, exist_ok=True)
        existing_note = vault_dir / "Notes" / "Existing.md"
        existing_note.write_text("# Existing User Note\nDo not touch.", encoding="utf-8")
        existing_mtime_before = existing_note.stat().st_mtime

        server = McpServer(
            storage=self.storage,
            vault_path=vault_dir,
            writable_inbox="AI-Memory",
        )

        req = {
            "jsonrpc": "2.0",
            "id": 50,
            "method": "tools/call",
            "params": {
                "name": "brain_add_memory",
                "arguments": {
                    "title": "Scoped Architecture Note",
                    "content": "Storing decision safely in scoped inbox.",
                    "memory_type": "memory/semantic",
                    "tags": ["architecture", "inbox"],
                    "priority": "Alta",
                    "project": "TaskFlow",
                },
            },
        }
        res = server.handle_request(req)
        self.assertFalse(res["result"].get("isError", False))
        data = json.loads(res["result"]["content"][0]["text"])

        # Check path starts with AI-Memory
        self.assertTrue(data["path"].startswith("AI-Memory/Semantic/"))

        # Check physical file exists on disk inside AI-Memory
        written_file = vault_dir / data["path"]
        self.assertTrue(written_file.is_file())
        file_content = written_file.read_text(encoding="utf-8")
        self.assertIn('title: "Scoped Architecture Note"', file_content)
        self.assertIn("Storing decision safely in scoped inbox.", file_content)
        self.assertIn("type: memory/semantic", file_content)

        # Verify existing user note is 100% untouched
        self.assertEqual(existing_note.read_text(encoding="utf-8"), "# Existing User Note\nDo not touch.")
        self.assertEqual(existing_note.stat().st_mtime, existing_mtime_before)

    def test_scoped_writable_inbox_prevents_escape(self):
        """Verifies that attempts to escape the scoped writable inbox via path traversal are confined."""
        vault_dir = Path(self.temp_dir.name) / "escape_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)

        server = McpServer(
            storage=self.storage,
            vault_path=vault_dir,
            writable_inbox="AI-Memory",
        )

        req = {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "tools/call",
            "params": {
                "name": "brain_add_memory",
                "arguments": {
                    "title": "Traversal Attempt",
                    "content": "Trying to escape inbox.",
                    "path": "../Notes/Hacked.md",
                },
            },
        }
        res = server.handle_request(req)
        self.assertFalse(res["result"].get("isError", False))
        data = json.loads(res["result"]["content"][0]["text"])

        # The path must be safely confined inside AI-Memory
        self.assertTrue(data["path"].startswith("AI-Memory/"))
        self.assertFalse((vault_dir / "Notes" / "Hacked.md").exists())
        self.assertTrue((vault_dir / data["path"]).exists())

    def test_scoped_writable_inbox_protects_external_note_on_complementary_update(self):
        """Verifies that complementary updates against external read-only notes create scoped references instead of editing the user file."""
        vault_dir = Path(self.temp_dir.name) / "comp_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "Notes").mkdir(parents=True, exist_ok=True)
        user_note = vault_dir / "Notes" / "Domain.md"
        user_note.write_text("# Domain Model\nCore entities: Account, User, Organization.", encoding="utf-8")

        # Ingest external note into storage
        self.storage.add_memory(
            title="Domain Model",
            content="Core entities: Account, User, Organization.",
            path="Notes/Domain.md",
        )

        server = McpServer(
            storage=self.storage,
            vault_path=vault_dir,
            writable_inbox="AI-Memory",
        )

        # Call brain_add_memory with complementary update
        req = {
            "jsonrpc": "2.0",
            "id": 52,
            "method": "tools/call",
            "params": {
                "name": "brain_add_memory",
                "arguments": {
                    "title": "Domain Model Extensions",
                    "content": "Core entities: Account, User, Organization. note aggiuntive: added Billing entity.",
                },
            },
        }
        res = server.handle_request(req)
        self.assertFalse(res["result"].get("isError", False))
        data = json.loads(res["result"]["content"][0]["text"])

        # External file must NOT be modified
        self.assertEqual(user_note.read_text(encoding="utf-8"), "# Domain Model\nCore entities: Account, User, Organization.")

        # Scoped reference created in AI-Memory
        self.assertTrue(data["path"].startswith("AI-Memory/"))
        self.assertEqual(data["deduplication"]["action"], "created_scoped_reference")
        self.assertTrue((vault_dir / data["path"]).is_file())

    def test_strict_read_only_mode_blocks_add_memory(self):
        """Verifies that strict read-only mode (read_only=True, writable_inbox=None) blocks brain_add_memory with clear error."""
        vault_dir = Path(self.temp_dir.name) / "ro_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)

        server = McpServer(
            storage=self.storage,
            vault_path=vault_dir,
            read_only=True,
            writable_inbox=None,
        )

        req = {
            "jsonrpc": "2.0",
            "id": 53,
            "method": "tools/call",
            "params": {
                "name": "brain_add_memory",
                "arguments": {
                    "title": "Blocked Note",
                    "content": "This should be blocked.",
                },
            },
        }
        res = server.handle_request(req)
        self.assertTrue(res["result"].get("isError", False))
        err_msg = res["result"]["content"][0]["text"]
        self.assertIn("strict read-only mode", err_msg)
        self.assertIn("--writable-inbox", err_msg)


if __name__ == "__main__":
    unittest.main()
