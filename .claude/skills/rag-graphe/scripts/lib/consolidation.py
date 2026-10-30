"""Consolidation (étape 2) : outils purs sur le graphe partagé.

Candidats (paires d'embeddings proches), mémoire des jugements, compatibilité des types,
extraits de chunk pour l'arbitrage, nom canonique le plus fréquent. Aucun appel Claude ici :
le jugement des paires est fait par `etape2_consolidation.py` via `lib/arbitrage.py`.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # scripts/ : commun.py

import json
from pathlib import Path

import numpy as np

import commun  # noqa: F401  (ajoute _rag_lib au chemin d'import)
from arbitrage import ArbitrationSide
from graph_store import ConceptGraph, ConceptRecord, GraphIntegrityError
import paths

EXCERPT_WINDOW = 400
SHORT_FORM_MAX_CHARS = 3
# Types generiques : le LLM d'extraction hesite souvent entre "concept" et un
# type precis pour un MEME concept (ex. numpy = tool ou concept). Ils sont donc
# compatibles avec tout type ; seuls deux types precis DIFFERENTS (ex. method et
# tool) interdisent la fusion -- ces paires ne sont ni jugees ni memorisees.
GENERIC_TYPES = {"concept", "other"}


def pair_key(a: str, b: str) -> str:
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


def types_compatible(a: str, b: str) -> bool:
    return a == b or a in GENERIC_TYPES or b in GENERIC_TYPES


def merged_type(keep_type: str, drop_type: str) -> str:
    """Type du concept conserve apres fusion : le type precis l'emporte sur un type generique."""
    return drop_type if keep_type in GENERIC_TYPES and drop_type not in GENERIC_TYPES else keep_type


def mention_forms_by_chunk(document_id: str, cache: dict) -> dict[int, list[str]]:
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
    LIES a ce concept (concepts.json de chaque pdf). Regle fixe : plus
    frequent d'abord, puis ordre alphabetique a egalite. Une forme de
    SHORT_FORM_MAX_CHARS caracteres ou moins ("f", "map") n'est jamais choisie
    tant qu'un alias plus long existe : trop ambigue pour servir de nom. Sans
    aucune frequence connue (concepts.json absent), le nom est laisse tel quel."""
    cache: dict = {}
    plan = []
    for concept in concepts:
        counts = {alias: 0 for alias in concept.aliases}
        for _chunk_id, document_id, chunk_index in graph.mentions_of(concept.id):
            for form in mention_forms_by_chunk(document_id, cache).get(chunk_index, []):
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
    """Une paire jugee "distincte" est rejugee quand un pdf qui n'existait pas
    (pour ce concept) au moment du jugement le mentionne depuis : ce nouveau
    contexte peut changer la reponse. Une decision sans memoire des pdfs
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
