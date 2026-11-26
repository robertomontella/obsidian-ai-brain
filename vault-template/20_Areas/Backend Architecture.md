---
title: "Area: Backend Architecture & Distributed Systems"
type: area
status: active
priority: Alta
tags:
  - area
  - backend
  - distributed-systems
  - architecture
created_at: 2026-11-23
updated_at: 2026-11-23
---

# Backend Architecture & Distributed Systems

Questa cartella raccoglie le linee guida, gli standard architetturali e le prassi per i servizi backend e i sistemi distribuiti.

## 🏛️ Principi Fondamentali
1. **Local-First & Zero Extra Infrastructure:**
   - Preferire motori embedded (es. SQLite in WAL mode) quando il carico e i requisiti lo consentono.
   - Ridurre i punti singoli di fallimento e la complessità di deployment.

2. **Isolamento della Concorrenza:**
   - Gestire la concorrenza multi-processo tramite lockfile a livello di filesystem (`fcntl` su Unix/macOS) e parametri transazionali espliciti (`busy_timeout`).
   - Evitare condizioni di stallo (deadlock) strutturando transazioni brevi e atomiche.

3. **Idempotenza e Recupero dagli Errori:**
   - Tutte le mutazioni di stato critiche devono supportare chiavi di idempotenza o token deterministici.
   - Progettare meccanismi di self-healing e riallineamento periodico degli indici.

## 🔗 Progetti & Risorse Correlate
- Progetto [[TaskFlow]]
- Guida [[Coding Guidelines]]
