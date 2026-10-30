"""Liste les pdfs a chunker pour /rag-chunking (commande sans argument).

Usage:
    python lister_a_chunker.py

Pour chaque PDF condense de corpuscondense/, affiche le pdf s'il est pret
pour /rag-chunking, c'est-a-dire si toutes ces conditions sont vraies :
    - rag_data/work/<document_id>/status.json existe
    - nottext.status de status.json est "done"
    - chunking.status de status.json n'est pas "done"
(Un pdf sans status.json, ou dont nottext n'est pas done, est exclu.)

Ne lance aucun traitement : lecture seule.
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))  # paths/status (partages entre etapes)

import paths
import status as status_lib


def _est_a_chunker(work_dir: Path) -> bool:
    """Vrai si nottext est done et que chunking n'est pas encore done."""
    if not (work_dir / "status.json").exists():
        return False
    if not status_lib.is_done(work_dir, "nottext"):
        return False
    return not status_lib.is_done(work_dir, "chunking")


def main() -> int:
    # Console Windows en cp1252 par defaut : les accents des noms de fichiers sortiraient en "�".
    sys.stdout.reconfigure(encoding="utf-8")
    if not paths.CORPUS_CONDENSE_DIR.is_dir():
        print(f"ERREUR: dossier introuvable: {paths.CORPUS_CONDENSE_DIR}", file=sys.stderr)
        return 1

    a_chunker = []
    for condense_pdf in sorted(paths.CORPUS_CONDENSE_DIR.glob("*.pdf")):
        document_id = paths.document_id_from_pdf(condense_pdf)
        if _est_a_chunker(paths.work_dir_for(document_id)):
            a_chunker.append((condense_pdf.name, document_id))

    if not a_chunker:
        print("Aucun livre a chunker.")
        return 0

    print(f"Livres a chunker : {len(a_chunker)}")
    for numero, (nom, document_id) in enumerate(a_chunker, start=1):
        print(f"{numero}. {nom}")
        print(f"   document_id: {document_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
