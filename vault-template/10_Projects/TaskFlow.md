---
title: "Progetto TaskFlow"
type: project
status: active
priority: Alta
tags:
  - project
  - taskflow
  - orchestration
  - engine
created_at: 2026-11-23
updated_at: 2026-11-23
---

# Progetto TaskFlow: Orchestration Engine

TaskFlow è un motore leggero di orchestrazione task e flussi di lavoro asincroni progettato per microservizi e pipeline agentiche multi-processo.

## 🎯 Obiettivi Chiave
1. Fornire una coda di esecuzione deterministica basata su SQLite in modalità WAL.
2. Garantire latenze di dispatch inferiori a 5ms senza dipendenze da server di messaggistica esterni (es. Redis/RabbitMQ).
3. Permettere il resume automatico di workflow complessi in caso di crash o interruzione di rete.

## 📐 Architettura & Memoria Cognitiva
Il progetto è documentato e tracciato nei layer di memoria a lungo termine di AI-BRAIN:
- **Decisione Architetturale:** [[2026-11-23 TaskFlow SQLite Architecture]] (Episodic Memory)
- **Modello di Dominio:** [[TaskFlow Domain Model]] (Semantic Memory)
- **Procedura di Test:** [[SOP - Running TaskFlow Tests]] (Procedural Memory)
- **Sprint Attivo:** [[TaskFlow Active Sprint]] (Working Memory)

## 📋 Roadmap & Milestone
- [x] Definizione dello schema dati delle code e stati (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`).
- [ ] Implementazione del connection pool concorrente con lockfile e `busy_timeout`.
- [ ] Benchmark prestazionali su 10.000 eventi/secondo.
- [ ] Rilascio SDK Python in pura Standard Library.
