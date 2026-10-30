"""Couverture d'une expression : déjà traitée par un article ? matière dans les chunks ?

Lecture seule. Trois verdicts possibles pour une expression :
  - « traité »   : un article a l'expression dans son slug, son titre ou ses mots-clés
                   (c'est donc un article dédié) ;
  - « mentionné » : elle apparaît dans le texte d'un ou plusieurs articles, sans article
                   qui lui soit consacré ;
  - « absent »   : aucun article ne la contient.
La matière, elle, est comptée dans rag_data/work/*/chunks.json (même principe que
check_gap.py) : une expression sans passage dans les pdf n'est jamais proposée.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import paths
from inc_articles import Article, fold, word_in

WORK_DIR = paths.WORK_DIR


@dataclass
class Coverage:
    verdict: str  # "traité" | "mentionné" | "absent"
    traite_par: list[str] = field(default_factory=list)
    mentionne_dans: list[str] = field(default_factory=list)
    chunks: int = 0
    livres: int = 0


class ChunkIndex:
    """Textes des chunks de tous les pdf, pré-normalisés une seule fois."""

    def __init__(self, work_dir=WORK_DIR):
        self._texts: list[tuple[str, str]] = []  # (document_id, texte replié)
        for chunks_file in sorted(work_dir.glob("*/chunks.json")):
            document_id = chunks_file.parent.name
            for chunk in json.loads(chunks_file.read_text(encoding="utf-8")):
                self._texts.append((document_id, fold(chunk.get("text", ""))))

    def count(self, expression: str) -> tuple[int, int]:
        key = fold(expression)
        hits = [doc for doc, text in self._texts if word_in(key, text)]
        return len(hits), len(set(hits))


def _slug_contains(expression_folded: str, slug: str) -> bool:
    """Vrai si les mots de l'expression apparaissent en suite entière dans le slug (« flashattention »
    dans « flashattention-et-pagedattention »)."""
    slug_key = expression_folded.replace(" ", "-")
    return f"-{slug_key}-" in f"-{slug}-"


def coverage(expression: str, articles: list[Article], chunks: ChunkIndex) -> Coverage:
    key = fold(expression)
    traite_par = [
        a.slug for a in articles
        if _slug_contains(key, a.slug)
        or word_in(key, fold(a.title))
        or any(fold(k) == key for k in a.keywords)
    ]
    mentionne_dans = [
        a.slug for a in articles
        if a.slug not in traite_par and word_in(key, fold(a.body))
    ]
    n_chunks, n_livres = chunks.count(expression)
    if traite_par:
        verdict = "traité"
    elif mentionne_dans:
        verdict = "mentionné"
    else:
        verdict = "absent"
    return Coverage(
        verdict=verdict,
        traite_par=traite_par,
        mentionne_dans=mentionne_dans,
        chunks=n_chunks,
        livres=n_livres,
    )
