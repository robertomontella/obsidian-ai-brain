"""5-Level Deduplication and Contradiction Resolution Engine for AI-BRAIN."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sanitizer import Sanitizer

# Common Italian and English stop words for lightweight tokenization
STOP_WORDS = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "e", "ed", "o", "od", "ma", "che", "cui", "non", "piu", "più",
    "del", "dello", "della", "dei", "degli", "delle",
    "al", "allo", "alla", "ai", "agli", "alle",
    "dal", "dallo", "dalla", "dai", "dagli", "dalle",
    "nel", "nello", "nella", "nei", "negli", "nelle",
    "sul", "sullo", "sulla", "sui", "sugli", "sulle",
    "come", "anche", "questo", "questa", "questi", "queste",
    "ha", "hanno", "è", "e'", "sono", "stato", "stata", "stati", "state",
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "over", "after",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "it", "its", "this", "that", "these", "those", "as", "if"
}

CONTRADICTION_KEYWORDS = {
    # Italian
    "sostituisce", "sostituito", "sostituita", "sostituzione", "sostituire",
    "invece di", "deprecato", "deprecata", "deprecati", "deprecate",
    "abbandonato", "abbandonata", "abbandonati", "migrazione da",
    "migrato da", "migrata da", "cambio architettura", "decisione ribaltata",
    "ribaltata", "annulla", "annullata", "non più", "non piu", "scartato",
    "scartata", "revocato", "revocata",
    # English
    "supersedes", "supersede", "replaces", "replace", "replaced",
    "instead of", "deprecated", "abandoned", "switched from",
    "switch from", "migrated from", "migration from", "overrules",
    "overrule", "overruled", "discarded", "reversal of"
}

COMPLEMENTARY_KEYWORDS = {
    "sezione aggiuntiva", "note aggiuntive", "integrazione", "backup",
    "snapshot", "troubleshooting", "estensione", "update", "aggiornamento",
    "approfondimento", "appendice", "changelog", "estensioni", "dettagli aggiuntivi",
    "additional notes", "extension", "supplementary", "appendix"
}


class DeduplicationLevel(str, Enum):
    """5 tiers of memory deduplication and reconciliation."""
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
    CONTRADICTION = "CONTRADICTION"
    COMPLEMENTARY_UPDATE = "COMPLEMENTARY_UPDATE"
    NEW_MEMORY = "NEW_MEMORY"


@dataclass
class ClassificationResult:
    """Outcome of deduplication evaluation against existing memories."""
    level: DeduplicationLevel
    matched_memory: dict[str, Any] | None = None
    similarity_score: float = 0.0
    reason: str = ""
    suggested_action: str = ""


def _tokenize(text: str) -> set[str]:
    """Extracts alphanumeric tokens lowercased and stripped of stop words."""
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    words = clean.split()
    return {w for w in words if len(w) > 2 and w not in STOP_WORDS}


def _jaccard_similarity(set1: set[str], set2: set[str]) -> float:
    """Computes Jaccard similarity index between two token sets."""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def _token_overlap(needle: set[str], haystack: set[str]) -> float:
    """Calculates what portion of needle tokens appear in haystack."""
    if not needle:
        return 0.0
    return len(needle & haystack) / len(needle)


class DeduplicationEngine:
    """Engine responsible for classifying new memories and resolving contradictions."""

    def __init__(self, storage: Any | None = None, writable_inbox: str | None = None) -> None:
        self.storage = storage
        self.writable_inbox = str(writable_inbox).strip("/").strip("\\") if writable_inbox else None

    def classify(
        self,
        new_title: str,
        new_content: str,
        existing_memories: list[dict[str, Any]] | None = None,
    ) -> ClassificationResult:
        """Classifies a memory proposal into one of the 5 deduplication tiers."""
        clean_title = Sanitizer.clean(new_title).strip()
        clean_content = Sanitizer.clean(new_content).strip()
        new_hash = hashlib.sha256(clean_content.encode("utf-8")).hexdigest()

        if existing_memories is None:
            if self.storage is not None:
                existing_memories = self.storage.get_all_memories(include_archived=False)
            else:
                existing_memories = []

        if not existing_memories:
            return ClassificationResult(
                level=DeduplicationLevel.NEW_MEMORY,
                matched_memory=None,
                similarity_score=0.0,
                reason="No existing memories found in vault.",
                suggested_action="Create new memory record.",
            )

        new_title_lower = clean_title.lower()
        new_content_lower = clean_content.lower()
        new_title_tokens = _tokenize(clean_title)
        new_content_tokens = _tokenize(clean_content)

        # 1. Level 1: EXACT_DUPLICATE Check
        for mem in existing_memories:
            if mem.get("status") == "superseded":
                continue
            mem_title_lower = mem.get("title", "").strip().lower()
            mem_hash = mem.get("hash", "")

            # Exact title match or exact SHA-256 content hash
            if (new_title_lower == mem_title_lower and new_title_lower) or (new_hash == mem_hash and new_hash):
                return ClassificationResult(
                    level=DeduplicationLevel.EXACT_DUPLICATE,
                    matched_memory=mem,
                    similarity_score=1.0,
                    reason=(
                        "Exact duplicate detected: identical title or SHA-256 hash."
                    ),
                    suggested_action="Increment access counter and touch confirmation.",
                )

        # 2. Check for CONTRADICTION across existing active memories
        # Contradiction occurs when new memory has contradiction markers AND shares topic/subject
        has_contradiction_marker = any(
            marker in new_content_lower or marker in new_title_lower
            for marker in CONTRADICTION_KEYWORDS
        )

        for mem in existing_memories:
            if mem.get("status") == "superseded":
                continue

            mem_title_lower = mem.get("title", "").strip().lower()
            mem_title_tokens = _tokenize(mem_title_lower)
            mem_content_tokens = _tokenize(mem.get("content", ""))

            title_sim = _jaccard_similarity(new_title_tokens, mem_title_tokens)
            content_sim = _jaccard_similarity(new_content_tokens, mem_content_tokens)
            title_overlap = _token_overlap(mem_title_tokens, new_title_tokens)
            content_overlap = _token_overlap(mem_content_tokens, new_content_tokens)
            
            # Check if existing title is referenced in new content or vice-versa
            references_existing = (
                mem_title_lower in new_content_lower
                or mem.get("id", "") in new_content_lower
                or (mem.get("project") and mem.get("project", "").lower() in new_content_lower)
            )

            # Shared topic: shared title tokens or subject overlap
            shared_topic = (
                title_overlap >= 0.30
                or title_sim >= 0.20
                or any(t in new_title_tokens for t in mem_title_tokens)
            )

            # Contradiction criteria:
            # - Explicit marker + (shared topic AND (content overlap > 0.20 or content_sim > 0.15 or references_existing))
            if has_contradiction_marker and (
                (shared_topic and (content_overlap >= 0.20 or content_sim >= 0.15 or references_existing or title_sim >= 0.20))
                or references_existing
            ):
                return ClassificationResult(
                    level=DeduplicationLevel.CONTRADICTION,
                    matched_memory=mem,
                    similarity_score=max(title_sim, content_sim, title_overlap),
                    reason=(
                        f"Contradiction detected against memory '{mem.get('title')}': "
                        "new decision overturns or replaces previous architecture."
                    ),
                    suggested_action="Mark existing as superseded and activate new record with graph relation.",
                )

        # 3. Check for Level 2: SEMANTIC_DUPLICATE and Level 4: COMPLEMENTARY_UPDATE
        best_candidate: dict[str, Any] | None = None
        best_score = 0.0
        best_is_complementary = False

        for mem in existing_memories:
            if mem.get("status") == "superseded":
                continue

            mem_title_lower = mem.get("title", "").strip().lower()
            mem_title_tokens = _tokenize(mem_title_lower)
            mem_content_tokens = _tokenize(mem.get("content", ""))

            title_sim = _jaccard_similarity(new_title_tokens, mem_title_tokens)
            content_sim = _jaccard_similarity(new_content_tokens, mem_content_tokens)
            combined_score = (title_sim * 0.6) + (content_sim * 0.4)

            # Check for explicit complementary markers in new title/content
            is_comp = any(
                comp in new_title_lower or comp in new_content_lower
                for comp in COMPLEMENTARY_KEYWORDS
            ) or (
                # Or title is a subsection of existing: "Title - Subtopic"
                (mem_title_lower in new_title_lower and new_title_lower != mem_title_lower)
            )

            if combined_score > best_score:
                best_score = combined_score
                best_candidate = mem
                best_is_complementary = is_comp

        if best_candidate and best_score >= 0.65 and not best_is_complementary:
            return ClassificationResult(
                level=DeduplicationLevel.SEMANTIC_DUPLICATE,
                matched_memory=best_candidate,
                similarity_score=best_score,
                reason=(
                    f"High semantic overlap ({best_score:.2f}) with '{best_candidate.get('title')}': "
                    "same topic and core conclusions."
                ),
                suggested_action="Reconfirm existing memory without duplicating.",
            )

        if best_candidate and (best_is_complementary or (0.30 <= best_score < 0.65 and (
            best_candidate.get("title", "").lower() in new_title_lower
        ))):
            return ClassificationResult(
                level=DeduplicationLevel.COMPLEMENTARY_UPDATE,
                matched_memory=best_candidate,
                similarity_score=best_score,
                reason=(
                    f"Complementary update for '{best_candidate.get('title')}': "
                    "adds new sections or details without contradicting existing facts."
                ),
                suggested_action="Update and extend existing memory note.",
            )

        # 5. Level 5: NEW_MEMORY
        return ClassificationResult(
            level=DeduplicationLevel.NEW_MEMORY,
            matched_memory=None,
            similarity_score=best_score,
            reason="Novel information with no significant overlap in active memories.",
            suggested_action="Create new memory note in appropriate cognitive layer.",
        )

    def process_and_store(
        self,
        title: str,
        content: str,
        memory_type: str = "memory/semantic",
        tags: list[str] | str | None = None,
        path: str | None = None,
        summary: str | None = None,
        priority: str = "Media",
        source_agent: str = "Antigravity",
        project: str | None = None,
        supersedes: str | None = None,
        importance_score: float = 3.0,
    ) -> dict[str, Any]:
        """Classifies and applies the appropriate storage action based on 5-level deduplication."""
        if self.storage is None:
            raise RuntimeError("StorageManager required to process and store memories.")

        clean_title = Sanitizer.clean(title).strip()

        candidates = self.storage.get_all_memories(include_archived=False)
        classification = self.classify(new_title=title, new_content=content, existing_memories=candidates)

        if classification.level == DeduplicationLevel.EXACT_DUPLICATE:
            matched = classification.matched_memory
            self.storage.increment_access_count(matched["id"])
            fresh_record = self.storage.read_memory(matched["id"])
            result = dict(fresh_record) if fresh_record else dict(matched)
            result["deduplication"] = {
                "level": DeduplicationLevel.EXACT_DUPLICATE,
                "action": "reconfirmed",
                "matched_id": matched["id"],
                "reason": classification.reason,
            }
            return result

        if classification.level == DeduplicationLevel.SEMANTIC_DUPLICATE:
            matched = classification.matched_memory
            self.storage.increment_access_count(matched["id"])
            fresh_record = self.storage.read_memory(matched["id"])
            result = dict(fresh_record) if fresh_record else dict(matched)
            result["deduplication"] = {
                "level": DeduplicationLevel.SEMANTIC_DUPLICATE,
                "action": "reconfirmed",
                "matched_id": matched["id"],
                "reason": classification.reason,
            }
            return result

        if classification.level == DeduplicationLevel.CONTRADICTION:
            matched = classification.matched_memory
            old_id = matched["id"]

            # Save new memory as active, referencing superseded note
            new_record = self.storage.add_memory(
                title=title,
                content=content,
                memory_type=memory_type,
                tags=tags,
                path=path,
                summary=summary,
                priority=priority,
                source_agent=source_agent,
                project=project or matched.get("project"),
                supersedes=old_id,
                status="active",
                importance_score=importance_score,
            )
            new_id = new_record["id"]

            # Mark old memory superseded and annotate superseded_by
            self.storage.update_memory_status(
                memory_id=old_id,
                status="superseded",
                superseded_by=new_id,
            )

            # Establish knowledge graph arc
            self.storage.add_relation(source_id=new_id, target_id=old_id, relation_type="supersedes")

            new_record["deduplication"] = {
                "level": DeduplicationLevel.CONTRADICTION,
                "action": "superseded_old_record",
                "superseded_id": old_id,
                "reason": classification.reason,
            }
            return new_record

        if classification.level == DeduplicationLevel.COMPLEMENTARY_UPDATE:
            matched = classification.matched_memory
            target_id = matched["id"]
            matched_path = matched.get("path", "")

            # If matched note is outside designated writable inbox, do NOT mutate in place!
            # Instead, create a scoped reference note inside the writable inbox.
            if self.writable_inbox and not matched_path.startswith(f"{self.writable_inbox}/"):
                new_record = self.storage.add_memory(
                    title=title,
                    content=content,
                    memory_type=memory_type,
                    tags=tags,
                    path=path,
                    summary=summary,
                    priority=priority,
                    source_agent=source_agent,
                    project=project or matched.get("project"),
                    status="active",
                    importance_score=importance_score,
                )
                new_record["deduplication"] = {
                    "level": DeduplicationLevel.COMPLEMENTARY_UPDATE,
                    "action": "created_scoped_reference",
                    "matched_id": target_id,
                    "reason": (
                        f"Referenced external read-only note '{matched.get('title')}' "
                        f"without modifying user file on disk."
                    ),
                }
                return new_record

            existing_content = matched.get("content", "")
            clean_new_content = Sanitizer.clean(content).strip()

            if clean_new_content not in existing_content:
                header = f"\n\n## {clean_title}\n" if clean_title != matched.get("title") else "\n\n"
                merged_content = f"{existing_content}{header}{clean_new_content}".strip()
            else:
                merged_content = existing_content

            # Normalize tags merge
            existing_tags = matched.get("tags", "[]")
            try:
                t_list = json.loads(existing_tags) if isinstance(existing_tags, str) else list(existing_tags)
            except Exception:
                t_list = []
            if isinstance(tags, list):
                for t in tags:
                    if t not in t_list:
                        t_list.append(t)

            updated_record = self.storage.add_memory(
                title=matched["title"],
                content=merged_content,
                memory_type=matched.get("type", memory_type),
                tags=t_list,
                path=matched["path"],
                summary=summary or matched.get("summary"),
                priority=priority,
                source_agent=source_agent,
                project=project or matched.get("project"),
                status=matched.get("status", "active"),
                importance_score=importance_score,
                memory_id=target_id,
            )
            updated_record["deduplication"] = {
                "level": DeduplicationLevel.COMPLEMENTARY_UPDATE,
                "action": "updated_existing",
                "target_id": target_id,
                "reason": classification.reason,
            }
            return updated_record

        # Default: NEW_MEMORY
        created_record = self.storage.add_memory(
            title=title,
            content=content,
            memory_type=memory_type,
            tags=tags,
            path=path,
            summary=summary,
            priority=priority,
            source_agent=source_agent,
            project=project,
            supersedes=supersedes,
            status="active",
            importance_score=importance_score,
        )
        created_record["deduplication"] = {
            "level": DeduplicationLevel.NEW_MEMORY,
            "action": "created",
            "reason": classification.reason,
        }
        return created_record


def classify(
    new_title: str,
    new_content: str,
    existing_memories: list[dict[str, Any]],
) -> ClassificationResult:
    """Convenience functional interface for classifying a memory candidate."""
    engine = DeduplicationEngine()
    return engine.classify(new_title=new_title, new_content=new_content, existing_memories=existing_memories)
