"""Étape 2 de /rag-graphe : consolider le graphe partagé (fusion des doublons, noms canoniques).

Rattrape les doublons que la résolution d'entités de l'étape 1 a laissés passer (ex. `numpy` /
`array numpy`). Toutes les paires de concepts à similarité >= 0.80 sont jugées par `claude -p`
(nom, type, définition, pdfs et extrait de chunk des DEUX côtés) ; sur « même concept », le
concept le moins mentionné est fusionné dans l'autre (`ConceptGraph.merge_concepts`).

Chaque paire jugée est mémorisée dans `rag_data/db/consolidation_decisions.json` : une relance
ne rejuge pas une paire tranchée, sauf une paire « distincte » dont un concept est mentionné
depuis par un NOUVEAU pdf. Une fois toutes les paires jugées, chaque concept prend pour forme
canonique son nom LE PLUS FRÉQUENT dans le corpus (`plan_renames`).

Lancée par le skill une fois le graphe du document terminé (`[FIN]` de `etape1_lot.py`) ; jamais
par l'étape 1 elle-même. Porte sur le graphe PARTAGÉ, pas sur un document.

Usage :
    python etape2_consolidation.py [--batch-size 10] [--dry-run]

Un lot par appel (au-delà de 10 minutes, l'application passerait la commande en arrière-plan) :
relancer la même commande tant que la dernière ligne est [LOT n/N].

Sortie : stdout et stderr fusionnés, le détail par paire, puis UNE ligne de bilan commençant par
[LOT n/N] (relancer la même commande), [FIN], [BLOQUANT] ou [ERREUR].
Code de sortie : 0 pour [LOT]/[FIN] (un lot partiel n'est pas une erreur), 2 pour [BLOQUANT]
(intégrité du graphe : n'enchaîne rien), 1 pour [ERREUR].
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # commun.py
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import argparse
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from commun import load_prompt, nombre_appels
from arbitrage import ArbitrationError, ArbitrationSide, arbitrer
from consolidation import (
    candidate_pairs, ExcerptSource, load_decisions, merged_type, needs_rejudging,
    pair_key, plan_renames, save_decisions, side_of, types_compatible,
)
from graph_store import ConceptGraph, GraphIntegrityError
from resolution import AMBIGUOUS_THRESHOLD
import paths

ARBITRAGE_SYSTEM = load_prompt("etape2.md")


def _arbitrer_paire(sa: ArbitrationSide, sb: ArbitrationSide) -> tuple[bool | None, str]:
    """Thread : un seul appel Claude. Ne lit ni n'ecrit le graphe. Retourne (verdict, erreur)."""
    try:
        return arbitrer(sa, sb, ARBITRAGE_SYSTEM), ""
    except ArbitrationError as exc:
        return None, str(exc)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr = sys.stdout  # un seul flux : la sortie brute a relayer est complete et ordonnee

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-size", type=int, default=10, help="paires jugees par lot (defaut 10)")
    parser.add_argument("--threshold", type=float, default=AMBIGUOUS_THRESHOLD, help="similarite minimale d'une paire (defaut : seuil d'arbitrage)")
    parser.add_argument("--dry-run", action="store_true", help="liste les paires a juger, ne juge et ne modifie rien")
    args = parser.parse_args()

    start = time.time()
    graph = ConceptGraph(paths.GRAPH_DB_PATH)
    try:
        return _run(graph, args, start)
    finally:
        graph.close()


def _run(graph: ConceptGraph, args, start: float) -> int:
    problems = graph.check_integrity()
    if problems:
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        print("[BLOQUANT] integrite du graphe : ecart detecte avant consolidation -- NE PAS enchainer.")
        return 2

    concepts = graph.all_concepts()
    decisions = load_decisions(paths.CONSOLIDATION_DECISIONS_PATH)
    try:
        pairs = candidate_pairs(concepts, args.threshold)
    except GraphIntegrityError as exc:
        print(f"[ERREUR] {exc}")
        return 1
    compatible = [p for p in pairs if types_compatible(concepts[p[1]].type, concepts[p[2]].type)]
    n_type_skipped = len(pairs) - len(compatible)
    pending = [
        p for p in compatible
        if needs_rejudging(decisions.get(pair_key(concepts[p[1]].id, concepts[p[2]].id)), concepts[p[1]], concepts[p[2]])
    ]
    n_rejudge = sum(1 for p in pending if pair_key(concepts[p[1]].id, concepts[p[2]].id) in decisions)
    print(
        f"{len(concepts)} concept(s), {len(pairs)} paire(s) >= {args.threshold} : {len(compatible) - len(pending)} deja jugee(s), "
        f"{len(pending)} a juger (dont {n_rejudge} a rejuger : nouveau livre depuis), "
        f"{n_type_skipped} ignoree(s) (types precis differents, ex. method/tool)"
    )

    if args.dry_run:
        for sim, i, j in pending:
            print(f"  {sim:.3f}  {concepts[i].canonical_form!r} <-> {concepts[j].canonical_form!r}")
        for concept, new_name, counts in plan_renames(graph, concepts):
            print(f"  renommage : {concept.canonical_form!r} -> {new_name!r}  {counts}")
        print("--dry-run : rien n'a ete juge ni modifie.")
        print(f"[FIN] dry-run : {len(pending)} paire(s) a juger.")
        return 0

    excerpts = ExcerptSource(graph)
    # Lecture du graphe faite ici, par le parent : la connexion Kuzu n'est pas partagee entre threads.
    lot = pending[: args.batch_size]
    cotes = [
        (concepts[i], concepts[j], side_of(concepts[i], excerpts), side_of(concepts[j], excerpts), sim)
        for sim, i, j in lot
    ]
    # Un thread par paire : chaque thread fait un seul appel Claude et ne touche pas au graphe.
    with ThreadPoolExecutor(max_workers=max(1, len(cotes))) as pool:
        verdicts = list(pool.map(lambda c: _arbitrer_paire(c[2], c[3]), cotes))

    # Ecritures : le parent seul, dans l'ordre de similarite.
    gone: set[str] = set()  # concepts fusionnes (supprimes) pendant ce lot
    attempts = merged = distinct = failed = skipped = 0
    for (a, b, _sa, _sb, sim), (same, erreur) in zip(cotes, verdicts):
        if a.id in gone or b.id in gone:
            skipped += 1
            continue  # un concept de cette paire a deja ete fusionne dans ce lot : la paire sera recalculee a la relance
        attempts += 1
        if same is None:
            print(f"  {a.canonical_form!r} <-> {b.canonical_form!r} : arbitrage echoue, paire retentee au prochain lot ({erreur})", file=sys.stderr)
            failed += 1
            continue

        decisions[pair_key(a.id, b.id)] = {
            "same": same, "names": [a.canonical_form, b.canonical_form], "sim": round(sim, 3),
            "docs": {a.id: sorted(a.documents), b.id: sorted(b.documents)},
        }
        if same:
            # Conserve le concept le plus mentionne (puis le present dans le plus de pdfs).
            weight = lambda c: (len(graph.mentions_of(c.id)), len(c.documents))
            keep, drop = (a, b) if weight(a) >= weight(b) else (b, a)
            try:
                graph.merge_concepts(keep.id, drop.id, new_type=merged_type(keep.type, drop.type))
                keep.type = merged_type(keep.type, drop.type)
            except GraphIntegrityError as exc:
                print(f"[BLOQUANT] fusion de {drop.canonical_form!r} dans {keep.canonical_form!r} : {exc}")
                save_decisions(paths.CONSOLIDATION_DECISIONS_PATH, decisions)
                return 2
            gone.add(drop.id)
            keep.documents |= drop.documents
            merged += 1
            print(f"  FUSION  {sim:.3f}  {drop.canonical_form!r} -> {keep.canonical_form!r}")
        else:
            distinct += 1
            print(f"  distincts  {sim:.3f}  {a.canonical_form!r} / {b.canonical_form!r}")
        # Checkpoint apres CHAQUE paire : une interruption ne perd jamais plus d'une paire.
        save_decisions(paths.CONSOLIDATION_DECISIONS_PATH, decisions)

    problems = graph.check_integrity()
    if problems:
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        print("[BLOQUANT] integrite du graphe apres consolidation : ecart detecte -- NE PAS enchainer.")
        return 2

    seconds = f"{time.time() - start:.0f} s"
    remaining = sum(
        1 for _s, i, j in pending
        if needs_rejudging(decisions.get(pair_key(concepts[i].id, concepts[j].id)), concepts[i], concepts[j])
        and concepts[i].id not in gone and concepts[j].id not in gone
    )
    print(f"Bilan du lot : {attempts} paire(s) jugee(s), {skipped} sautee(s) (concept deja fusionne dans ce lot) : {merged} fusion(s), {distinct} distincte(s), {failed} echec(s) d'arbitrage, {nombre_appels()} appel(s) claude au total.")
    if attempts > 0 and failed == attempts:
        print(f"[ERREUR] consolidation : aucun arbitrage n'a abouti en {seconds} -- voir le detail ci-dessus.")
        return 1
    if remaining > 0:
        # Les paires impliquant un concept fusionne sont recalculees a la relance : le total peut varier.
        decided = len(decisions)
        n_lot = math.ceil(decided / args.batch_size)
        n_total = math.ceil((decided + remaining) / args.batch_size)
        print(f"[LOT {n_lot}/{n_total}] consolidation en {seconds} : {merged} fusion(s) dans ce lot, ~{remaining} paire(s) restante(s) -> relancer la meme commande.")
        return 0
    renamed = 0
    for concept, new_name, counts in plan_renames(graph, graph.all_concepts()):
        graph.rename_concept(concept.id, new_name)
        renamed += 1
        print(f"  renommage : {concept.canonical_form!r} -> {new_name!r}  {counts}")
    problems = graph.check_integrity()
    if problems:
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        print("[BLOQUANT] integrite du graphe apres renommage : ecart detecte -- NE PAS enchainer.")
        return 2
    print(f"{renamed} concept(s) renomme(s) selon le nom le plus frequent du corpus.")
    print(f"[FIN] consolidation terminee en {seconds} : {merged} fusion(s) dans ce lot, {len(decisions)} paire(s) jugee(s) au total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
