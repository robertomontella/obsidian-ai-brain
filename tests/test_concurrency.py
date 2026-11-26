"""Concurrency and Stress Test Suite for AI-BRAIN SQLite WAL Mode.

Verifies that PRAGMA journal_mode=WAL and PRAGMA busy_timeout=5000 prevent any
sqlite3.OperationalError: database is locked errors across 10 concurrent workers
(both multi-threaded and multi-process).
"""

from __future__ import annotations

import concurrent.futures
import multiprocessing as mp
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO_ROOT / "mcp-server"
sys.path.insert(0, str(SERVER_DIR))

from storage import StorageManager


def _multiprocess_worker(db_path: str, worker_id: int, ops_per_worker: int) -> dict:
    """Worker function executed in separate OS processes.

    Creates an independent StorageManager instance and executes concurrent
    writes, updates, and searches.
    """
    errors = []
    storage = StorageManager(db_path=db_path)

    for i in range(ops_per_worker):
        # 1. Add Memory (Write)
        title = f"Process Worker {worker_id} Memory {i}"
        content = (
            f"Process worker {worker_id} is executing iteration {i}.\n"
            f"Testing SQLite WAL concurrency under load for project-gamma.\n"
            f"Important keyword: concurrent-proc-test-{worker_id}-{i}"
        )
        try:
            record = storage.add_memory(
                title=title,
                content=content,
                memory_type="memory/semantic",
                tags=["concurrency", f"worker-{worker_id}", "wal-test"],
                project="ConcurrencyMultiProcess",
                priority="Media",
                source_agent=f"WorkerProcess-{worker_id}"
            )
            mem_id = record["id"]

            # 2. Update status or read memory (Write / Access update)
            if i % 2 == 0:
                storage.update_memory_status(mem_id, status="active", supersedes=f"old-{worker_id}-{i}")
            else:
                storage.read_memory(mem_id)

            # 3. Search Memory (Read / FTS5 query)
            results = storage.search_memory(f"concurrent-proc-test-{worker_id}-{i}", limit=3)
            if not results:
                errors.append(f"FTS search failed to find memory for worker {worker_id} iter {i}")

        except sqlite3.OperationalError as e:
            errors.append(f"sqlite3.OperationalError: {e}")
        except Exception as e:
            errors.append(f"Unexpected error: {type(e).__name__}: {e}")

    return {
        "worker_id": worker_id,
        "completed_ops": ops_per_worker,
        "errors": errors
    }


class TestConcurrency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "brain_memory.sqlite"
        self.storage = StorageManager(db_path=self.db_path)
        self.storage.init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wal_and_busy_timeout_pragmas(self):
        """Verifies connection pragmas are correctly initialized."""
        with self.storage._connection() as conn:
            journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")
            self.assertEqual(busy_timeout, 5000)

    def test_multithreaded_concurrency_10_workers(self):
        """Simulates 10 concurrent threads executing writes, updates, and searches

        simultaneously on the same database. Proves 0 database locked errors.
        """
        num_workers = 10
        ops_per_worker = 15

        def thread_task(worker_id: int) -> list[str]:
            # Each thread uses its own StorageManager instance to avoid Python-level lock sharing
            thread_storage = StorageManager(db_path=self.db_path)
            errors: list[str] = []

            for i in range(ops_per_worker):
                try:
                    # Write
                    title = f"Thread Worker {worker_id} Item {i}"
                    content = (
                        f"Thread worker {worker_id} writing item {i}.\n"
                        f"Search token: thread-token-{worker_id}-{i}\n"
                        f"Architecture note on multi-threaded SQLite concurrency."
                    )
                    rec = thread_storage.add_memory(
                        title=title,
                        content=content,
                        memory_type="memory/procedural",
                        tags=["thread-test", f"worker-{worker_id}"],
                        project="ThreadConcurrency"
                    )

                    # Update
                    if i % 2 == 0:
                        thread_storage.update_memory_status(rec["id"], status="active")
                    else:
                        thread_storage.read_memory(rec["id"])

                    # Read / Search
                    res = thread_storage.search_memory(f"thread-token-{worker_id}-{i}")
                    if not res:
                        errors.append(f"Thread {worker_id} iter {i} missing from FTS")

                except sqlite3.OperationalError as oe:
                    errors.append(f"OperationalError: {oe}")
                except Exception as ex:
                    errors.append(f"Exception: {type(ex).__name__}: {ex}")

            return errors

        all_errors = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(thread_task, w_id) for w_id in range(num_workers)]
            for future in concurrent.futures.as_completed(futures):
                all_errors.extend(future.result())

        self.assertEqual(len(all_errors), 0, f"Thread concurrency errors encountered: {all_errors}")

        # Verify all records exist in DB
        with self.storage._connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories WHERE project = 'ThreadConcurrency'").fetchone()[0]
            self.assertEqual(count, num_workers * ops_per_worker)

    def test_multiprocess_concurrency_10_workers(self):
        """Simulates 10 concurrent OS processes accessing brain_memory.sqlite.

        Proves that PRAGMA journal_mode=WAL and PRAGMA busy_timeout=5000 prevent
        database locking across independent processes.
        """
        num_workers = 10
        ops_per_worker = 10
        db_path_str = str(self.db_path)

        ctx = mp.get_context("spawn")
        results = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers, mp_context=ctx) as executor:
            futures = [
                executor.submit(_multiprocess_worker, db_path_str, w_id, ops_per_worker)
                for w_id in range(num_workers)
            ]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())

        total_errors = []
        for res in results:
            if res["errors"]:
                total_errors.extend(res["errors"])

        self.assertEqual(
            len(total_errors), 0,
            f"Multi-process concurrency failed with lock errors: {total_errors}"
        )

        # Verify database integrity and total count
        with self.storage._connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories WHERE project = 'ConcurrencyMultiProcess'").fetchone()[0]
            self.assertEqual(count, num_workers * ops_per_worker)

            # Ensure FTS5 table is also fully consistent
            fts_count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
            self.assertGreaterEqual(fts_count, count)

    def test_concurrent_readers_and_writers(self):
        """Tests heavy read contention while simultaneous continuous writes are occurring."""
        num_writers = 5
        num_readers = 5
        ops = 10
        errors = []

        def writer(w_id: int):
            s = StorageManager(self.db_path)
            for i in range(ops):
                try:
                    s.add_memory(
                        title=f"RW Stress Writer {w_id} - {i}",
                        content=f"Content for stress test writer {w_id} iter {i} token-stress-{w_id}",
                        memory_type="memory/episodic",
                        project="RWStress"
                    )
                except Exception as e:
                    errors.append(f"Writer error: {e}")

        def reader(r_id: int):
            s = StorageManager(self.db_path)
            for i in range(ops):
                try:
                    s.search_memory("stress", limit=10)
                    s.get_stats()
                except Exception as e:
                    errors.append(f"Reader error: {e}")

        threads = []
        for i in range(num_writers):
            threads.append(concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(writer, i))
        for i in range(num_readers):
            threads.append(concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(reader, i))

        for t in threads:
            t.result()

        self.assertEqual(len(errors), 0, f"Errors in read/write stress: {errors}")


if __name__ == "__main__":
    unittest.main()
