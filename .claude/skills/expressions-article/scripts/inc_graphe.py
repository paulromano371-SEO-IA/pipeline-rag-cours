"""Consultation de la base graphe Kuzu (rag_data/db/graph) pour qualifier une expression.

C'est l'autorité sur le TYPE (concept, method, tool, metric, person, other) et sur
l'existence de la notion dans le corpus : une expression absente du graphe n'est pas
proposée comme sujet d'article (elle ne pourrait pas être rédigée à partir du RAG).

Lecture seule : aucune écriture dans la base. Les concepts sont chargés une fois, puis
l'appariement se fait en mémoire. Si rien ne correspond au texte, les concepts les plus
proches par embedding sont donnés comme indice, jamais comme une correspondance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import paths
from graph_store import ConceptGraph, ConceptRecord, cosine_similarities

from inc_articles import fold

_LEADING_ARTICLES = {"le", "la", "les", "l", "un", "une", "des", "du", "de", "d"}
PROCHES_PAR_DEFAUT = 3


def _key(text: str) -> str:
    """Forme de comparaison : sans accents ni casse, sans article initial, sans ponctuation."""
    s = fold(text).replace("'", " ").replace("’", " ")
    s = re.sub(r"[^\w\s-]", " ", s)
    words = [w for w in s.split() if w]
    while words and words[0] in _LEADING_ARTICLES:
        words.pop(0)
    return " ".join(words)


def _singular_key(key: str) -> str:
    """Même forme avec le pluriel français retiré de chaque mot (« hallucinations » -> « hallucination »)."""
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in key.split())


@dataclass
class Lookup:
    expression: str
    trouve: bool
    forme: str = ""
    type: str = ""
    sens: str = ""
    alias_matche: str = ""
    documents: int = 0
    mentions: int = 0
    homonymes: int = 0
    proches: list[tuple[str, str, float]] = field(default_factory=list)


class GraphIndex:
    def __init__(self, db_path=paths.GRAPH_DB_PATH):
        self._graph = ConceptGraph(db_path)
        self._concepts: list[ConceptRecord] = self._graph.all_concepts()
        self._by_key: dict[str, list[ConceptRecord]] = {}
        for concept in self._concepts:
            for form in [concept.canonical_form, *concept.aliases]:
                bucket = self._by_key.setdefault(_key(form), [])
                if all(known.id != concept.id for known in bucket):
                    bucket.append(concept)

    @property
    def concept_count(self) -> int:
        return len(self._concepts)

    def close(self) -> None:
        self._graph.close()

    def lookup(self, expression: str) -> Lookup:
        key = _key(expression)
        hits = self._by_key.get(key, []) or self._by_key.get(_singular_key(key), [])
        if hits:
            # Plusieurs concepts pour une même forme (homonymes ou fragments non fusionnés) :
            # on garde celui vu dans le plus de pdf, et on signale le nombre d'homonymes.
            best = max(hits, key=lambda c: (len(c.documents), len(self._graph.mentions_of(c.id))))
            matched = next(
                (f for f in [best.canonical_form, *best.aliases] if _key(f) == _key(expression)),
                best.canonical_form,
            )
            return Lookup(
                expression=expression,
                trouve=True,
                forme=best.canonical_form,
                type=best.type,
                sens=best.sense,
                alias_matche=matched if matched != best.canonical_form else "",
                documents=len(best.documents),
                mentions=len(self._graph.mentions_of(best.id)),
                homonymes=len(hits),
            )
        return Lookup(expression=expression, trouve=False, proches=self._neighbours(expression))

    def _neighbours(self, expression: str) -> list[tuple[str, str, float]]:
        """Concepts les plus proches par embedding (indice seulement). Import tardif :
        le modèle d'embedding n'est chargé que si une expression n'a pas de correspondance."""
        if not self._concepts:
            return []
        from vector_store import embed_texts

        query = embed_texts([expression])[0]
        scores = cosine_similarities(query, [c.embedding for c in self._concepts])
        ranked = sorted(zip(self._concepts, scores), key=lambda pair: -pair[1])[:PROCHES_PAR_DEFAUT]
        return [(c.canonical_form, c.type, round(float(s), 3)) for c, s in ranked]
