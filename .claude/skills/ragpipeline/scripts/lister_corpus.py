"""Liste les PDF sources de corpusdedepart/ qui n'ont PAS encore ete traites de
bout en bout par /ragpipeline (sortie de /ragpipeline sans argument).

Usage (sans argument) :
    .venv-rag/Scripts/python.exe lister_corpus.py

Un PDF source est considere traite de bout en bout si :
  - son PDF condense corpuscondense/<slug>.pdf existe (slug = slugifier du nom) ;
  - un dossier rag_data/work/<document_id>/ pointe vers ce PDF condense (meta.json)
    et a son etape `graphe` a `done` (derniere etape du pipeline).

Seuls les PDF directement a la racine de corpusdedepart/ sont consideres (le
sous-dossier done/ et tout autre sous-dossier sont ignores).
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

RACINE_PROJET = Path(__file__).resolve().parents[4]
CORPUS_DEPART = RACINE_PROJET / "corpusdedepart"
CORPUS_CONDENSE = RACINE_PROJET / "corpuscondense"
WORK_DIR = RACINE_PROJET / "rag_data" / "work"


def slugifier(nom_livre: str) -> str:
    """Minuscules, sans accents, espaces/ponctuation -> '_' (meme regle que
    /cours-condense pour nommer corpuscondense/<slug>.pdf)."""
    sans_accents = unicodedata.normalize("NFKD", nom_livre)
    sans_accents = "".join(c for c in sans_accents if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "_", sans_accents.lower())
    return slug.strip("_")


def pdfs_traites_de_bout_en_bout() -> set[str]:
    """Noms des PDF condenses (ex. 'livre_2025.pdf') dont le pipeline est termine."""
    traites = set()
    if not WORK_DIR.is_dir():
        return traites
    for d in WORK_DIR.iterdir():
        sp, mp = d / "status.json", d / "meta.json"
        if not (sp.exists() and mp.exists()):
            continue
        try:
            status = json.loads(sp.read_text(encoding="utf-8"))
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        etape = status.get("graphe")
        if isinstance(etape, dict):
            etape = etape.get("status")
        if etape == "done" and meta.get("source_pdf"):
            traites.add(Path(meta["source_pdf"]).name)
    return traites


def main() -> int:
    # Console Windows en cp1252 : les noms de fichiers accentues doivent passer en UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not CORPUS_DEPART.is_dir():
        print(f"ERREUR: dossier introuvable: {CORPUS_DEPART}", file=sys.stderr)
        return 1

    sources = sorted(p for p in CORPUS_DEPART.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    traites = pdfs_traites_de_bout_en_bout()

    a_traiter = [p for p in sources if f"{slugifier(p.stem)}.pdf" not in traites]
    deja = len(sources) - len(a_traiter)

    print(f"PDF non traites de bout en bout : {len(a_traiter)} / {len(sources)} "
          f"({deja} deja traites)")
    print()
    if not a_traiter:
        print("Tous les PDF ont ete traites de bout en bout.")
        return 0
    for i, p in enumerate(a_traiter, 1):
        print(f"  {i:>2}. {p.name}")
    print()
    print("Usage : /ragpipeline corpusdedepart/<livre>.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
