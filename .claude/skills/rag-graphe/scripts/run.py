"""Etape /rag-graphe : concepts.json -> resolution d'entites + graphe (Kuzu).

Pour chaque mention de concept, compare par similarite d'embedding aux
concepts deja connus du graphe : fusion automatique (similarite haute),
nouveau concept (similarite basse), ou arbitrage `claude -p` (zone ambigue).

Usage:
    python run.py <pdf_condense | document_id | chemin_dossier_travail | concepts.json> [--force]

Ecrit dans <racine_projet>/rag_data/db/graph/ (base Kuzu UNIQUE, partagee
entre tous les documents) et met a jour <document>/status.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from graph_store import ConceptGraph
from entity_resolution import resolve_concept
from concepts import ConceptMention
import paths
import status as status_lib


def _document_id_of(work_dir: Path) -> str:
    meta_path = work_dir / "meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))["document_id"]
    return work_dir.name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou concepts.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    concepts_path = work_dir / "concepts.json"

    if not concepts_path.exists():
        print(f"ERREUR: concepts introuvables: {concepts_path} (lancez d'abord /rag-concepts)", file=sys.stderr)
        return 1

    if status_lib.is_done(work_dir, "graphe") and not args.force:
        print("deja fait (graphe)")
        return 0

    document_id = _document_id_of(work_dir)
    per_chunk = json.loads(concepts_path.read_text(encoding="utf-8"))

    graph = ConceptGraph(paths.GRAPH_DB_PATH)
    known_concepts = graph.all_concepts()  # charge une fois, pas par mention

    n_mentions = 0
    for entry in per_chunk:
        chunk_index = entry["chunk_index"]
        chunk_id = f"{document_id}::{chunk_index}"
        graph.add_chunk(chunk_id, document_id, chunk_index)

        for m in entry["mentions"]:
            mention = ConceptMention(name=m["name"], canonical_form=m["canonical_form"], type=m["type"])
            outcome = resolve_concept(mention, graph, known_concepts=known_concepts)
            graph.link_mention(chunk_id, outcome.concept_id)
            n_mentions += 1

    status_lib.mark_stage(work_dir, "graphe", "done", n_mentions=n_mentions, document_id=document_id)

    print(f"OK: {n_mentions} mention(s) resolue(s) et liee(s) au graphe")
    print(f"base graphe: {paths.GRAPH_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
