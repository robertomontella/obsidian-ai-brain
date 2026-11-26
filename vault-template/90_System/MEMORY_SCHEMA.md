# Architettura del Sistema di Memoria AI-BRAIN

> **Versione:** 1.0.0 (Open-Source Architecture Reference)  
> **Specifiche:** Model Context Protocol (MCP 2.0) + Obsidian Vault (PARA + 4 Cognitive Layers)  
> **Database Motore:** SQLite 3 WAL Mode con Full-Text Search FTS5 BM25

---

## 1. Tassonomia della Memoria Cognitiva

AI-BRAIN unisce l'organizzazione metodologica **PARA** (Projects, Areas, Resources, Archive) a un'architettura a **4 Layer Cognitivi** specializzati per agenti intelligenti:

```
AI-BRAIN-Vault/
├── 00_Inbox/               # Memoria di lavoro: catture grezze, note temporanee da smistare
├── 10_Projects/            # Progetti attivi ad alta priorità con scadenze definite
├── 20_Areas/               # Aree di responsabilità a lungo termine (Architettura, DevOps...)
├── 30_Resources/           # Risorse informative, cheat sheet, documentazione esterna
├── 40_Archive/             # Progetti completati e memoria inattiva o deprecata
├── 50_Memory/              # LAYER COGNITIVO STRUTTURATO PER AGENTI AI
│   ├── Episodic/           # Log temporali, decisioni architetturali, session history (half-life: 45d)
│   ├── Semantic/           # Principi permanenti, definizioni di dominio, invarianti (half-life: 365d)
│   ├── Procedural/         # Runbook, SOP, procedure standard, direttive operative (half-life: 180d)
│   └── Working/            # Contesto attivo di sessione corrente e sprint (half-life: 7d)
└── 90_System/              # Infrastruttura di sistema e specifiche di configurazione
```

### Caratteristiche dei Layer Cognitivi
| Layer | Scopo Principale | Durabilità Tipica | Esempio |
|---|---|---|---|
| **Episodic** | Tracciare *cosa* è accaduto e *perché* è stata presa una decisione | Media (~45 giorni) | Scelta di un database o risoluzione di un bug critico |
| **Semantic** | Fatti stabili, modelli di dominio, tassonomie e concetti | Lunga (~365 giorni) | Modello di dominio, glossario, specifiche di protocollo |
| **Procedural** | Istruzioni "how-to", sequenze di comandi e procedure (SOP) | Medio-Lunga (~180 giorni) | Come eseguire la suite di test o il deployment |
| **Working** | Stato corrente del lavoro, checklist di sprint, blocker attivi | Breve (~7 giorni) | Note di sessione attiva, task dello sprint corrente |

---

## 2. Schema Dati Relazionale & FTS5 (SQLite)

Il runtime AI-BRAIN mantiene una copia indicizzata delle memorie in un database SQLite locale (`brain_memory.sqlite`) per consentire ricerche testuali < 5ms senza caricare modelli pesanti in RAM.

### Tabella: `memories`
| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | `TEXT PRIMARY KEY` | Hash deterministico a 12 caratteri del percorso relativo |
| `path` | `TEXT UNIQUE` | Percorso relativo all'interno del vault (`50_Memory/...`) |
| `title` | `TEXT NOT NULL` | Titolo estratto dal frontmatter o dal primo header H1 |
| `type` | `TEXT NOT NULL` | `memory/episodic`, `memory/semantic`, `memory/procedural`, `memory/working` |
| `summary` | `TEXT` | Abstract o sintesi per recall rapido |
| `tags` | `TEXT` | Array JSON di tag associati |
| `content` | `TEXT` | Testo completo della nota in formato Markdown |
| `hash` | `TEXT NOT NULL` | Hash SHA-256 del contenuto per la sincronizzazione incrementale |
| `created_at` | `TEXT` | Data di creazione (formato ISO 8601 `YYYY-MM-DD`) |
| `updated_at` | `TEXT` | Data di ultimo aggiornamento |
| `priority` | `TEXT` | Livello di priorità (`Alta`, `Media`, `Bassa`) |
| `source_agent` | `TEXT` | Agente autore (es. `Antigravity`, `Claude`, `Cursor`, `Gemini`) |
| `project` | `TEXT` | Progetto associato (opzionale) |
| `supersedes` | `TEXT` | Wikilink della memoria superata da questa nota (se applicabile) |
| `status` | `TEXT` | Stato del record (`active`, `superseded`, `archived`) |
| `importance_score`| `REAL` | Valore di rilevanza cognitiva (da 1.0 a 5.0) |
| `access_count` | `INTEGER` | Numero di accessi o richiami nei prompt degli agenti |
| `last_accessed` | `TEXT` | Data dell'ultimo accesso per ranking recency (formato `YYYY-MM-DD HH:MM:SS`) |
| `archived_at` | `TEXT` | Data di archiviazione esplicita del record (formato `YYYY-MM-DD HH:MM:SS`) |

### Tabella Virtuale FTS5: `memory_fts`
Indice full-text sincronizzato automaticamente con `memories` tramite trigger SQLite:
```sql
CREATE VIRTUAL TABLE memory_fts USING fts5(
    title,
    content,
    tags,
    summary,
    content='memories',
    content_rowid='rowid',
    tokenize='porter unicode61'
);
```

---

## 3. Standard Frontmatter Markdown

Ogni nota di memoria strutturata presente in `50_Memory/` deve iniziare con un blocco frontmatter YAML valido conforme a questo standard:

```yaml
---
title: "Titolo Chiaro e Sintetico della Nota"
type: memory/episodic   # Valori validi: memory/episodic, memory/semantic, memory/procedural, memory/working
status: active          # Valori validi: active, superseded, archived
priority: Media         # Valori validi: Alta, Media, Bassa
tags:
  - tag-principale
  - categoria-secondaria
project: NomeProgetto   # Opzionale: nome del progetto in 10_Projects
source_agent: Antigravity
created_at: 2026-11-23
updated_at: 2026-11-23
importance_score: 4.0
---
```

---

## 4. Ciclo di Vita e Deduplicazione a 5 Livelli

Durante l'acquisizione automatica delle memorie da parte degli agenti, il runtime classifica la nuova informazione rispetto al patrimonio esistente:
1. **EXACT_DUPLICATE:** Contenuto o hash identico a una memoria già presente -> Nessuna modifica, aggiorna solo `access_count` e `updated_at`.
2. **SEMANTIC_DUPLICATE:** Concetto identico espresso con parole diverse -> Arricchisce i tag della nota esistente senza creare duplicati.
3. **CONTRADICTION:** La nuova informazione smentisce o sostituisce una decisione precedente -> La nota precedente viene impostata a `status: superseded` con puntatore `superseded_by`, la nuova diventa `status: active` con `supersedes: [[Vecchia Nota]]`.
4. **COMPLEMENTARY / UPDATE:** Dettaglio additivo su un argomento noto -> La nota esistente viene aggiornata incrementalmente.
5. **NEW:** Concetto mai registrato in precedenza -> Creazione di una nuova scheda con metadati completi.

---

## 5. Politica di Sicurezza & Zero-Leak

La privacy e la sicurezza del vault sono garantite da regole deterministiche prima della scrittura su disco:
- **Nessuna memorizzazione di credenziali:** Filtro regex automatico che intercetta e maschera chiavi API (es. OpenAI, Anthropic, GitHub), bearer token e password prima del salvataggio.
- **Nessun percorso assoluto personale:** I percorsi salvati nelle note usano notazioni relative o token di ambiente (`~/vault`, `./progetto`).
- **Controllo utente:** Le note sono file Markdown standard modificabili liberamente da qualsiasi editor di testo o client Obsidian.
