"""Repository Integrity, Documentation, Disclaimer, and License Test Suite.

Validates:
1. Existence and non-emptiness of README.md, DISCLAIMER.md, LICENSE, .gitignore, setup.py.
2. README.md strictly adheres to the 5 approved sections:
   - Nome con breve descrizione
   - What Problem It Solves
   - Installation (60 Seconds) with Steps 1-3 and toggles/subsections for Claude, Cursor, Antigravity
   - How AI-BRAIN is Structured (PARA tree + 4 cognitive layers + SQLite FTS5 engine)
   - Privacy & Security (100% local, zero cloud, secret sanitizer, git leak protection, MIT)
3. README.md contains ZERO icons, emojis, or external image badges.
4. LICENSE is a valid MIT license with Copyright (c) 2026 Roberto Montella.
5. DISCLAIMER.md provides complete legal disclaimer, AS IS clause, and backup warnings.
6. .gitignore correctly ignores SQLite databases, WAL, locks, Python caches, and .DS_Store.
7. Absolute absence of personal paths (/Users/...) or author usernames across all repo files.
8. Absolute absence of junk files (.DS_Store, compiled .pyc, __pycache__).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that must exist at the root of the repository
REQUIRED_ROOT_FILES = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "DISCLAIMER.md",
    REPO_ROOT / "LICENSE",
    REPO_ROOT / ".gitignore",
    REPO_ROOT / "setup.py",
    REPO_ROOT / "CONTRIBUTING.md",
]

# Patterns forbidden in production/release code, documentation, and templates
FORBIDDEN_LEAK_PATTERNS = [
    re.compile(r"/Users/\w+", re.IGNORECASE),
    re.compile(r"robertomontella", re.IGNORECASE),
    re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"),
    re.compile(r"\bghp_[a-zA-Z0-9_\-]{20,}\b"),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE),
]

# Emoji and pictograph regex ranges
EMOJI_PATTERN = re.compile(
    r"[\U0001F600-\U0001F64F"  # Emoticons
    r"\U0001F300-\U0001F5FF"  # Misc Symbols & Pictographs
    r"\U0001F680-\U0001F6FF"  # Transport & Map
    r"\U0001F700-\U0001F77F"  # Alchemical Symbols
    r"\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    r"\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    r"\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    r"\U0001FA00-\U0001FA6F"  # Chess Symbols
    r"\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    r"\u2600-\u26FF"          # Misc Symbols
    r"\u2700-\u27BF"          # Dingbats
    r"]",
    re.UNICODE,
)

# Badge pattern: shields.io, badgen, img.shields, etc. or markdown image badge links
BADGE_PATTERN = re.compile(
    r"(shields\.io|badgen\.net|badge\.svg|travis-ci|codecov\.io|github\.com/.+/actions/workflows/.+/badge\.svg)",
    re.IGNORECASE,
)


class TestRepositoryIntegrity(unittest.TestCase):
    """Test suite validating production documentation, licensing, security, and cleanliness."""

    def test_required_root_files_exist_and_non_empty(self) -> None:
        """Verifica che tutti i file obbligatori alla radice del repository esistano e non siano vuoti."""
        for file_path in REQUIRED_ROOT_FILES:
            self.assertTrue(
                file_path.is_file(),
                f"File obbligatorio mancante: {file_path.relative_to(REPO_ROOT)}",
            )
            content = file_path.read_text(encoding="utf-8").strip()
            self.assertGreater(
                len(content),
                20,
                f"File troppo breve o vuoto: {file_path.relative_to(REPO_ROOT)}",
            )

    def test_readme_contains_five_approved_sections(self) -> None:
        """Verifica la presenza rigorosa delle 5 sezioni richieste dall'utente nel README.md."""
        readme_file = REPO_ROOT / "README.md"
        self.assertTrue(readme_file.is_file(), "README.md non trovato")
        content = readme_file.read_text(encoding="utf-8")

        # 1. Nome con breve descrizione
        self.assertTrue(
            re.search(r"^#\s+AI-BRAIN", content, re.MULTILINE),
            "Sezione 1 mancante: Titolo con nome 'AI-BRAIN' all'inizio del file",
        )

        # 2. What Problem It Solves
        self.assertTrue(
            re.search(r"^##\s+What Problem It Solves", content, re.MULTILINE),
            "Sezione 2 mancante: '## What Problem It Solves'",
        )

        # 3. Installation (60 Seconds)
        self.assertTrue(
            re.search(r"^##\s+Installation \(60 Seconds\)", content, re.MULTILINE),
            "Sezione 3 mancante: '## Installation (60 Seconds)'",
        )
        self.assertTrue(
            re.search(r"Step 1", content, re.IGNORECASE)
            and re.search(r"Step 2", content, re.IGNORECASE)
            and re.search(r"Step 3", content, re.IGNORECASE),
            "La sezione Installation deve contenere Step 1, Step 2 e Step 3",
        )
        self.assertTrue(
            "Claude" in content and "Cursor" in content and "Antigravity" in content,
            "La sezione Installation deve documentare Claude, Cursor e Antigravity",
        )

        # 4. How AI-BRAIN is Structured
        self.assertTrue(
            re.search(r"^##\s+How AI-BRAIN is Structured", content, re.MULTILINE),
            "Sezione 4 mancante: '## How AI-BRAIN is Structured'",
        )
        # Verifica PARA + 4 layer cognitivi + SQLite FTS5
        para_folders = ["00_Inbox", "10_Projects", "20_Areas", "30_Resources", "40_Archive"]
        for folder in para_folders:
            self.assertIn(
                folder,
                content,
                f"Struttura PARA incompleta nel README: cartella '{folder}' non menzionata",
            )

        cognitive_layers = ["Episodic", "Semantic", "Procedural", "Working"]
        for layer in cognitive_layers:
            self.assertIn(
                layer,
                content,
                f"4 Layer cognitivi incompleti nel README: '{layer}' non menzionato",
            )

        self.assertTrue(
            re.search(r"FTS5", content, re.IGNORECASE) and re.search(r"SQLite", content, re.IGNORECASE),
            "Motore SQLite FTS5 non descritto nella sezione How AI-BRAIN is Structured",
        )
        # Verifica che l'immagine di architettura sia referenziata ed esista su disco
        self.assertTrue(
            re.search(r"!\[.*AI-BRAIN Architecture.*\]\(assets/architecture\.(png|svg)\)", content),
            "Immagine dell'architettura mancante nella sezione How AI-BRAIN is Structured",
        )
        self.assertTrue(
            (REPO_ROOT / "assets" / "architecture.png").is_file(),
            "File immagine assets/architecture.png non trovato su disco",
        )

        # 5. Privacy & Security
        self.assertTrue(
            re.search(r"^##\s+Privacy & Security", content, re.MULTILINE),
            "Sezione 5 mancante: '## Privacy & Security'",
        )
        self.assertTrue(
            re.search(r"100%\s+local", content, re.IGNORECASE) or re.search(r"local", content, re.IGNORECASE),
            "Privacy & Security deve specificare architettura 100% locale",
        )
        self.assertTrue(
            re.search(r"zero cloud", content, re.IGNORECASE) or re.search(r"no cloud", content, re.IGNORECASE),
            "Privacy & Security deve specificare zero cloud",
        )
        self.assertTrue(
            re.search(r"sanitizer", content, re.IGNORECASE) or re.search(r"anti-leak", content, re.IGNORECASE),
            "Privacy & Security deve descrivere il secret sanitizer / anti-leak",
        )
        self.assertTrue(
            re.search(r"MIT", content),
            "Privacy & Security deve menzionare la licenza MIT",
        )

    def test_readme_has_zero_emojis_icons_or_badges(self) -> None:
        """Verifica il DIVIETO ASSOLUTO di emoji, icone e badge grafici in README.md."""
        readme_file = REPO_ROOT / "README.md"
        self.assertTrue(readme_file.is_file(), "README.md non trovato")
        content = readme_file.read_text(encoding="utf-8")

        # Controllo emoji
        emojis_found = EMOJI_PATTERN.findall(content)
        self.assertEqual(
            len(emojis_found),
            0,
            f"Trovate emoji vietate in README.md: {set(emojis_found)}",
        )

        # Controllo badge
        badges_found = BADGE_PATTERN.findall(content)
        self.assertEqual(
            len(badges_found),
            0,
            f"Trovati badge vietati in README.md: {badges_found}",
        )

        # Controllo immagini generiche di badge
        self.assertFalse(
            re.search(r"!\[.*badge.*\]\(.*\)", content, re.IGNORECASE),
            "Trovata immagine badge in README.md",
        )

    def test_license_file_mit_and_copyright(self) -> None:
        """Verifica che LICENSE sia una licenza MIT valida con copyright di Roberto Montella."""
        license_file = REPO_ROOT / "LICENSE"
        self.assertTrue(license_file.is_file(), "LICENSE non trovato")
        content = license_file.read_text(encoding="utf-8")

        self.assertIn("MIT License", content)
        self.assertTrue(
            re.search(r"Copyright\s+(\(c\)\s+)?2026\s+Roberto\s+Montella", content, re.IGNORECASE),
            "Copyright mancante o non corrispondente a 'Roberto Montella 2026'",
        )
        self.assertIn("Permission is hereby granted, free of charge", content)
        self.assertIn("THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND", content)

    def test_disclaimer_content_and_protection(self) -> None:
        """Verifica che DISCLAIMER.md contenga liberatoria legale completa e avvertenze di backup."""
        disclaimer_file = REPO_ROOT / "DISCLAIMER.md"
        self.assertTrue(disclaimer_file.is_file(), "DISCLAIMER.md non trovato")
        content = disclaimer_file.read_text(encoding="utf-8")

        self.assertTrue(
            "AS IS" in content,
            "DISCLAIMER.md deve contenere la clausola 'AS IS'",
        )
        self.assertTrue(
            re.search(r"no\s+warranty|without\s+warranty", content, re.IGNORECASE),
            "DISCLAIMER.md deve dichiarare l'assenza di garanzie",
        )
        self.assertTrue(
            re.search(r"backup", content, re.IGNORECASE),
            "DISCLAIMER.md deve raccomandare esplicitamente i backup dei dati",
        )
        self.assertTrue(
            re.search(r"liability|limitation\s+of\s+liability", content, re.IGNORECASE),
            "DISCLAIMER.md deve limitare la responsabilità dell'autore",
        )

    def test_gitignore_covers_sqlite_locks_cache_and_os_files(self) -> None:
        """Verifica che .gitignore escluda sqlite, file lock, bytecode Python e file di sistema."""
        gitignore_file = REPO_ROOT / ".gitignore"
        self.assertTrue(gitignore_file.is_file(), ".gitignore non trovato")
        lines = [line.strip() for line in gitignore_file.read_text(encoding="utf-8").splitlines()]
        non_comment_lines = [l for l in lines if l and not l.startswith("#")]

        # Verifica sqlite
        has_sqlite = any("*.sqlite" in l or "*.db" in l for l in non_comment_lines)
        self.assertTrue(has_sqlite, ".gitignore non esclude i file di database SQLite (*.sqlite)")
        has_wal = any("*.sqlite-wal" in l or "*-wal" in l for l in non_comment_lines)
        self.assertTrue(has_wal, ".gitignore non esclude i file SQLite WAL")

        # Verifica locks
        has_locks = any("*.lock" in l for l in non_comment_lines)
        self.assertTrue(has_locks, ".gitignore non esclude i file lock (*.lock)")

        # Verifica python cache
        has_pycache = any("__pycache__" in l for l in non_comment_lines)
        self.assertTrue(has_pycache, ".gitignore non esclude __pycache__")
        has_pyc = any("*.pyc" in l for l in non_comment_lines)
        self.assertTrue(has_pyc, ".gitignore non esclude *.pyc")

        # Verifica .DS_Store
        has_ds_store = any(".DS_Store" in l for l in non_comment_lines)
        self.assertTrue(has_ds_store, ".gitignore non esclude .DS_Store")

    def test_no_personal_paths_or_leak_in_production_files(self) -> None:
        """Effettua una scansione globale su codice, template e documentazione escludendo leak personali."""
        target_dirs = [
            REPO_ROOT / "vault-template",
            REPO_ROOT / "mcp-server",
            REPO_ROOT / "agents",
            REPO_ROOT / "tools",
            REPO_ROOT / ".github",
        ]
        target_files = [
            REPO_ROOT / "setup.py",
            REPO_ROOT / "README.md",
            REPO_ROOT / "DISCLAIMER.md",
            REPO_ROOT / ".gitignore",
            REPO_ROOT / "CONTRIBUTING.md",
        ]

        files_to_check: list[Path] = []
        for d in target_dirs:
            if d.is_dir():
                files_to_check.extend([p for p in d.rglob("*") if p.is_file()])
        for f in target_files:
            if f.is_file():
                files_to_check.append(f)

        violations: list[str] = []
        for file_path in files_to_check:
            # Salta i file binari se presenti
            try:
                text = file_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue

            for pattern in FORBIDDEN_LEAK_PATTERNS:
                matches = pattern.findall(text)
                if matches:
                    rel_path = file_path.relative_to(REPO_ROOT)
                    violations.append(f"{rel_path}: rilevato pattern vietato {pattern.pattern} -> {matches[:3]}")

        self.assertEqual(
            len(violations),
            0,
            f"Trovate violazioni di privacy/leak nei file di produzione:\n" + "\n".join(violations),
        )

    def test_no_garbage_files_in_repository(self) -> None:
        """Verifica che l'intero albero del repository sia privo di file spazzatura (.DS_Store, *.pyc, db committati)."""
        garbage_patterns = [
            "**/.DS_Store",
            "**/*.pyc",
            "**/*.pyo",
            "**/__pycache__",
        ]
        found_garbage: list[str] = []
        for pattern in garbage_patterns:
            for p in REPO_ROOT.glob(pattern):
                found_garbage.append(str(p.relative_to(REPO_ROOT)))

        self.assertEqual(
            len(found_garbage),
            0,
            f"Trovati file spazzatura nella repository: {found_garbage}",
        )


if __name__ == "__main__":
    unittest.main()
