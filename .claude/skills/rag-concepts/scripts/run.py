"""Etape /rag-concepts : chunks.json -> concepts.json (concepts candidats par chunk).

Appelle `claude -p` headless une fois par chunk indexable (pas de cle API,
reutilise l'abonnement Claude Code). Un chunk dont l'extraction echoue
(bruit non detecte, reponse LLM inexploitable) est ignore et journalise,
sans interrompre le traitement des autres.

Usage:
    python run.py <pdf_condense | document_id | chemin_dossier_travail | chunks.json> [--force]

Ecrit dans <document>/ :
    concepts.json   {"chunk_index": N, "mentions": [{"name","canonical_form","type"}, ...]}
    status.json     suivi de l'etape "extraction_concepts"
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from chunk import Chunk
from quality import is_noise_text
from concepts import extract_concepts, ConceptExtractionError
import checks
import paths
import status as status_lib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=str, help="PDF condense, document_id, dossier de travail, ou chunks.json")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Supprime concepts.json et l'entree extraction_concepts de status.json avant de regenerer entierement (ex. apres un changement du prompt d'extraction).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="nombre max de chunks a envoyer a claude -p dans cette invocation (defaut : tous) — "
        "relancer la meme commande (SANS --force/--reset) tant que le code de sortie est 3",
    )
    args = parser.parse_args()

    try:
        work_dir = paths.resolve_work_dir(args.path)
    except ValueError as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 1

    chunks_path = work_dir / "chunks.json"
    concepts_path = work_dir / "concepts.json"

    if not chunks_path.exists():
        print(f"ERREUR: chunks introuvables: {chunks_path} (lancez d'abord /rag-chunking)", file=sys.stderr)
        return 1

    already_done = (
        status_lib.is_done(work_dir, "extraction_concepts") and not args.force and not args.reset
        and concepts_path.exists()
    )

    # Controle d'entree AVANT toute suppression : un --reset suivi d'un
    # controle en echec (chunks perimes, CLI claude absent...) detruirait
    # concepts.json et son statut sans rien reconstruire.
    if not already_done:
        pre = checks.check_input("concepts", work_dir)
        print(pre.report())
        if not pre.ok:
            return 1

    if args.reset:
        concepts_path.unlink(missing_ok=True)
        status = status_lib.load_status(work_dir)
        status.pop("extraction_concepts", None)
        status_lib.save_status(work_dir, status)
        print(f"reset : {concepts_path} supprime (si present), extraction_concepts retire de status.json")

    if already_done:
        print(f"deja fait (extraction_concepts): {concepts_path}")
        return 0

    raw_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunks = [Chunk(**d) for d in raw_chunks]

    # Reprise apres interruption (Ctrl+C, crash, timeout) : si concepts.json
    # contient deja des resultats partiels d'un run precedent (status pas
    # "done", sinon on serait deja sorti ci-dessus) et que --force n'est pas
    # passe, on repart de ces resultats plutot que de tout refaire -- chaque
    # chunk deja present dans le fichier est saute. Un chunk qui avait
    # echoue (bruit ou extraction ratee) n'est PAS enregistre dans le
    # fichier : il sera naturellement retente ici, ce qui est le
    # comportement voulu pour un echec transitoire (reseau, LLM). Avec
    # --force, on repart entierement de zero comme avant.
    results = []
    processed_indices: set[int] = set()
    if concepts_path.exists() and not args.force:
        try:
            results = json.loads(concepts_path.read_text(encoding="utf-8"))
            processed_indices = {r["chunk_index"] for r in results}
        except (json.JSONDecodeError, KeyError, TypeError):
            results = []
            processed_indices = set()
        if processed_indices:
            print(f"reprise : {len(processed_indices)} chunk(s) deja traite(s), ignore(s)")

    n_skipped = 0
    n_concepts = sum(len(r["mentions"]) for r in results)

    indexable = [c for c in chunks if not is_noise_text(c.text)]
    pending_at_start = [c for c in indexable if c.index not in processed_indices]
    print(
        f"--- Lot en cours : "
        f"{len(pending_at_start) if args.batch_size is None else min(args.batch_size, len(pending_at_start))} "
        f"chunk(s) sur {len(pending_at_start)} restant(s) ---"
    )

    attempts = 0
    successes = 0
    for chunk in chunks:
        if chunk.index in processed_indices:
            continue
        if is_noise_text(chunk.text):
            n_skipped += 1
            continue
        if args.batch_size is not None and attempts >= args.batch_size:
            break
        attempts += 1
        try:
            mentions = extract_concepts(chunk.text)
        except ConceptExtractionError as exc:
            print(f"  chunk {chunk.index}: extraction ignoree ({exc})", file=sys.stderr)
            n_skipped += 1
            continue
        successes += 1
        n_concepts += len(mentions)
        results.append({"chunk_index": chunk.index, "mentions": [asdict(m) for m in mentions]})
        # Checkpoint apres CHAQUE chunk traite avec succes (pas seulement a
        # la fin) : une interruption ne perd jamais plus d'un chunk de
        # progression, et la reprise ci-dessus s'appuie exactement sur cet
        # etat intermediaire.
        concepts_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f">>> Progression : {len(results)}/{len(indexable)} chunk(s) traites")

    if not concepts_path.exists():
        concepts_path.write_text("[]", encoding="utf-8")  # aucun chunk n'a abouti : le controle ci-dessous le dira

    # Lot partiel : il reste des chunks ET ce lot a fait des progres. Un lot ou
    # RIEN n'a abouti termine le traitement (comme /rag-nottext) : un chunk en
    # echec reste "pending", donc sans cette sortie le code 3 boucle
    # indefiniment sur un chunk qui echoue a chaque tentative — le controle de
    # sortie ci-dessous tranche alors (bloquant si > 5 % de chunks sans concepts).
    done_now = {r["chunk_index"] for r in results}
    still_pending = [c for c in indexable if c.index not in done_now]
    if args.batch_size is not None and still_pending and successes > 0:
        # "failed" + detail "lot partiel" a chaque lot intermediaire : un
        # --force/--reset repart d'un ancien "done" (sans cette retrogradation
        # la relance sans --force croirait l'etape terminee), et le detail
        # reste a jour du nombre de chunks restants.
        status_lib.mark_stage(
            work_dir, "extraction_concepts", "failed",
            detail=f"lot partiel en cours : {len(still_pending)} chunk(s) restant(s)",
        )
        print(
            f"Lot de {attempts} chunk(s) traite(s) ({successes} reussi(s)), {len(still_pending)} restant(s) — "
            "relance exactement la meme commande (sans --force/--reset) pour continuer "
            "(etape non terminee, status.json pas encore marque 'done')."
        )
        return 3

    # `chunks_ignores` melange bruit (normal) et echecs d'extraction (anormal) :
    # le controle recalcule les deux separement a partir de concepts.json, ce
    # qui reste exact meme apres une ou plusieurs reprises.
    post = checks.check_output("concepts", work_dir)
    print(post.report())
    status_lib.mark_stage(
        work_dir, "extraction_concepts", "done" if post.ok else "failed", detail=post.detail(),
        n_concepts=n_concepts, chunks_ignores=n_skipped,
        n_noise=post.metrics.get("n_noise"), n_failed=post.metrics.get("n_failed"),
        verdict=post.verdict, verdict_reasons=post.blocking,
    )

    print(f"{'OK' if post.ok else 'BLOQUANT'}: {n_concepts} mention(s) de concept sur {len(chunks) - n_skipped} chunk(s) ({n_skipped} ignore(s))")
    print(f"concepts: {concepts_path}")
    return 0 if post.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
