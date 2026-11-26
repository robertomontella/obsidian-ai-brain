---
title: "TaskFlow SQLite Architecture Decision"
type: memory/episodic
status: active
priority: Alta
tags:
  - architecture
  - sqlite
  - taskflow
  - storage
  - wal
project: TaskFlow
source_agent: Antigravity
created_at: 2026-11-23
updated_at: 2026-11-23
importance_score: 4.5
---

# Decisione Architetturale: Engine di Persistenza SQLite WAL per TaskFlow

> **Data:** 2026-11-23 | **Tipo:** Memoria Episodica | **Progetto:** [[TaskFlow]]

## 🎯 Contesto & Problema
Il runtime del motore [[TaskFlow]] deve persistere le code di esecuzione dei task, i checkpoint dei workflow e lo storico degli stati garantendo:
- Latenza di lettura/scrittura < 5ms su macchine locali e server edge.
- Zero dipendenze da server o demoni esterni (es. Redis, PostgreSQL).
- Accesso concorrente sicuro per processi worker multipli senza corruzione dei dati.

## 💡 Decisione
È stato deciso di adottare **SQLite 3** configurato con:
1. `PRAGMA journal_mode = WAL;` (Write-Ahead Logging per consentire letture concorrenti non bloccate dalle scritture).
2. `PRAGMA synchronous = NORMAL;` (ottimo equilibrio tra integrità transazionale e throughput I/O).
3. `PRAGMA busy_timeout = 5000;` (attesa fino a 5 secondi in caso di contesa lock per eliminare gli errori `database is locked`).
4. Indici Full-Text Search integrati (**FTS5**) per la ricerca testuale istantanea sui log di esecuzione.

## ⚖️ Valutazione Alternative
- **Flat Files JSON:** Esclusi per assenza di transazionalità atomica (ACID) e rischio di corruzione sotto scritture concorrenti.
- **Redis / Key-Value Server:** Escluso per la dipendenza da un processo demone esterno e requisiti di consumo RAM.
- **PostgreSQL:** Eccessivo per ambienti edge o tool locali single-node; introduce complessità di setup.

## 🚀 Conseguenze & Impatto
- L'engine non richiede installazioni esterne, sfruttando la libreria `sqlite3` integrata in Python.
- I file di lock e il file WAL (`.db-wal`, `.db-shm`) devono essere gestiti e ignorati nei sistemi di versionamento del codice.
- Supporto nativo per transazioni isolate e recovery automatico in caso di kill improvviso del processo worker.
