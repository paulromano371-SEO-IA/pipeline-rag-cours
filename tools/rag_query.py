"""Interroge le RAG avec `retrieval.retrieve()` et affiche le resultat de
facon lisible : regroupe par document puis par section (dans l'ordre de
premiere apparition), avec le type de contenu (code/formule/image/texte) et
la raison de chaque chunk (recherche directe / expansion de section /
expansion de concept). Outil d'exploration manuelle -- ne fait aucun appel
LLM, aucune ecriture, lecture seule sur les bases existantes.

Code/formule/image ne sont jamais tronques (contrairement a la prose, ou
`--full` est necessaire pour voir le texte complet) ; les images sont
signalees par leur chemin resolu (`images/...`), jamais affichees dans le
terminal (impossible) -- `--open-images` les ouvre dans la visionneuse par
defaut de Windows si demande explicitement.

Usage:
    python rag_query.py "<requete>" [--full] [--open-images]
                         [--search-top-k N] [--max-sections N]
                         [--result-cap N] [--relevance-margin F]
                         [--db-path ...] [--graph-db-path ...]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap
from pathlib import Path

# La console Windows par defaut (cp1252) ne sait pas encoder certains
# caracteres presents dans les formules LaTeX/texte source (grec, symboles
# mathematiques) -- plante sinon avec un UnicodeEncodeError des le premier
# chunk contenant un tel caractere (verifie empiriquement). errors="replace"
# plutot que de faire planter tout l'affichage pour un caractere isole.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_RAG_LIB = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "_rag_lib"
sys.path.insert(0, str(_RAG_LIB))

import paths  # noqa: E402
from retrieval import (  # noqa: E402
    DEFAULT_MAX_SECTIONS,
    DEFAULT_RELEVANCE_MARGIN,
    DEFAULT_RESULT_CAP,
    DEFAULT_SEARCH_TOP_K,
    RetrievedChunk,
    retrieve,
)


def _type_tags(r: RetrievedChunk) -> str:
    tags = []
    if r.has_code:
        tags.append("code")
    if r.has_formula:
        tags.append("formule")
    if r.has_image:
        tags.append("image")
    return "+".join(tags) if tags else "texte"


def _preview(text: str, width: int = 220) -> str:
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    return flat[: width].rstrip() + "..."


_IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _image_paths(document_id: str, text: str) -> list[Path]:
    """Chemins absolus des images referencees dans `text` (syntaxe Markdown
    `![...](chemin)`), resolus par rapport au dossier de travail du document
    -- c'est la ou `/rag-extraction`/`/rag-nottext` les ecrivent
    (`rag_data/work/<document_id>/images/...`)."""
    work_dir = paths.work_dir_for(document_id)
    return [work_dir / m for m in _IMAGE_MARKDOWN_RE.findall(text)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", type=str)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Affiche le texte complet des chunks de prose aussi (code/formule/image sont deja toujours affiches en entier, jamais tronques).",
    )
    parser.add_argument(
        "--open-images",
        action="store_true",
        help="Ouvre chaque image referencee dans la visionneuse par defaut de Windows (desactive par defaut : n'ouvre jamais de fenetre sans le demander explicitement).",
    )
    parser.add_argument("--search-top-k", type=int, default=DEFAULT_SEARCH_TOP_K)
    parser.add_argument("--max-sections", type=int, default=DEFAULT_MAX_SECTIONS)
    parser.add_argument("--result-cap", type=int, default=DEFAULT_RESULT_CAP)
    parser.add_argument("--relevance-margin", type=float, default=DEFAULT_RELEVANCE_MARGIN)
    parser.add_argument("--db-path", type=str, default=str(paths.VECTOR_DB_PATH))
    parser.add_argument("--graph-db-path", type=str, default=str(paths.GRAPH_DB_PATH))
    args = parser.parse_args()

    results = retrieve(
        args.query,
        db_path=Path(args.db_path),
        graph_db_path=Path(args.graph_db_path),
        search_top_k=args.search_top_k,
        max_sections=args.max_sections,
        result_cap=args.result_cap,
        relevance_margin=args.relevance_margin,
    )

    if not results:
        print("Aucun resultat.")
        return 0

    print(f"Requete : {args.query!r}")
    print(f"{len(results)} chunk(s) trouve(s)\n")

    by_reason: dict[str, int] = {}
    by_type = {"code": 0, "formule": 0, "image": 0, "texte": 0}
    for r in results:
        reason_key = r.reason.split(":")[0]
        by_reason[reason_key] = by_reason.get(reason_key, 0) + 1
        if r.has_code:
            by_type["code"] += 1
        if r.has_formula:
            by_type["formule"] += 1
        if r.has_image:
            by_type["image"] += 1
        if not (r.has_code or r.has_formula or r.has_image):
            by_type["texte"] += 1

    print("Origine  : " + ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items())))
    print("Contenu  : " + ", ".join(f"{k}={v}" for k, v in by_type.items()))
    print("=" * 100)

    # Regroupement par (document, section), dans l'ordre de premiere apparition
    # dans `results` (deja ordonne par pertinence par `retrieve()`).
    order: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[RetrievedChunk]] = {}
    for r in results:
        trail = " > ".join(r.heading_trail)
        key = (r.document_id, trail)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(r)

    for doc_id, trail in order:
        chunks = sorted(grouped[(doc_id, trail)], key=lambda r: r.chunk_index)
        reasons_here = sorted({c.reason for c in chunks})
        print(f"\n[{doc_id}]")
        print(f"  {trail or '(sans titre)'}")
        print(f"  {len(chunks)} chunk(s) -- {', '.join(reasons_here)}")
        for c in chunks:
            score_s = f"{c.score:.3f}" if c.score is not None else "  -  "
            print(f"\n  - chunk #{c.chunk_index:<4d} [{_type_tags(c):12s}] score={score_s}")
            # Un bloc code/formule/image n'a JAMAIS de sens tronque (un code
            # coupe en plein milieu, une formule LaTeX incomplete...) --
            # seule la prose beneficie d'un apercu par defaut (--full pour
            # la voir en entier aussi).
            show_full = args.full or c.has_code or c.has_formula or c.has_image
            body = c.text if show_full else _preview(c.text)
            print(textwrap.indent(textwrap.fill(body, width=92), "      "))
            if c.has_image:
                for img_path in _image_paths(c.document_id, c.text):
                    exists = "" if img_path.exists() else "  (INTROUVABLE)"
                    print(f"      [image] {img_path}{exists}")
                    if args.open_images and img_path.exists():
                        os.startfile(img_path)  # noqa: S606 -- ouverture volontaire, jamais sans --open-images
        print("\n" + "-" * 100)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
