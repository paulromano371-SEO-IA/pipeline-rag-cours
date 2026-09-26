"""Consolidation du graphe : fusionne les concepts en double qui ont echappe a
la resolution d'entites de /rag-graphe.

Sous-etape LANCEE PAR LE SKILL /rag-graphe (voir son SKILL.md), une fois le
graphe du document termine (`[FIN]` de graphe_lot.py) -- jamais par
graphe_lot.py lui-meme. Elle porte sur le graphe PARTAGE, pas sur un document.

Principe : toutes les paires de concepts dont les embeddings se ressemblent
(>= 0.80, le seuil de l'arbitrage) sont jugees par `claude -p` avec leur
definition, leurs livres et un extrait de chunk de CHAQUE cote. Sur "meme
concept", le concept le moins mentionne est fusionne dans l'autre
(`ConceptGraph.merge_concepts` : alias et liens sont reportes).

Chaque paire jugee (fusionnee OU distincte) est memorisee dans
`rag_data/db/consolidation_decisions.json`, avec les livres qui mentionnaient
chaque concept a ce moment-la : une relance ne rejuge pas une paire tranchee,
sauf une paire "distincte" dont l'un des concepts est mentionne depuis par un
NOUVEAU livre (nouveau contexte, la reponse peut changer). L'etape est ainsi
reprenable et incrementale.

Une fois toutes les paires jugees, chaque concept prend pour forme canonique
son nom LE PLUS FREQUENT dans le corpus (voir `plan_renames`) : le nom ne
depend plus de l'ordre dans lequel les livres ont ete ranges.

Usage :
    python consolidate.py [--batch-size 10] [--dry-run]

Un lot par appel (au-dela de 10 minutes, l'application passerait la commande
en arriere-plan, ce que le pipeline interdit) : relancer la meme commande tant
que la derniere ligne est [LOT n/N].

Sortie : stdout et stderr fusionnes (ordre des lignes conserve, rien en rouge
pour un lot partiel), le detail par paire, puis UNE ligne de bilan commencant par
[LOT n/N] (relancer la meme commande), [FIN], [BLOQUANT] ou [ERREUR].
Code de sortie : 0 pour [LOT]/[FIN] (un lot partiel n'est pas une erreur),
2 pour [BLOQUANT], 1 pour [ERREUR].
"""
from __future__ import annotations
# Garde-fou : force l'interpreteur du projet (.venv-rag), voir _rag_lib/venv_guard.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_rag_lib"))

from graph_store import ConceptGraph, ConceptRecord, GraphIntegrityError
from entity_resolution import AMBIGUOUS_THRESHOLD, ArbitrationError, ArbitrationSide, _arbitrate_via_claude_code
import paths

EXCERPT_WINDOW = 400


def _pair_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def load_decisions(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_decisions(path: Path, decisions: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decisions, ensure_ascii=False, indent=1), encoding="utf-8")


def candidate_pairs(concepts: list[ConceptRecord], threshold: float) -> list[tuple[float, int, int]]:
    """Paires (similarite, i, j), i < j, de similarite >= threshold, la plus
    proche d'abord."""
    if len(concepts) < 2:
        return []
    dims = {len(c.embedding) for c in concepts}
    if len(dims) != 1:
        raise GraphIntegrityError(f"embeddings de dimensions differentes dans le graphe : {sorted(dims)}")
    matrix = np.asarray([c.embedding for c in concepts], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms
    sims = matrix @ matrix.T
    rows, cols = np.triu_indices(len(concepts), k=1)
    values = sims[rows, cols]
    keep = values >= threshold
    pairs = list(zip(values[keep].tolist(), rows[keep].tolist(), cols[keep].tolist()))
    pairs.sort(key=lambda p: p[0], reverse=True)
    return pairs


class ExcerptSource:
    """Extrait de chunk autour du terme, pour un concept (lu dans
    rag_data/work/<document>/chunks.json, mis en cache par document)."""

    def __init__(self, graph: ConceptGraph):
        self._graph = graph
        self._chunks: dict[str, dict[int, str]] = {}

    def _texts(self, document_id: str) -> dict[int, str]:
        if document_id not in self._chunks:
            path = paths.WORK_DIR / document_id / "chunks.json"
            try:
                self._chunks[document_id] = {c["index"]: c["text"] for c in json.loads(path.read_text(encoding="utf-8"))}
            except (OSError, json.JSONDecodeError, KeyError):
                self._chunks[document_id] = {}
        return self._chunks[document_id]

    def excerpt(self, concept: ConceptRecord) -> str:
        for _chunk_id, document_id, chunk_index in sorted(self._graph.mentions_of(concept.id), key=lambda m: (m[1], m[2])):
            text = self._texts(document_id).get(chunk_index)
            if not text:
                continue
            at = text.lower().find(concept.canonical_form.lower())
            start = max(at - EXCERPT_WINDOW // 4, 0) if at >= 0 else 0
            return text[start:start + EXCERPT_WINDOW]
        return ""


SHORT_FORM_MAX_CHARS = 3
# Types generiques : le LLM d'extraction hesite souvent entre "concept" et un
# type precis pour un MEME concept (ex. numpy = tool ou concept). Ils sont donc
# compatibles avec tout type ; seuls deux types precis DIFFERENTS (ex. method et
# tool) interdisent la fusion -- ces paires ne sont ni jugees ni memorisees.
GENERIC_TYPES = {"concept", "other"}


def types_compatible(a: str, b: str) -> bool:
    return a == b or a in GENERIC_TYPES or b in GENERIC_TYPES


def merged_type(keep_type: str, drop_type: str) -> str:
    """Type du concept conserve apres fusion : le type precis l'emporte sur un type generique."""
    return drop_type if keep_type in GENERIC_TYPES and drop_type not in GENERIC_TYPES else keep_type


def _mention_forms_by_chunk(document_id: str, cache: dict) -> dict[int, list[str]]:
    """{chunk_index: [formes canoniques extraites]} lu dans concepts.json du document."""
    if document_id not in cache:
        path = paths.WORK_DIR / document_id / "concepts.json"
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            cache[document_id] = {e["chunk_index"]: [m["canonical_form"] for m in e["mentions"]] for e in entries}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            cache[document_id] = {}
    return cache[document_id]


def plan_renames(graph: ConceptGraph, concepts: list[ConceptRecord]) -> list[tuple[ConceptRecord, str, dict[str, int]]]:
    """(concept, nouveau nom, frequences) pour chaque concept dont la forme
    canonique n'est pas son nom le plus frequent.

    Frequence d'un alias = nombre de mentions de cette forme dans les chunks
    LIES a ce concept (concepts.json de chaque livre). Regle fixe : plus
    frequent d'abord, puis ordre alphabetique a egalite. Une forme de
    SHORT_FORM_MAX_CHARS caracteres ou moins ("f", "map") n'est jamais choisie
    tant qu'un alias plus long existe : trop ambigue pour servir de nom. Sans
    aucune frequence connue (concepts.json absent), le nom est laisse tel quel."""
    cache: dict = {}
    plan = []
    for concept in concepts:
        counts = {alias: 0 for alias in concept.aliases}
        for _chunk_id, document_id, chunk_index in graph.mentions_of(concept.id):
            for form in _mention_forms_by_chunk(document_id, cache).get(chunk_index, []):
                if form in counts:
                    counts[form] += 1
        if not any(counts.values()):
            continue
        candidates = [a for a in counts if len(a) > SHORT_FORM_MAX_CHARS] or list(counts)
        best = min(candidates, key=lambda a: (-counts[a], a))
        if best != concept.canonical_form:
            plan.append((concept, best, counts))
    return plan


def needs_rejudging(decision: dict | None, a: ConceptRecord, b: ConceptRecord) -> bool:
    """Une paire jugee "distincte" est rejugee quand un livre qui n'existait pas
    (pour ce concept) au moment du jugement le mentionne depuis : ce nouveau
    contexte peut changer la reponse. Une decision sans memoire des livres
    (ancien format) n'est jamais rejugee ; une paire jugee "meme concept" a deja
    ete fusionnee."""
    if decision is None:
        return True
    if decision.get("same") or "docs" not in decision:
        return False
    known = decision["docs"]
    return bool(set(a.documents) - set(known.get(a.id, ())) or set(b.documents) - set(known.get(b.id, ())))


def side_of(concept: ConceptRecord, excerpts: ExcerptSource) -> ArbitrationSide:
    return ArbitrationSide(
        name=concept.canonical_form, sense=concept.sense,
        documents=tuple(sorted(concept.documents)), excerpt=excerpts.excerpt(concept), type=concept.type,
    )


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
        if needs_rejudging(decisions.get(_pair_key(concepts[p[1]].id, concepts[p[2]].id)), concepts[p[1]], concepts[p[2]])
    ]
    n_rejudge = sum(1 for p in pending if _pair_key(concepts[p[1]].id, concepts[p[2]].id) in decisions)
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
    gone: set[str] = set()  # concepts fusionnes (supprimes) pendant ce lot
    attempts = merged = distinct = failed = 0
    for sim, i, j in pending:
        a, b = concepts[i], concepts[j]
        if a.id in gone or b.id in gone:
            continue  # sa paire sera recalculee (avec le concept conserve) a la relance
        if attempts >= args.batch_size:
            break
        attempts += 1
        try:
            same = _arbitrate_via_claude_code(side_of(a, excerpts), side_of(b, excerpts))
        except ArbitrationError as exc:
            print(f"  {a.canonical_form!r} <-> {b.canonical_form!r} : arbitrage echoue, paire retentee au prochain lot ({exc})", file=sys.stderr)
            failed += 1
            continue

        decisions[_pair_key(a.id, b.id)] = {
            "same": same, "names": [a.canonical_form, b.canonical_form], "sim": round(sim, 3),
            "docs": {a.id: sorted(a.documents), b.id: sorted(b.documents)},
        }
        if same:
            # Conserve le concept le plus mentionne (puis le present dans le plus de livres).
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
        if needs_rejudging(decisions.get(_pair_key(concepts[i].id, concepts[j].id)), concepts[i], concepts[j])
        and concepts[i].id not in gone and concepts[j].id not in gone
    )
    print(f"Bilan du lot : {attempts} paire(s) jugee(s) : {merged} fusion(s), {distinct} distincte(s), {failed} echec(s) d'arbitrage.")
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
