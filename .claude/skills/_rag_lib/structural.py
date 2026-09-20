"""Sections structurelles d'un livre (table des matieres, sommaire, index,
bibliographie...) : du texte de navigation, sans contenu reel a retrouver.

Une seule liste pour tout le pipeline :
- `/rag-chunking` (`chunk.py:drop_structural_sections`) n'en produit AUCUN
  chunk, donc rien n'en est embedde, indexe, envoye a l'extraction de
  concepts ni relie dans le graphe ;
- `retrieval.py` les ecarte encore a la recherche (second filet, pour des
  donnees indexees avant cette exclusion) ;
- `checks.py` verifie qu'aucun chunk n'en porte le fil d'ariane.

Reconnaissance sur le TITRE de la section (accents et casse ignores), jamais
sur son contenu : le titre "Table des matieres" est celui que produit
\\tableofcontents dans le gabarit fige de /cours-condense. Sans effet sur
`pivot.md`, qui garde ces sections (fidelite au PDF condense).
"""
from __future__ import annotations

import unicodedata

STRUCTURAL_HEADINGS = frozenset({
    "table des matieres", "sommaire", "index", "bibliographie",
    "annexe", "annexes", "glossaire", "table of contents", "contents",
    "references", "remerciements",
})


def normalize(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return stripped.lower().strip()


def is_structural_heading(title: str) -> bool:
    return normalize(title) in STRUCTURAL_HEADINGS


def is_structural_trail(heading_trail: list[str]) -> bool:
    return any(is_structural_heading(h) for h in heading_trail)
