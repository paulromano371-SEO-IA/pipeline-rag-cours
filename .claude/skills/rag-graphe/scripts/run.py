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
import atexit
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from graph_store import ConceptGraph, GraphIntegrityError
from entity_resolution import ArbitrationError, resolve_concept
from concepts import ConceptMention
from vector_store import embed_texts
import checks
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
    empty = {"chunks_done": [], "per_chunk": {}, "base_mentions": 0, "base_skipped": 0}
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return empty
    data.setdefault("chunks_done", [])
    if "per_chunk" not in data:
        # Ancien format (avant le comptage par chunk) : ses totaux cumules ne
        # sont pas decomposables par chunk, on les garde comme base fixe.
        data["base_mentions"] = data.get("n_mentions", 0)
        data["base_skipped"] = data.get("n_skipped", 0)
        data["per_chunk"] = {}
    data.setdefault("base_mentions", 0)
    data.setdefault("base_skipped", 0)
    return data


def _totals(progress: dict) -> tuple[int, int]:
    """(mentions liees, mentions ignorees) cumulees. Comptees PAR CHUNK et
    remplacees a chaque nouvelle tentative : un chunk retente ne compte
    jamais deux fois."""
    per_chunk = progress["per_chunk"].values()
    return (
        progress["base_mentions"] + sum(c["linked"] for c in per_chunk),
        progress["base_skipped"] + sum(c["skipped"] for c in per_chunk),
    )


def _integrity_stop(work_dir: Path, where: str, lines: list[str]) -> int:
    """Arret sur incoherence entre Kuzu et ce que le programme y a ecrit. Code 2
    (BLOQUANT), etape marquee `failed` : le pipeline ne doit jamais enchainer
    sur une base dont les alias ne sont plus fiables."""
    print("=== INTEGRITE DU GRAPHE : ECART DETECTE — arret de /rag-graphe ===", file=sys.stderr)
    print(f"Moment : {where}", file=sys.stderr)
    for line in lines:
        print(f"  - {line}", file=sys.stderr)
    print(
        "Le graphe contient deja l'ecart : relancer sans le retirer le laisserait en place. "
        "Reconstruire ce livre avec `--reset --batch-size 10` (ou `--clear-graph` si un autre livre est touche).",
        file=sys.stderr,
    )
    status_lib.mark_stage(
        work_dir, "graphe", "failed", detail=f"integrite du graphe : ecart detecte ({where})",
        verdict="bloquant", verdict_reasons=[f"integrite du graphe : {where}"],
    )
    return 2


def _save_progress(work_dir: Path, progress: dict) -> None:
    _progress_path(work_dir).write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")


def _work_dirs_with_graph_state() -> list[Path]:
    """Dossiers de travail (tous documents) dont l'etape 'graphe' est renseignee
    dans status.json ou qui portent un fichier de reprise — ce que `--clear-graph`
    doit remettre a zero, le graphe etant partage entre tous les documents."""
    found = []
    if not paths.WORK_DIR.exists():
        return found
    for d in sorted(p for p in paths.WORK_DIR.iterdir() if p.is_dir()):
        try:
            has_state = "graphe" in status_lib.load_status(d) or _progress_path(d).exists()
        except (json.JSONDecodeError, OSError):
            print(f"  attention : status.json illisible dans {d.name}, ignore", file=sys.stderr)
            continue
        if has_state:
            found.append(d)
    return found


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
        print("ERREUR: --dry-run n'a de sens qu'avec --clear-graph", file=sys.stderr)
        return 1

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
    # Fermeture propre (CHECKPOINT + close) a la sortie du processus, quel que
    # soit le `return` pris plus bas (lot partiel, fin, arret integrite, erreur).
    atexit.register(graph.close)

    # Controle d'entree AVANT toute suppression : un --reset suivi d'un
    # controle en echec (concepts perimes, CLI claude absent...) retirerait la
    # contribution de ce document au graphe partage sans rien reconstruire.
    pre_checked = False
    if args.reset or args.clear_graph:
        pre = checks.check_input("graphe", work_dir)
        print(pre.report())
        if not pre.ok:
            return 1
        pre_checked = True

    if args.clear_graph:
        stats = graph.stats()
        other_dirs = _work_dirs_with_graph_state()
        print("=== --clear-graph : impact sur le graphe PARTAGE ===")
        print(f"Supprime : {stats['concepts']} concept(s), {stats['mentions']} lien(s), "
              f"{sum(stats['chunks_by_document'].values())} chunk(s) de {len(stats['chunks_by_document'])} document(s) :")
        for doc, n in sorted(stats["chunks_by_document"].items()):
            known = "" if (paths.WORK_DIR / doc).exists() else "  [AUCUN dossier de travail : non reconstructible]"
            print(f"  - {doc} ({n} chunk(s)){known}")
        print(f"Etape 'graphe' remise a zero dans status.json pour {len(other_dirs)} document(s) : "
              + (", ".join(d.name for d in other_dirs) or "aucun"))
        print("Aucun document n'est reconstruit par cette commande.")
        if args.dry_run:
            print("--dry-run : rien n'a ete modifie.")
            return 0
        graph.clear()
        for d in other_dirs:
            _progress_path(d).unlink(missing_ok=True)
            state = status_lib.load_status(d)
            state.pop("graphe", None)
            status_lib.save_status(d, state)
        print("graphe vide ; etape 'graphe' remise a zero pour les documents ci-dessus.")
        print("A reconstruire ensuite, document par document : /rag-graphe <document> --reset --batch-size 10")
        return 0

    if args.reset:
        graph.remove_document(document_id)
        _progress_path(work_dir).unlink(missing_ok=True)
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

    # Integrite AVANT tout travail : detecte une corruption apparue a la
    # fermeture/reouverture de la base (entre deux lots) ou laissee par un run
    # precedent, sans qu'elle soit imputee au lot qui commence.
    problems = graph.check_integrity()
    if problems:
        return _integrity_stop(work_dir, "debut de lot (base relue a l'ouverture)", problems)

    # Reprise apres interruption : si --force n'est pas passe, on saute les
    # chunks deja entierement traites lors d'un run precedent interrompu
    # (voir _load_progress) -- evite de retraiter (et surtout de re-arbitrer
    # via claude -p, l'operation couteuse ici) des mentions deja liees au
    # graphe. Avec --force, on repart de zero comme avant (mais le graphe
    # LUI-MEME n'est pas vide pour autant : utiliser `ConceptGraph.clear()`
    # au prealable si une reconstruction complete est voulue).
    progress = (
        {"chunks_done": [], "per_chunk": {}, "base_mentions": 0, "base_skipped": 0}
        if args.force else _load_progress(work_dir)
    )
    chunks_done = set(progress["chunks_done"])
    if chunks_done:
        print(f"reprise : {len(chunks_done)} chunk(s) deja traite(s) et lie(s), ignore(s)")

    remaining_entries = [entry for entry in per_chunk if entry["chunk_index"] not in chunks_done]
    batch_entries = remaining_entries if args.batch_size is None else remaining_entries[: args.batch_size]
    print(f"--- Lot en cours : {len(batch_entries)} chunk(s) sur {len(remaining_entries)} restant(s) ---")

    # Embedding groupe de toutes les mentions DU LOT en un seul appel,
    # avant la boucle de resolution -- 12x plus rapide qu'un appel individuel
    # par mention (mesure empirique : 404ms/appel isole contre 32ms/texte en
    # lot de 20 ; sur ~1600 mentions, ~11 minutes evitees). La boucle de
    # resolution elle-meme reste sequentielle (une mention peut se fusionner
    # avec un concept cree par une mention precedente du meme document) :
    # seul le calcul d'embedding, independant de cet ordre, est groupe. Limite
    # au lot (et non a tous les chunks restants) pour ne pas re-embedder a
    # chaque invocation ce qui sera traite dans les suivantes.
    all_canonical_forms = [m["canonical_form"] for entry in batch_entries for m in entry["mentions"]]
    embeddings = embed_texts(all_canonical_forms) if all_canonical_forms else []
    embedding_iter = iter(embeddings)

    newly_done = 0
    for entry in batch_entries:
        chunk_index = entry["chunk_index"]
        chunk_id = f"{document_id}::{chunk_index}"
        graph.add_chunk(chunk_id, document_id, chunk_index)
        chunk_linked = 0
        chunk_skipped = 0

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
                chunk_skipped += 1
                continue
            except GraphIntegrityError as exc:
                return _integrity_stop(
                    work_dir,
                    f"pendant le lot, chunk {chunk_index} ({chunk_id}), mention {m['name']!r} -> {mention.canonical_form!r}",
                    str(exc).splitlines(),
                )
            graph.link_mention(chunk_id, outcome.concept_id)
            chunk_linked += 1

        # Checkpoint apres CHAQUE chunk traite : une interruption ne perd
        # jamais plus d'un chunk de progression. Un chunk qui contient au
        # moins une mention ignoree n'est PAS marque "fait" : une reprise le
        # retente (mentions deja liees : fusion puis lien idempotent, voir
        # `link_mention`) au lieu de perdre definitivement ces mentions.
        progress["per_chunk"][str(chunk_index)] = {"linked": chunk_linked, "skipped": chunk_skipped}
        if chunk_skipped == 0:
            if chunk_index not in chunks_done:
                newly_done += 1
            chunks_done.add(chunk_index)
        else:
            chunks_done.discard(chunk_index)
        progress["chunks_done"] = sorted(chunks_done)
        _save_progress(work_dir, progress)
        print(f">>> Progression : {len(chunks_done)}/{len(per_chunk)} chunk(s) traites")

    # Integrite APRES le lot : toute la base relue et comparee a l'etat tenu en
    # memoire (alias compris). Un ecart ici, sans ecart pendant le lot, veut dire
    # que la corruption est apparue apres l'ecriture (pas au moment de celle-ci).
    problems = graph.check_integrity(known_concepts)
    if problems:
        return _integrity_stop(work_dir, "fin de lot (base relue et comparee a la memoire)", problems)

    n_mentions, n_skipped = _totals(progress)

    # Lot partiel : il reste des chunks ET ce lot a fait des progres. Un lot ou
    # aucun chunk n'a ete mene a bout termine le traitement (comme
    # /rag-nottext) : un chunk avec mention ignoree reste "pending", donc sans
    # cette sortie le code 3 boucle indefiniment — le controle de sortie
    # ci-dessous tranche alors (bloquant si > 5 % de mentions ignorees).
    still_pending = [entry for entry in per_chunk if entry["chunk_index"] not in chunks_done]
    if args.batch_size is not None and still_pending and newly_done > 0:
        # "failed" + detail "lot partiel" a chaque lot intermediaire : un
        # --force/--reset repart d'un ancien "done" (sans cette retrogradation
        # la relance sans --force croirait l'etape terminee), et le detail
        # reste a jour du nombre de chunks restants.
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
    if post.ok:
        _progress_path(work_dir).unlink(missing_ok=True)  # etape terminee, plus besoin de l'etat de reprise
    # Sinon l'etat de reprise est conserve : relancer la meme commande retente
    # uniquement les chunks qui ont des mentions ignorees.

    suffix = f" ({n_skipped} ignoree(s))" if n_skipped else ""
    print(f"{'OK' if post.ok else 'BLOQUANT'}: {n_mentions} mention(s) resolue(s) et liee(s) au graphe{suffix}")
    print(f"base graphe: {paths.GRAPH_DB_PATH}")
    return 0 if post.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
