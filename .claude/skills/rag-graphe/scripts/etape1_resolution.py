"""Étape 1 de /rag-graphe : résoudre les mentions d'un document et les lier au graphe partagé.

Usage :
    etape1_resolution.py <pdf_condense | document_id | dossier_de_travail | concepts.json>
        [--force] [--reset] [--batch-size N] [--clear-graph [--dry-run]]

Écrit dans rag_data/db/graph/ (base Kuzu UNIQUE, partagée entre tous les documents) et met à
jour <document>/status.json. Codes : 0 OK, 1 erreur d'entrée, 2 BLOQUANT, 3 lot partiel
(relancer la même commande). Sous-étapes dans lib/ : graphe_document, reprise, clear_graph.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # commun.py
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import argparse
import atexit
import json

from commun import document_id_of, nombre_appels, progress_path
from concepts import ConceptMention
from graph_store import ConceptGraph
from resolution import embedding_text
from vector_store import embed_texts
from graphe_document import ArretIntegrite, arret_integrite, charger_textes_chunks, decider_lot, ecrire_lot
import clear_graph
import reprise
import checks
import paths
import status as status_lib


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
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="nombre max de chunks a traiter dans cette invocation (defaut : tous) — "
        "relancer la meme commande (SANS --force/--reset) tant que le code de sortie est 3",
    )
    parser.add_argument(
        "--clear-graph",
        action="store_true",
        help=(
            "DESTRUCTIF POUR TOUT LE CORPUS : vide le graphe partage (concepts, chunks et liens de "
            "TOUS les documents, alias compris), remet a zero l'etape 'graphe' de chaque document, "
            "puis S'ARRETE : rien n'est reconstruit. Chaque document doit ensuite etre reconstruit "
            "(/rag-graphe <document> --reset). Seule facon de supprimer les alias residuels qu'un "
            "--reset laisse sur les concepts partages. Le document donne en argument doit avoir un "
            "concepts.json valide (controle d'entree : on ne vide pas ce que l'on ne peut pas refaire)."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="avec --clear-graph : affiche ce qui serait supprime, ne modifie rien",
    )
    args = parser.parse_args()
    if args.dry_run and not args.clear_graph:
        print("ERREUR: --dry-run n'a de sens qu'avec --clear-graph", file=_sys.stderr)
        return 1

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=_sys.stderr)
        return 1

    concepts_path = work_dir / "concepts.json"
    if not concepts_path.exists():
        print(f"ERREUR: concepts introuvables: {concepts_path} (lancez d'abord /rag-concepts)", file=_sys.stderr)
        return 1

    document_id = document_id_of(work_dir)
    graph = ConceptGraph(paths.GRAPH_DB_PATH)
    # Fermeture propre (CHECKPOINT + close) a la sortie du processus, quel que soit le `return` pris plus bas.
    atexit.register(graph.close)

    # Controle d'entree AVANT toute suppression : un --reset suivi d'un controle en echec
    # retirerait la contribution de ce document au graphe partage sans rien reconstruire.
    pre_checked = False
    if args.reset or args.clear_graph:
        pre = checks.check_input("graphe", work_dir)
        print(pre.report())
        if not pre.ok:
            return 1
        pre_checked = True

    if args.clear_graph:
        return clear_graph.vider(graph, dry_run=args.dry_run)

    if args.reset:
        graph.remove_document(document_id)
        progress_path(work_dir).unlink(missing_ok=True)
        status = status_lib.load_status(work_dir)
        status.pop("graphe", None)
        status_lib.save_status(work_dir, status)
        print(f"reset : contribution de {document_id!r} retiree du graphe (autres documents non affectes)")

    if status_lib.is_done(work_dir, "graphe") and not args.force and not args.reset:
        # `done` ne prouve rien sur le graphe lui-meme (base supprimee ou
        # videe depuis) : verifie qu'il contient bien ce document.
        existing = checks.check_output("graphe", work_dir, graph=graph)
        if existing.ok:
            print("deja fait (graphe)")
            print(existing.report())
            return 0
        print("status.json dit 'done' mais le graphe ne le confirme pas — reconstruction :")
        print(existing.report())

    if not pre_checked:
        pre = checks.check_input("graphe", work_dir)
        print(pre.report())
        if not pre.ok:
            return 1

    per_chunk = json.loads(concepts_path.read_text(encoding="utf-8"))
    known_concepts = graph.all_concepts()  # charge une fois, pas par mention

    # Integrite AVANT tout travail : detecte une corruption apparue entre deux lots.
    problems = graph.check_integrity()
    if problems:
        return arret_integrite(work_dir, "debut de lot (base relue a l'ouverture)", problems)

    # Reprise apres interruption : sans --force, on saute les chunks deja entierement traites.
    progress = reprise.vide() if args.force else reprise.charger(work_dir)
    chunks_done = set(progress["chunks_done"])
    if chunks_done:
        print(f"reprise : {len(chunks_done)} chunk(s) deja traite(s) et lie(s), ignore(s)")

    remaining_entries = [entry for entry in per_chunk if entry["chunk_index"] not in chunks_done]
    batch_entries = remaining_entries if args.batch_size is None else remaining_entries[: args.batch_size]
    print(f"--- Lot en cours : {len(batch_entries)} chunk(s) sur {len(remaining_entries)} restant(s) ---")

    # Embedding groupe de toutes les mentions DU LOT en un seul appel (12x plus rapide qu'un
    # appel par mention). Les decisions sont ensuite prises en parallele, les ecritures restent sequentielles.
    mentions_of_batch = [
        ConceptMention(name=m["name"], canonical_form=m["canonical_form"], type=m["type"], sense=m.get("sense", ""))
        for entry in batch_entries for m in entry["mentions"]
    ]
    embeddings = embed_texts([embedding_text(m) for m in mentions_of_batch]) if mentions_of_batch else []
    chunk_texts = charger_textes_chunks(work_dir)

    # Décisions : un thread par mention, lecture seule sur known_concepts.
    decisions = decider_lot(batch_entries, document_id, chunk_texts, known_concepts, embeddings)
    print(f"appels claude du lot : {nombre_appels()}")
    # Écritures : le seul processus parent, dans l'ordre des mentions.
    try:
        par_chunk = ecrire_lot(graph, document_id, batch_entries, decisions, known_concepts)
    except ArretIntegrite as exc:
        return arret_integrite(work_dir, exc.where, exc.lines)

    newly_done = 0
    for entry in batch_entries:
        chunk_index = entry["chunk_index"]
        chunk_linked, chunk_skipped = par_chunk[chunk_index]

        # Checkpoint apres CHAQUE chunk : un chunk avec mention ignoree n'est PAS marque "fait",
        # une reprise le retente au lieu de perdre definitivement ces mentions.
        progress["per_chunk"][str(chunk_index)] = {"linked": chunk_linked, "skipped": chunk_skipped}
        if chunk_skipped == 0:
            if chunk_index not in chunks_done:
                newly_done += 1
            chunks_done.add(chunk_index)
        else:
            chunks_done.discard(chunk_index)
        progress["chunks_done"] = sorted(chunks_done)
        reprise.enregistrer(work_dir, progress)
        print(f">>> Progression : {len(chunks_done)}/{len(per_chunk)} chunk(s) traites")

    # Integrite APRES le lot : base relue et comparee a l'etat tenu en memoire.
    problems = graph.check_integrity(known_concepts)
    if problems:
        return arret_integrite(work_dir, "fin de lot (base relue et comparee a la memoire)", problems)

    n_mentions, n_skipped = reprise.totaux(progress)

    # Lot partiel : il reste des chunks ET ce lot a fait des progres. Un lot ou aucun chunk
    # n'a ete mene a bout termine le traitement (sinon le code 3 bouclerait indefiniment).
    still_pending = [entry for entry in per_chunk if entry["chunk_index"] not in chunks_done]
    if args.batch_size is not None and still_pending and newly_done > 0:
        status_lib.mark_stage(
            work_dir, "graphe", "failed",
            detail=f"lot partiel en cours : {len(still_pending)} chunk(s) restant(s)",
        )
        print(
            f"Lot de {len(batch_entries)} chunk(s) traite(s), {len(still_pending)} restant(s) — "
            "relance exactement la meme commande (sans --force/--reset) pour continuer "
            "(etape non terminee, status.json pas encore marque 'done')."
        )
        return 3

    post = checks.check_output("graphe", work_dir, graph=graph, n_mentions=n_mentions, n_skipped=n_skipped)
    print(post.report())
    status_lib.mark_stage(
        work_dir, "graphe", "done" if post.ok else "failed", detail=post.detail(),
        n_mentions=n_mentions, n_skipped=n_skipped, document_id=document_id,
        verdict=post.verdict, verdict_reasons=post.blocking,
    )
    suffix = f" ({n_skipped} ignoree(s))" if n_skipped else ""
    print(f"{'OK' if post.ok else 'BLOQUANT'}: {n_mentions} mention(s) resolue(s) et liee(s) au graphe{suffix}")
    print(f"base graphe: {paths.GRAPH_DB_PATH}")
    return 0 if post.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
