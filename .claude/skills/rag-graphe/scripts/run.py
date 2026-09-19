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
from entity_resolution import ArbitrationError, resolve_concept
from concepts import ConceptMention
from vector_store import embed_texts
import paths
import status as status_lib


_PROGRESS_FILENAME = "graphe_progress.json"


def _document_id_of(work_dir: Path) -> str:
    meta_path = work_dir / "meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))["document_id"]
    return work_dir.name


def _progress_path(work_dir: Path) -> Path:
    return work_dir / _PROGRESS_FILENAME


def _load_progress(work_dir: Path) -> dict:
    """Etat de reprise apres interruption : chunks deja entierement traites
    (toutes leurs mentions resolues et liees, succes ou echec journalise —
    voir la boucle principale) + compteurs cumules, pour que le rapport
    final reste exact meme apres une ou plusieurs reprises. Contrairement a
    `concepts.json` (voir /rag-concepts), le graphe Kuzu lui-meme est deja
    persiste au fil de l'eau (chaque `create_concept`/`link_mention` est un
    commit immediat) : ce fichier ne sert donc qu'a savoir QUELS chunks ne
    plus retraiter, pas a stocker le resultat lui-meme."""
    path = _progress_path(work_dir)
    if not path.exists():
        return {"chunks_done": [], "n_mentions": 0, "n_skipped": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"chunks_done": [], "n_mentions": 0, "n_skipped": 0}
    data.setdefault("chunks_done", [])
    data.setdefault("n_mentions", 0)
    data.setdefault("n_skipped", 0)
    return data


def _save_progress(work_dir: Path, progress: dict) -> None:
    _progress_path(work_dir).write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou concepts.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Retire du graphe partage la contribution de CE document uniquement "
            "(ses concepts et liens ; les concepts encore mentionnes par un autre "
            "document sont conserves), puis reconstruit entierement a partir de "
            "concepts.json. N'affecte aucun autre document du corpus."
        ),
    )
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

    document_id = _document_id_of(work_dir)
    graph = ConceptGraph(paths.GRAPH_DB_PATH)

    if args.reset:
        graph.remove_document(document_id)
        _progress_path(work_dir).unlink(missing_ok=True)
        status = status_lib.load_status(work_dir)
        status.pop("graphe", None)
        status_lib.save_status(work_dir, status)
        print(f"reset : contribution de {document_id!r} retiree du graphe (autres documents non affectes)")

    if status_lib.is_done(work_dir, "graphe") and not args.force and not args.reset:
        print("deja fait (graphe)")
        return 0

    per_chunk = json.loads(concepts_path.read_text(encoding="utf-8"))
    known_concepts = graph.all_concepts()  # charge une fois, pas par mention

    # Reprise apres interruption : si --force n'est pas passe, on saute les
    # chunks deja entierement traites lors d'un run precedent interrompu
    # (voir _load_progress) -- evite de retraiter (et surtout de re-arbitrer
    # via claude -p, l'operation couteuse ici) des mentions deja liees au
    # graphe. Avec --force, on repart de zero comme avant (mais le graphe
    # LUI-MEME n'est pas vide pour autant : utiliser `ConceptGraph.clear()`
    # au prealable si une reconstruction complete est voulue).
    progress = {"chunks_done": [], "n_mentions": 0, "n_skipped": 0} if args.force else _load_progress(work_dir)
    chunks_done = set(progress["chunks_done"])
    if chunks_done:
        print(f"reprise : {len(chunks_done)} chunk(s) deja traite(s) et lie(s), ignore(s)")

    remaining_entries = [entry for entry in per_chunk if entry["chunk_index"] not in chunks_done]

    # Embedding groupe de TOUTES les mentions restantes en un seul appel,
    # avant la boucle de resolution -- 12x plus rapide qu'un appel individuel
    # par mention (mesure empirique : 404ms/appel isole contre 32ms/texte en
    # lot de 20 ; sur ~1600 mentions, ~11 minutes evitees). La boucle de
    # resolution elle-meme reste sequentielle (une mention peut se fusionner
    # avec un concept cree par une mention precedente du meme document) :
    # seul le calcul d'embedding, independant de cet ordre, est groupe.
    all_canonical_forms = [m["canonical_form"] for entry in remaining_entries for m in entry["mentions"]]
    embeddings = embed_texts(all_canonical_forms) if all_canonical_forms else []
    embedding_iter = iter(embeddings)

    n_mentions = progress["n_mentions"]
    n_skipped = progress["n_skipped"]
    for entry in remaining_entries:
        chunk_index = entry["chunk_index"]
        chunk_id = f"{document_id}::{chunk_index}"
        graph.add_chunk(chunk_id, document_id, chunk_index)

        for m in entry["mentions"]:
            mention = ConceptMention(name=m["name"], canonical_form=m["canonical_form"], type=m["type"])
            mention_embedding = next(embedding_iter)
            # Une mention dont l'arbitrage `claude -p` echoue (timeout, reponse
            # non-JSON...) est ignoree et journalisee, sans interrompre le
            # traitement des mentions suivantes -- meme principe de resilience
            # que /rag-concepts pour un chunk en echec (voir rag-concepts/scripts/run.py).
            # Avant ce correctif, une seule mention en echec faisait perdre
            # TOUTE la progression du document (aucun `except` ici), verifie
            # empiriquement sur un vrai run (timeout apres 229 concepts crees).
            try:
                outcome = resolve_concept(mention, graph, known_concepts=known_concepts, embedding=mention_embedding)
            except ArbitrationError as exc:
                print(
                    f"  chunk {chunk_index}: mention {mention.canonical_form!r} ignoree "
                    f"(arbitrage claude -p echoue : {exc})",
                    file=sys.stderr,
                )
                n_skipped += 1
                continue
            graph.link_mention(chunk_id, outcome.concept_id)
            n_mentions += 1

        # Checkpoint apres CHAQUE chunk entierement traite (toutes ses
        # mentions resolues, avec succes ou echec journalise) : une
        # interruption ne perd jamais plus d'un chunk de progression.
        chunks_done.add(chunk_index)
        _save_progress(work_dir, {"chunks_done": sorted(chunks_done), "n_mentions": n_mentions, "n_skipped": n_skipped})

    status_lib.mark_stage(work_dir, "graphe", "done", n_mentions=n_mentions, n_skipped=n_skipped, document_id=document_id)
    _progress_path(work_dir).unlink(missing_ok=True)  # etape terminee, plus besoin de l'etat de reprise

    suffix = f" ({n_skipped} ignoree(s))" if n_skipped else ""
    print(f"OK: {n_mentions} mention(s) resolue(s) et liee(s) au graphe{suffix}")
    print(f"base graphe: {paths.GRAPH_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
