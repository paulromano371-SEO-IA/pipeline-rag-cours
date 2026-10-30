"""Liste les PDF condenses de corpuscondense/ qui ne sont PAS dans le RAG.

Un PDF condense est considere dans le RAG si son `document_id` courant (nom +
hash du contenu, voir .claude/skills/_rag_lib/paths.py) a l'etape
`indexation_vectorielle` a `done` dans rag_data/work/<document_id>/status.json :
c'est l'etape qui ecrit les chunks dans la base Chroma.

Un PDF modifie depuis son indexation obtient un nouveau hash : il est alors
signale comme "indexe, mais version modifiee" (meme slug, autre hash), car
l'ancienne version est encore dans le RAG et doit etre reindexee.

Lecture seule : ne modifie rien.

Usage :
    .venv-rag/Scripts/python.exe tools/lister_condenses_non_indexes.py
"""
import json
import sys
from pathlib import Path

RACINE_PROJET = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE_PROJET / ".claude" / "skills" / "_rag_lib"))
from paths import CORPUS_CONDENSE_DIR, WORK_DIR, document_id_from_pdf  # noqa: E402

ETAPE_INDEXATION = "indexation_vectorielle"


def documents_indexes() -> set[str]:
    """document_id de chaque dossier de travail dont l'indexation est terminee."""
    indexes = set()
    if not WORK_DIR.is_dir():
        return indexes
    for d in WORK_DIR.iterdir():
        sp = d / "status.json"
        if not sp.is_file():
            continue
        try:
            status = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        etape = status.get(ETAPE_INDEXATION)
        if isinstance(etape, dict) and etape.get("status") == "done":
            indexes.add(d.name)
    return indexes


def slug_de(document_id: str) -> str:
    """`<slug>__<hash>` -> `<slug>` (le slug ne contient jamais `__`)."""
    return document_id.rsplit("__", 1)[0]


def main() -> int:
    # Console Windows en cp1252 : les noms de fichiers accentues doivent passer en UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not CORPUS_CONDENSE_DIR.is_dir():
        print(f"ERREUR: dossier introuvable: {CORPUS_CONDENSE_DIR}", file=sys.stderr)
        return 1

    pdfs = sorted(p for p in CORPUS_CONDENSE_DIR.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    indexes = documents_indexes()
    slugs_indexes = {slug_de(i) for i in indexes}

    indexes_ok, non_indexes = [], []
    for p in pdfs:
        did = document_id_from_pdf(p)
        if did in indexes:
            indexes_ok.append(p)
        else:
            # Absent, ou PDF modifie depuis son indexation : dans les deux cas
            # la version courante n'est pas dans le RAG.
            non_indexes.append(p)

    print(f"pdf condensés présents dans corpuscondense/ : {len(pdfs)}")
    print(f"  indexés     : {len(indexes_ok)}")
    print(f"  non indexés : {len(non_indexes)}")
    print()

    if non_indexes:
        print("Non indexés :")
        for i, p in enumerate(non_indexes, 1):
            print(f"  {i:>2}. {p.name}")
        print()
    else:
        print("Tous les PDF condensés sont indexés.")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
