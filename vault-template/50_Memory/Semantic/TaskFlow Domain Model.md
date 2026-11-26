---
title: "TaskFlow Domain Model"
type: memory/semantic
status: active
priority: Alta
tags:
  - domain-model
  - entities
  - taskflow
  - schema
project: TaskFlow
source_agent: Antigravity
created_at: 2026-11-23
updated_at: 2026-11-23
importance_score: 4.0
---

# Modello di Dominio TaskFlow

> **Tipo:** Memoria Semantica | **Stato:** Attivo | **Progetto:** [[TaskFlow]]

Questo documento formalizza il modello concettuale, le entità centrali e le regole di business del motore di orchestrazione [[TaskFlow]].

## 🧱 Entità Principali

### 1. `Workflow`
Rappresenta una sequenza diretta e aciclica (DAG) di attività da compiere.
- `id`: Identificativo univoco (UUIDv4 o slug deterministico).
- `name`: Nome del flusso di lavoro.
- `status`: `PENDING`, `IN_PROGRESS`, `PAUSED`, `COMPLETED`, `FAILED`.
- `created_at` / `updated_at`: Timestamp ISO 8601.

### 2. `Task`
L'unità atomica di lavoro all'interno di un workflow.
- `id`: Identificativo univoco.
- `workflow_id`: Chiave esterna verso il workflow di appartenenza.
- `action`: Nome dell'azione o del tool da eseguire.
- `payload`: Parametri di input (struttura JSON serializzata).
- `state`: `QUEUED`, `RUNNING`, `SUCCESS`, `ERROR`, `SKIPPED`.
- `retry_count`: Numero di tentativi già eseguiti (max consentito: 3).

### 3. `WorkerNode`
Il processo o agente di calcolo che reclama ed esegue un task.
- `worker_id`: Identificativo univoco del processo (hostname + PID).
- `heartbeat`: Ultimo timestamp di attività per detection di nodi zombie.

## 📐 Invarianti di Business
1. **Idempotenza delle Esecuzioni:** L'invocazione multipla di un task completato con il medesimo `id` non deve rieseguire l'azione.
2. **Transizione Atomica di Stato:** Il passaggio da `QUEUED` a `RUNNING` avviene con lock transazionale per impedire la contesa tra worker distinti.
3. **Dead-Letter Queue (DLQ):** Task che falliscono per 3 volte consecutive vengono contrassegnati come `FAILED` e spostati nella coda di ispezione.
