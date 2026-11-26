"""Unit tests for the 5-level Deduplication Engine."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "mcp-server"))

from storage import StorageManager
from deduplication import DeduplicationEngine, DeduplicationLevel, classify


class TestDeduplication(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "brain_memory.sqlite"
        self.storage = StorageManager(db_path=self.db_path)
        self.storage.init_db()
        self.engine = DeduplicationEngine(storage=self.storage)

        # Pre-populate with base memory
        self.base_memory = self.storage.add_memory(
            title="Architettura Database TaskFlow",
            content="TaskFlow adotta PostgreSQL 15 come database primario relazionale con pooling pgbouncer.",
            memory_type="memory/semantic",
            tags=["database", "architecture", "taskflow"],
            priority="Alta",
            project="TaskFlow"
        )

    def tearDown(self):
        if hasattr(self, "temp_dir"):
            self.temp_dir.cleanup()

    def test_level_1_exact_duplicate_by_title_and_hash(self):
        """Exact match on title or SHA-256 hash increments access/confirm count without creating duplicate."""
        # 1. Exact title duplicate
        result = self.engine.classify(
            new_title="Architettura Database TaskFlow",
            new_content="Contenuto leggermente diverso ma titolo identico",
            existing_memories=[self.base_memory]
        )
        self.assertEqual(result.level, DeduplicationLevel.EXACT_DUPLICATE)
        self.assertEqual(result.matched_memory["id"], self.base_memory["id"])

        # 2. Exact content hash duplicate with different title
        result_hash = self.engine.classify(
            new_title="Nuovo Titolo Per Stesso Testo",
            new_content="TaskFlow adotta PostgreSQL 15 come database primario relazionale con pooling pgbouncer.",
            existing_memories=[self.base_memory]
        )
        self.assertEqual(result_hash.level, DeduplicationLevel.EXACT_DUPLICATE)
        self.assertEqual(result_hash.matched_memory["id"], self.base_memory["id"])

        # Test process_and_store reconfirms access count
        stored = self.engine.process_and_store(
            title="Architettura Database TaskFlow",
            content="TaskFlow adotta PostgreSQL 15 come database primario relazionale con pooling pgbouncer."
        )
        self.assertEqual(stored["deduplication"]["level"], DeduplicationLevel.EXACT_DUPLICATE)
        self.assertEqual(stored["id"], self.base_memory["id"])
        
        # Verify access count was incremented
        updated = self.storage.read_memory(self.base_memory["id"])
        self.assertGreater(updated["access_count"], 0)

    def test_level_2_semantic_duplicate(self):
        """Semantic duplicate with high topic and conclusion overlap reconfirms without duplicating."""
        similar_title = "TaskFlow Architettura Database"
        similar_content = "TaskFlow utilizza il database relazionale PostgreSQL 15 come storage primario insieme al pooling pgbouncer."

        result = self.engine.classify(
            new_title=similar_title,
            new_content=similar_content,
            existing_memories=[self.base_memory]
        )
        self.assertEqual(result.level, DeduplicationLevel.SEMANTIC_DUPLICATE)
        self.assertEqual(result.matched_memory["id"], self.base_memory["id"])

        stored = self.engine.process_and_store(
            title=similar_title,
            content=similar_content
        )
        self.assertEqual(stored["deduplication"]["level"], DeduplicationLevel.SEMANTIC_DUPLICATE)
        self.assertEqual(stored["id"], self.base_memory["id"])

    def test_level_3_contradiction_supersedes_old_and_activates_new(self):
        """Contradiction marks old memory as superseded and activates new memory with graph relation."""
        contradicting_title = "Migrazione Database TaskFlow a SQLite WAL"
        contradicting_content = (
            "Decisione ribaltata: sostituito PostgreSQL 15 con SQLite 3 in modalità WAL invece di pgbouncer. "
            "Sostituisce la precedente architettura PostgreSQL per eliminare la dipendenza da server esterni."
        )

        result = self.engine.classify(
            new_title=contradicting_title,
            new_content=contradicting_content,
            existing_memories=[self.base_memory]
        )
        self.assertEqual(result.level, DeduplicationLevel.CONTRADICTION)
        self.assertEqual(result.matched_memory["id"], self.base_memory["id"])

        # Process and store the contradiction
        stored = self.engine.process_and_store(
            title=contradicting_title,
            content=contradicting_content,
            memory_type="memory/episodic",
            project="TaskFlow"
        )

        self.assertEqual(stored["deduplication"]["level"], DeduplicationLevel.CONTRADICTION)
        new_id = stored["id"]
        self.assertNotEqual(new_id, self.base_memory["id"])
        self.assertEqual(stored["status"], "active")
        self.assertEqual(stored["supersedes"], self.base_memory["id"])

        # Verify old memory is now 'superseded' with annotation
        old_record = self.storage.read_memory(self.base_memory["id"])
        self.assertEqual(old_record["status"], "superseded")
        self.assertEqual(old_record.get("superseded_by"), new_id)

        # Verify graph relation in memory_relations
        relations = self.storage.get_relations(new_id)
        supersedes_relations = [
            r for r in relations
            if r["source_id"] == new_id and r["target_id"] == self.base_memory["id"] and r["relation_type"] == "supersedes"
        ]
        self.assertEqual(len(supersedes_relations), 1)

    def test_level_4_complementary_update(self):
        """Complementary update extends existing memory with new sections without contradiction."""
        complementary_title = "Architettura Database TaskFlow - Backup e Snapshot"
        complementary_content = (
            "Sezione Aggiuntiva Backup: TaskFlow esegue backup orari dei dati tramite pg_dump "
            "e snapshot atomici del volume disco ogni 24 ore."
        )

        result = self.engine.classify(
            new_title=complementary_title,
            new_content=complementary_content,
            existing_memories=[self.base_memory]
        )
        self.assertEqual(result.level, DeduplicationLevel.COMPLEMENTARY_UPDATE)
        self.assertEqual(result.matched_memory["id"], self.base_memory["id"])

        stored = self.engine.process_and_store(
            title=complementary_title,
            content=complementary_content,
            project="TaskFlow"
        )
        self.assertEqual(stored["deduplication"]["level"], DeduplicationLevel.COMPLEMENTARY_UPDATE)
        # Should update existing memory
        self.assertEqual(stored["id"], self.base_memory["id"])
        updated = self.storage.read_memory(self.base_memory["id"])
        self.assertIn("Backup e Snapshot", updated["content"])

    def test_level_5_new_memory(self):
        """Completely new topic creates a new memory record in appropriate layer."""
        new_title = "Protocollo di Autenticazione OAuth2"
        new_content = "Implementazione del flusso Authorization Code Grant con PKCE per client web e mobile."

        result = self.engine.classify(
            new_title=new_title,
            new_content=new_content,
            existing_memories=[self.base_memory]
        )
        self.assertEqual(result.level, DeduplicationLevel.NEW_MEMORY)
        self.assertIsNone(result.matched_memory)

        stored = self.engine.process_and_store(
            title=new_title,
            content=new_content,
            memory_type="memory/semantic"
        )
        self.assertEqual(stored["deduplication"]["level"], DeduplicationLevel.NEW_MEMORY)
        self.assertNotEqual(stored["id"], self.base_memory["id"])
        self.assertEqual(stored["title"], new_title)

    def test_classify_helper_function(self):
        """Top-level classify function is directly callable with memories list."""
        res = classify(
            new_title="Architettura Database TaskFlow",
            new_content="Qualsiasi testo",
            existing_memories=[self.base_memory]
        )
        self.assertEqual(res.level, DeduplicationLevel.EXACT_DUPLICATE)

    def test_complementary_update_preserves_and_merges_tags(self):
        """Validates that existing tags are preserved and merged without JSON decode failure."""
        complementary_title = "Architettura Database TaskFlow - Pooling Config"
        complementary_content = "Sezione Aggiuntiva: Configurazione pool size a 25 connessioni con timeout a 5000ms."

        stored = self.engine.process_and_store(
            title=complementary_title,
            content=complementary_content,
            tags=["pooling", "tuning"]
        )
        self.assertEqual(stored["deduplication"]["level"], DeduplicationLevel.COMPLEMENTARY_UPDATE)
        updated = self.storage.read_memory(self.base_memory["id"])
        
        # Tags must contain original tags + new tags
        import json
        tag_list = json.loads(updated["tags"]) if isinstance(updated["tags"], str) else updated["tags"]
        self.assertIn("database", tag_list)
        self.assertIn("taskflow", tag_list)
        self.assertIn("pooling", tag_list)
        self.assertIn("tuning", tag_list)

    def test_exact_duplicate_skips_superseded_memory(self):
        """Verifies that an obsolete superseded memory is not reconfirmed by EXACT_DUPLICATE check."""
        # Mark base memory as superseded
        self.storage.update_memory_status(self.base_memory["id"], status="superseded")
        superseded_mem = self.storage.read_memory(self.base_memory["id"])

        result = self.engine.classify(
            new_title="Architettura Database TaskFlow",
            new_content="Nuova proposta dopo dismissione",
            existing_memories=[superseded_mem]
        )
        self.assertNotEqual(result.level, DeduplicationLevel.EXACT_DUPLICATE)


if __name__ == "__main__":
    unittest.main()
