import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mcp-server"))

from storage import StorageManager


class TestMcpStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "brain_memory.sqlite"
        self.storage = StorageManager(db_path=self.db_path)
        self.storage.init_db()

    def tearDown(self):
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    def test_schema_initialization_and_wal_mode(self):
        self.assertTrue(self.db_path.exists())
        stats = self.storage.get_stats()
        self.assertTrue(stats.get("wal_enabled"), "PRAGMA journal_mode must be WAL")
        self.assertEqual(stats.get("total_memories"), 0)

        # Check tables existence
        with self.storage._get_connection() as conn:
            cursor = conn.cursor()
            tables = [row[0] for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()]
            self.assertIn("memories", tables)
            self.assertIn("memories_fts", tables)
            self.assertIn("memory_relations", tables)

            # Check busy_timeout PRAGMA
            busy_timeout = cursor.execute("PRAGMA busy_timeout;").fetchone()[0]
            self.assertEqual(busy_timeout, 5000)

    def test_add_memory_and_auto_sanitization(self):
        # Adding a memory with an API key should auto-sanitize before saving to DB
        content_with_secret = (
            "TaskFlow uses SQLite WAL mode.\n"
            "Secret token for deployment: sk-proj-supersecret1234567890abcdef."
        )
        record = self.storage.add_memory(
            title="TaskFlow Architecture",
            content=content_with_secret,
            memory_type="memory/semantic",
            tags=["sqlite", "wal", "backend"],
            project="TaskFlow"
        )

        self.assertIsNotNone(record)
        self.assertIn("id", record)
        self.assertEqual(record["title"], "TaskFlow Architecture")

        # Verify secret was sanitized from stored content
        read_back = self.storage.read_memory(record["id"])
        self.assertIsNotNone(read_back)
        self.assertNotIn("sk-proj-supersecret1234567890abcdef", read_back["content"])
        self.assertIn("[REDACTED_API_KEY]", read_back["content"])

    def test_fts5_bm25_search_latency_and_ranking(self):
        # Insert test records
        for i in range(20):
            self.storage.add_memory(
                title=f"Note {i}: Microservices and Concurrency",
                content=f"Detailed documentation on concurrent database access pattern {i} with SQLite WAL.",
                memory_type="memory/semantic" if i % 2 == 0 else "memory/episodic",
                tags=["concurrency", "sqlite"]
            )

        self.storage.add_memory(
            title="Irrelevant Topic",
            content="This note discusses front-end CSS color schemes.",
            memory_type="memory/working",
            tags=["frontend", "css"]
        )

        # Benchmark search latency: must be < 5ms
        start = time.perf_counter()
        results = self.storage.search_memory(query="concurrent SQLite WAL", limit=5)
        duration_ms = (time.perf_counter() - start) * 1000

        self.assertLess(duration_ms, 5.0, f"Search took {duration_ms:.2f}ms, expected < 5ms")
        self.assertGreaterEqual(len(results), 1)
        self.assertIn("concurrency", results[0]["title"].lower() + results[0]["content"].lower())

    def test_search_filtering_by_type_and_tag(self):
        self.storage.add_memory(
            title="Semantic Decision",
            content="Domain model for entities and aggregate roots.",
            memory_type="memory/semantic",
            tags=["domain", "architecture"]
        )
        self.storage.add_memory(
            title="Episodic Sprint Record",
            content="Sprint review: completed migration to SQLite WAL.",
            memory_type="memory/episodic",
            tags=["sprint", "architecture"]
        )

        # Filter by memory_type
        semantic_results = self.storage.search_memory(
            query="architecture", memory_type="memory/semantic"
        )
        self.assertEqual(len(semantic_results), 1)
        self.assertEqual(semantic_results[0]["type"], "memory/semantic")

        # Filter by tag
        sprint_results = self.storage.search_memory(
            query="architecture", tag="sprint"
        )
        self.assertEqual(len(sprint_results), 1)
        self.assertEqual(sprint_results[0]["title"], "Episodic Sprint Record")

    def test_read_memory_and_access_count_increment(self):
        created = self.storage.add_memory(
            title="SOP Deployment",
            content="Run tests then deploy artifacts.",
            memory_type="memory/procedural",
            tags=["sop", "deploy"]
        )

        # Initial access count
        rec1 = self.storage.read_memory(created["id"])
        self.assertEqual(rec1["access_count"], 1)

        # Second read
        rec2 = self.storage.read_memory(created["path"])
        self.assertEqual(rec2["access_count"], 2)

        # Non-existent
        self.assertIsNone(self.storage.read_memory("non-existent-id"))

    def test_get_stats(self):
        self.storage.add_memory(title="T1", content="C1", memory_type="memory/semantic")
        self.storage.add_memory(title="T2", content="C2", memory_type="memory/episodic")

        stats = self.storage.get_stats()
        self.assertEqual(stats["total_memories"], 2)
        self.assertEqual(stats["by_type"].get("memory/semantic"), 1)
        self.assertEqual(stats["by_type"].get("memory/episodic"), 1)
        self.assertTrue(stats["wal_enabled"])
        self.assertGreater(stats["db_size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
