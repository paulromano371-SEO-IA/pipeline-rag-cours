"""Commun aux étapes de /rag-extraction : chemins, modules partagés, lecture/écriture JSON.

Chaque étape (etape1, etape2, etape3) importe ce fichier. Les modules propres à
/rag-extraction sont dans scripts/lib/, ceux partagés entre skills dans _rag_lib/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_RACINE = next(p for p in _SCRIPTS.parents if (p / ".claude" / "skills" / "_rag_lib").is_dir())
sys.path.insert(0, str(_RACINE / ".claude" / "skills" / "_rag_lib"))
sys.path.insert(0, str(_SCRIPTS / "lib"))  # en dernier : prioritaire en cas de collision de nom

import venv_guard  # noqa: F401  (impose l'interpréteur .venv-rag)
import paths  # noqa: E402

# Sortie console en UTF-8 : sous Windows elle part en cp1252, ce qui abîme les accents.
for _flux in (sys.stdout, sys.stderr):
    _flux.reconfigure(encoding="utf-8", errors="replace")

# Fichiers échangés entre les étapes, dans le dossier de travail du document.
ETAPE1_JSON = "etape1_extraction.json"
ETAPE2_JSON = "etape2_verification.json"


def resoudre(pdf_arg: str) -> tuple[Path, str, Path, Path]:
    """Retourne (pdf, document_id, dossier de travail, dossier de /cours-condense)."""
    pdf_path = Path(pdf_arg).resolve()
    document_id = paths.document_id_from_pdf(pdf_path)
    work_dir = paths.work_dir_for(document_id)
    courscondense_dir = paths.courscondense_dir_for_condense_pdf(pdf_path)
    return pdf_path, document_id, work_dir, courscondense_dir


def ecrire_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def lire_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
