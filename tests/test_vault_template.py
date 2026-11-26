import json
import os
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VAULT_TEMPLATE = REPO_ROOT / "vault-template"

REQUIRED_DIRS = [
    "00_Inbox",
    "10_Projects",
    "20_Areas",
    "30_Resources",
    "40_Archive",
    "50_Memory",
    "90_System",
]

REQUIRED_MEMORY_LAYERS = [
    "Episodic",
    "Semantic",
    "Procedural",
    "Working",
]

REQUIRED_FILES = [
    VAULT_TEMPLATE / "00_Inbox" / "README.md",
    VAULT_TEMPLATE / "10_Projects" / "TaskFlow.md",
    VAULT_TEMPLATE / "20_Areas" / "Backend Architecture.md",
    VAULT_TEMPLATE / "30_Resources" / "Coding Guidelines.md",
    VAULT_TEMPLATE / "40_Archive" / "README.md",
    VAULT_TEMPLATE / "50_Memory" / "Episodic" / "2026-11-23 TaskFlow SQLite Architecture.md",
    VAULT_TEMPLATE / "50_Memory" / "Semantic" / "TaskFlow Domain Model.md",
    VAULT_TEMPLATE / "50_Memory" / "Procedural" / "SOP - Running TaskFlow Tests.md",
    VAULT_TEMPLATE / "50_Memory" / "Working" / "TaskFlow Active Sprint.md",
    VAULT_TEMPLATE / "90_System" / "MEMORY_SCHEMA.md",
    VAULT_TEMPLATE / "Neural-Map.canvas",
]

FORBIDDEN_PATTERNS = [
    re.compile(r"/Users/\w+", re.IGNORECASE),
    re.compile(r"robertomontella", re.IGNORECASE),
    re.compile(r"montella", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"password\s*[:=]\s*['\"][^'\"]+['\"]", re.IGNORECASE),
]


class TestVaultTemplate(unittest.TestCase):
    def test_vault_root_exists(self):
        """Verifica che la cartella vault-template esista."""
        self.assertTrue(
            VAULT_TEMPLATE.exists() and VAULT_TEMPLATE.is_dir(),
            f"Vault template directory not found at {VAULT_TEMPLATE}",
        )

    def test_required_top_level_directories_exist(self):
        """Verifica che tutte le cartelle PARA + 90_System esistano."""
        for d in REQUIRED_DIRS:
            dir_path = VAULT_TEMPLATE / d
            self.assertTrue(
                dir_path.exists() and dir_path.is_dir(),
                f"Required directory '{d}' missing in vault-template",
            )

    def test_cognitive_memory_layers_exist_and_populated(self):
        """Verifica che i 4 layer cognitivi esistano e abbiano almeno un file .md valido."""
        memory_dir = VAULT_TEMPLATE / "50_Memory"
        self.assertTrue(
            memory_dir.exists() and memory_dir.is_dir(),
            "50_Memory directory missing",
        )

        for layer in REQUIRED_MEMORY_LAYERS:
            layer_dir = memory_dir / layer
            self.assertTrue(
                layer_dir.exists() and layer_dir.is_dir(),
                f"Cognitive memory layer '{layer}' missing in 50_Memory",
            )
            md_files = list(layer_dir.glob("*.md"))
            self.assertGreater(
                len(md_files),
                0,
                f"Memory layer '{layer}' must contain at least one .md file",
            )
            for md_file in md_files:
                self.assertGreater(
                    md_file.stat().st_size,
                    20,
                    f"Memory note '{md_file.name}' in '{layer}' is empty or too short",
                )

    def test_required_mock_and_readme_files_exist(self):
        """Verifica la presenza di tutti i file di template e mock specificati nel brief."""
        for file_path in REQUIRED_FILES:
            self.assertTrue(
                file_path.exists() and file_path.is_file(),
                f"Required file '{file_path.relative_to(REPO_ROOT)}' does not exist",
            )
            self.assertGreater(
                file_path.stat().st_size,
                30,
                f"File '{file_path.relative_to(REPO_ROOT)}' is unexpectedly empty",
            )

    def test_sanitization_no_personal_data_or_author_paths(self):
        """Scansiona tutti i file del template per escludere percorsi utente, nomi privati e token."""
        if not VAULT_TEMPLATE.exists():
            self.fail("vault-template does not exist to scan")

        scanned_count = 0
        for root, _, files in os.walk(VAULT_TEMPLATE):
            for file_name in files:
                file_path = Path(root) / file_name
                # Leggi solo i file di testo / markdown
                try:
                    content = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

                scanned_count += 1
                for pattern in FORBIDDEN_PATTERNS:
                    match = pattern.search(content)
                    self.assertIsNone(
                        match,
                        f"Forbidden pattern '{pattern.pattern}' found in "
                        f"'{file_path.relative_to(REPO_ROOT)}': '{match.group(0) if match else ''}'",
                    )

        self.assertGreater(scanned_count, 0, "No template files were scanned")

    def test_neural_map_canvas_structure_and_connectivity(self):
        """Verifica che Neural-Map.canvas esista, sia JSON valido e colleghi TaskFlow, MCP e 4 layer cognitivi."""
        canvas_path = VAULT_TEMPLATE / "Neural-Map.canvas"
        self.assertTrue(canvas_path.is_file(), f"Neural-Map.canvas non trovato in {VAULT_TEMPLATE}")

        try:
            data = json.loads(canvas_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.fail(f"Neural-Map.canvas contiene JSON non valido: {exc}")

        self.assertIsInstance(data, dict, "La radice del Canvas deve essere un oggetto JSON")
        self.assertIn("nodes", data, "Il canvas deve contenere la lista 'nodes'")
        self.assertIn("edges", data, "Il canvas deve contenere la lista 'edges'")

        nodes = data["nodes"]
        edges = data["edges"]
        self.assertGreaterEqual(len(nodes), 6, "Il canvas deve contenere almeno 6 nodi (MCP, TaskFlow, 4 memory layer)")
        self.assertGreaterEqual(len(edges), 5, "Il canvas deve contenere almeno 5 archi di collegamento")

        node_ids: set[str] = set()
        node_texts: list[str] = []

        for node in nodes:
            self.assertIn("id", node, "Ogni nodo deve avere un 'id'")
            nid = node["id"]
            self.assertNotIn(nid, node_ids, f"ID nodo duplicato: {nid}")
            node_ids.add(nid)

            self.assertIn("type", node, f"Nodo {nid} privo di 'type'")
            self.assertIn("color", node, f"Nodo {nid} privo di 'color'")
            self.assertTrue(bool(node["color"]), f"Nodo {nid} ha color vuoto")

            # Verifica label o testo descrittivo
            desc = ""
            if "label" in node and node["label"]:
                desc += " " + str(node["label"])
            if "text" in node and node["text"]:
                desc += " " + str(node["text"])
            if "file" in node and node["file"]:
                desc += " " + str(node["file"])
                ref_file = VAULT_TEMPLATE / node["file"]
                self.assertTrue(ref_file.exists(), f"File referenziato nel nodo {nid} inesistente: {node['file']}")

            self.assertTrue(bool(desc.strip()), f"Nodo {nid} privo di etichetta o testo descrittivo")
            node_texts.append(desc.lower())

        # Verifica archi
        for edge in edges:
            self.assertIn("id", edge, "Ogni arco deve avere un 'id'")
            eid = edge["id"]
            self.assertIn("fromNode", edge, f"Arco {eid} privo di 'fromNode'")
            self.assertIn("toNode", edge, f"Arco {eid} privo di 'toNode'")
            self.assertIn(edge["fromNode"], node_ids, f"Arco {eid} fromNode inesistente: {edge['fromNode']}")
            self.assertIn(edge["toNode"], node_ids, f"Arco {eid} toNode inesistente: {edge['toNode']}")

            self.assertIn("color", edge, f"Arco {eid} privo di 'color'")
            self.assertTrue(bool(edge["color"]), f"Arco {eid} ha color vuoto")
            self.assertIn("label", edge, f"Arco {eid} privo di 'label'")
            self.assertTrue(bool(edge["label"]), f"Arco {eid} ha label vuoto")

        # Verifica entita chiave collegate
        all_text = " ".join(node_texts)
        self.assertTrue("mcp" in all_text or "runtime" in all_text, "Neural-Map.canvas deve includere il nodo MCP Runtime")
        self.assertTrue("taskflow" in all_text, "Neural-Map.canvas deve collegare il progetto TaskFlow")
        for layer in ["episodic", "semantic", "procedural", "working"]:
            self.assertTrue(layer in all_text, f"Neural-Map.canvas deve includere il layer di memoria '{layer}'")


if __name__ == "__main__":
    unittest.main()
