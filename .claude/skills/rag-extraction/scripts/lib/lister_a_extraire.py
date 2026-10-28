"""Liste les livres a extraire pour /rag-extraction (commande sans argument).

Usage:
    python lister_a_extraire.py

Pour chaque PDF de corpusdedepart/ dont le condense existe dans
corpuscondense/<slug>.pdf, affiche le livre s'il n'a pas encore d'extraction
exploitable, c'est-a-dire si l'une de ces conditions est vraie :
    - rag_data/work/<document_id>/pivot.md est absent
    - rag_data/work/<document_id>/status.json est absent
    - extraction.status de status.json n'est pas "done"
    - extraction.metadata.verdict de status.json n'est pas "ok" (ou absent)

Ne lance aucune extraction : lecture seule.
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


def _raison_a_extraire(work_dir: Path) -> str | None:
    """Motif pour lequel le livre doit etre extrait, ou None s'il est a jour."""
    if not (work_dir / "pivot.md").exists():
        return "pivot.md absent"
    if not (work_dir / "status.json").exists():
        return "status.json absent"
    if not status_lib.is_done(work_dir, "extraction"):
        return "status non done"
    verdict = status_lib.load_status(work_dir).get("extraction", {}).get("metadata", {}).get("verdict")
    if verdict != "ok":
        return f"verdict {verdict!r}"
    return None


def main() -> int:
    # Console Windows en cp1252 par defaut : les accents des noms de fichiers sortiraient en "�".
    sys.stdout.reconfigure(encoding="utf-8")
    if not paths.CORPUS_SOURCE_DIR.is_dir():
        print(f"ERREUR: dossier introuvable: {paths.CORPUS_SOURCE_DIR}", file=sys.stderr)
        return 1

    a_extraire = []
    for source_pdf in sorted(paths.CORPUS_SOURCE_DIR.glob("*.pdf")):
        condense_pdf = paths.CORPUS_CONDENSE_DIR / f"{paths._slugify_livre(source_pdf.stem)}.pdf"
        if not condense_pdf.is_file():
            continue
        document_id = paths.document_id_from_pdf(condense_pdf)
        raison = _raison_a_extraire(paths.work_dir_for(document_id))
        if raison is not None:
            a_extraire.append((source_pdf.name, document_id, raison))

    if not a_extraire:
        print("Aucun livre a extraire.")
        return 0

    print(f"Livres a extraire : {len(a_extraire)}")
    for numero, (nom, document_id, raison) in enumerate(a_extraire, start=1):
        print(f"{numero}. {nom}")
        print(f"   document_id: {document_id}  |  {raison}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
