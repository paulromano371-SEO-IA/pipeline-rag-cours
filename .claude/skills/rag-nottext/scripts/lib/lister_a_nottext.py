"""Liste les pdf a traiter pour /rag-nottext (commande sans argument).

Usage:
    python lister_a_nottext.py

Pour chaque PDF condense de corpuscondense/, affiche le pdf s'il est pret
pour /rag-nottext, c'est-a-dire si toutes ces conditions sont vraies :
    - rag_data/work/<document_id>/status.json existe
    - extraction.status de status.json est "done"
    - extraction.metadata.verdict de status.json est "ok"
    - nottext.status de status.json n'est pas "done"
(Un pdf sans status.json, ou dont l'extraction n'est pas done/ok, est exclu.)

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


def _est_a_traiter(work_dir: Path) -> bool:
    """Vrai si l'extraction est exploitable (done + ok) et que nottext n'est pas encore done."""
    if not (work_dir / "status.json").exists():
        return False
    if not status_lib.is_done(work_dir, "extraction"):
        return False
    verdict = status_lib.load_status(work_dir).get("extraction", {}).get("metadata", {}).get("verdict")
    if verdict != "ok":
        return False
    return not status_lib.is_done(work_dir, "nottext")


def main() -> int:
    # Console Windows en cp1252 par defaut : les accents des noms de fichiers sortiraient en "�".
    sys.stdout.reconfigure(encoding="utf-8")
    if not paths.CORPUS_CONDENSE_DIR.is_dir():
        print(f"ERREUR: dossier introuvable: {paths.CORPUS_CONDENSE_DIR}", file=sys.stderr)
        return 1

    a_traiter = []
    for condense_pdf in sorted(paths.CORPUS_CONDENSE_DIR.glob("*.pdf")):
        document_id = paths.document_id_from_pdf(condense_pdf)
        if _est_a_traiter(paths.work_dir_for(document_id)):
            a_traiter.append((condense_pdf.name, document_id))

    if not a_traiter:
        print("Aucun livre a traiter.")
        return 0

    print(f"Livres a traiter : {len(a_traiter)}")
    for numero, (nom, document_id) in enumerate(a_traiter, start=1):
        print(f"{numero}. {nom}")
        print(f"   document_id: {document_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
