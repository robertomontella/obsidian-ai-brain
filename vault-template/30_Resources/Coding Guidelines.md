---
title: "Coding Guidelines & Best Practices"
type: resource
status: active
priority: Media
tags:
  - resource
  - standards
  - python
  - clean-code
created_at: 2026-11-23
updated_at: 2026-11-23
---

# Coding Guidelines & Best Practices

Linee guida per lo sviluppo di moduli software ad alta affidabilità e codice manutenibile.

## 🐍 Python Development Standards
- **Standard Library First:**
  - Prima di introdurre una dipendenza esterna da PyPI, verificare se i moduli standard (`sqlite3`, `json`, `urllib`, `dataclasses`, `typing`, `unittest`) soddisfano il requisito.
  - Ridurre al minimo la superficie d'attacco delle dipendenze per tool infrastrutturali e CLI.
- **Type Annotations:**
  - Usare type hints espliciti per parametri di funzioni e valori di ritorno.
  - Utilizzare `Optional`, `Union`, `Dict`, `List` e i moderni generics (`list[str]`, `dict[str, Any]`).
- **Gestione delle Risorse:**
  - Utilizzare sempre context manager (`with open(...)`, `with sqlite3.connect(...)`) per garantire il rilascio di file descriptor e handle di database.
- **Testing & TDD:**
  - Coprire ogni modulo con unit test automatizzati basati su `unittest`.
  - Verificare scenari limite (input vuoti, caratteri speciali, concorrenza, timeout).
