"""Etape /rag-index : chunks.json -> embedding + indexation vectorielle (Chroma).

Usage:
    python run.py <pdf_condense | document_id | chemin_dossier_travail | chunks.json> [--force]

Ecrit dans <racine_projet>/rag_data/db/vector/ (base Chroma UNIQUE, partagee
entre tous les documents) et met a jour <document>/status.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from chunk import Chunk
from quality import is_noise_text
from vector_store import DocumentMetadata, index_chunks
import paths
import status as status_lib


def _document_id_of(work_dir: Path) -> str:
    meta_path = work_dir / "meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))["document_id"]
    return work_dir.name  # work_dir est deja nomme <document_id>


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou chunks.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    chunks_path = work_dir / "chunks.json"

    if not chunks_path.exists():
        print(f"ERREUR: chunks introuvables: {chunks_path} (lancez d'abord /rag-chunking)", file=sys.stderr)
        return 1

    if status_lib.is_done(work_dir, "indexation_vectorielle") and not args.force:
        print("deja fait (indexation_vectorielle)")
        return 0

    raw_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = [Chunk(**d) for d in raw_chunks]
    # has_code/has_formula exemptes du filtre anti-bruit : un extrait de
    # code ou une formule LaTeX a legitimement un ratio alphabetique bas
    # (beaucoup de symboles/operateurs), ce n'est jamais un signe d'echec
    # d'extraction contrairement a ce que ce filtre detecte d'habitude (voir
    # quality.py:is_noise_text) — sans cette exemption, un bloc de code ou
    # une formule pas encore traitee par /rag-nottext (ou dont le traitement
    # a echoue) risquait d'etre exclue silencieusement de l'index, a
    # l'oppose du but meme de /rag-nottext.
    indexable_chunks = [c for c in chunks if c.has_code or c.has_formula or not is_noise_text(c.text)]

    document_id = _document_id_of(work_dir)
    document = DocumentMetadata(document_id=document_id, source_path=str(work_dir / "pivot.md"))

    n_indexed = index_chunks(indexable_chunks, document, db_path=paths.VECTOR_DB_PATH)

    status_lib.mark_stage(
        work_dir, "indexation_vectorielle", "done",
        n_indexed=n_indexed, n_ignored=len(chunks) - len(indexable_chunks), document_id=document_id,
    )

    print(f"OK: {n_indexed} chunk(s) indexe(s) ({len(chunks) - len(indexable_chunks)} ignore(s) comme bruit)")
    print(f"document_id: {document_id}")
    print(f"base vectorielle: {paths.VECTOR_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
