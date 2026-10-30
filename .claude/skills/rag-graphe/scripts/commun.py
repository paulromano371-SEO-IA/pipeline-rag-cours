"""Socle commun de /rag-graphe : modèle Claude, appels `claude -p`, prompts, chemins.

Utilisé par les deux étapes :
- étape 1 (`etape1_resolution.py`) : résolution des mentions d'un document dans le graphe partagé ;
- étape 2 (`etape2_consolidation.py`) : fusion des doublons du graphe partagé.

Les appels Claude passent par `_claude_code_client_systemfile` : le `system` (consignes, identiques
d'un appel à l'autre) est écrit dans un fichier, et seul le message change d'un appel à l'autre,
pour que le cache de prompt se partage entre les appels d'une même étape.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import json
import threading
from pathlib import Path

from _claude_code_client_graph import ClaudeCodeCallError, run_claude_code, strip_code_fence  # noqa: F401

MODEL = "claude-haiku-5-5"
EFFORT = "medium"
TIMEOUT_S = 120  # un jugement de paire est court : au-delà, l'appel est tenu pour échoué

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROGRESS_FILENAME = "graphe_progress.json"


def load_prompt(name: str) -> str:
    """Texte du prompt de l'étape (le `system` de ses appels Claude)."""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


_verrou = threading.Lock()  # les arbitrages de l'étape 1 tournent en threads
_nb_appels = 0


def appeler_claude(system: str, user_text: str) -> str:
    """Un appel `claude -p` sans outils. Lève ClaudeCodeCallError si l'appel échoue.

    Chaque tentative est comptée, réussie ou non (voir `nombre_appels`)."""
    global _nb_appels
    with _verrou:
        _nb_appels += 1
    return run_claude_code(system, user_text, model=MODEL, timeout=TIMEOUT_S, effort=EFFORT)


def nombre_appels() -> int:
    """Nombre d'appels `claude -p` lancés par ce processus depuis son démarrage."""
    return _nb_appels


def document_id_of(work_dir: Path) -> str:
    meta_path = work_dir / "meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))["document_id"]
    return work_dir.name


def progress_path(work_dir: Path) -> Path:
    """Fichier de reprise de l'étape 1 (chunks déjà liés) dans le dossier de travail du document."""
    return work_dir / PROGRESS_FILENAME
