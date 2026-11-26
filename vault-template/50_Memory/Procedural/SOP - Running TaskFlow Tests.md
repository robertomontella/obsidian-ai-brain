---
title: "SOP - Running TaskFlow Tests"
type: memory/procedural
status: active
priority: Media
tags:
  - sop
  - testing
  - taskflow
  - procedures
  - runbook
project: TaskFlow
source_agent: Antigravity
created_at: 2026-11-23
updated_at: 2026-11-23
importance_score: 3.5
---

# Procedura Standard (SOP): Esecuzione Test Suite TaskFlow

> **Tipo:** Memoria Procedurale | **Ambito:** Qualità & CI/CD | **Progetto:** [[TaskFlow]]

Questa procedura operativa descrive i passaggi per eseguire la suite di test unitari, di integrazione e di concorrenza del progetto [[TaskFlow]].

## 📋 Prerequisiti
- Python 3.9 o versione superiore installato nell'ambiente di esecuzione.
- Nessuna dipendenza esterna richiesta (esecuzione basata su `unittest` della Standard Library).

## 🛠️ Procedura Operativa

### 1. Esecuzione Suite Unitaria Completa
Dalla radice del progetto, lanciare il runner standard:
```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

### 2. Esecuzione Test di Concorrenza (Stress Test)
Per validare la gestione del lock transazionale SQLite in scenari multi-processo:
```bash
python3 -m unittest tests/test_concurrency.py
```
*Criterio di successo:* Zero errori del tipo `sqlite3.OperationalError: database is locked`.

### 3. Verifica Assenza Leak e Dati Sensibili
Eseguire il test di validazione del template:
```bash
python3 -m unittest tests/test_vault_template.py
```

## 🚨 Troubleshooting
- **Errore `database is locked`:**
  Verificare che il `busy_timeout` sia impostato ad almeno 5000ms nel modulo storage e che la connessione sia stata aperta con `PRAGMA journal_mode=WAL`.
- **Rilascio lockfile orfano:**
  Se un worker è stato terminato con `SIGKILL`, rimuovere il file `.lock` temporaneo generato nella cartella scratch di runtime.
