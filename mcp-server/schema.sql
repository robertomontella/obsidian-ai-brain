-- AI-BRAIN Core Schema
-- SQLite 3 DDL with Full-Text Search (FTS5) and Relationship Graph

-- 1. Tabella Principale delle Memorie
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    summary TEXT,
    tags TEXT,
    content TEXT,
    hash TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    priority TEXT DEFAULT 'Media',
    source_agent TEXT,
    project TEXT,
    supersedes TEXT,
    superseded_by TEXT,
    status TEXT DEFAULT 'active',
    importance_score REAL DEFAULT 3.0,
    access_count INTEGER DEFAULT 0,
    last_accessed TEXT,
    archived_at TEXT
);

-- Indici di supporto per filtri e ordinamenti rapidi
CREATE INDEX IF NOT EXISTS idx_memories_path ON memories(path);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_last_accessed ON memories(last_accessed);

-- 2. Indice Virtuale FTS5 BM25 per Ricerca Full-Text Istantanea
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    title,
    content,
    tags,
    summary,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Trigger di sincronizzazione automatica memories -> memories_fts
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, title, content, tags, summary)
    VALUES (new.rowid, new.title, new.content, new.tags, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags, summary)
    VALUES('delete', old.rowid, old.title, old.content, old.tags, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF title, content, tags, summary ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags, summary)
    VALUES('delete', old.rowid, old.title, old.content, old.tags, old.summary);
    INSERT INTO memories_fts(rowid, title, content, tags, summary)
    VALUES (new.rowid, new.title, new.content, new.tags, new.summary);
END;

-- 3. Tabella Relazioni tra Memorie (Knowledge Graph)
CREATE TABLE IF NOT EXISTS memory_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL, -- 'relates_to', 'supersedes', 'derived_from', 'depends_on'
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY(source_id) REFERENCES memories(id) ON DELETE CASCADE,
    FOREIGN KEY(target_id) REFERENCES memories(id) ON DELETE CASCADE,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_relations_source ON memory_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON memory_relations(target_id);
