"""Met à jour rag_data/expressions/INDEX.md après chaque article analysé par /expressions-article.

Une ligne par article (date + heure, slug, titre, nombre d'expressions repérées, puis
répartition : nouveaux sujets, déjà traités, à vérifier). Tableau trié du plus récent au
plus ancien, compteur « Articles analysés » recalculé. Une ligne existante pour le même
slug est remplacée, jamais dupliquée.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(next(p for p in _Path(__file__).resolve().parents if (p / ".claude" / "skills" / "_rag_lib").is_dir()) / ".claude" / "skills" / "_rag_lib"))
import venv_guard  # noqa: F401

from collections import Counter

import paths

EXPRESSIONS_INDEX = paths.RAG_DATA_DIR / "expressions" / "INDEX.md"


def entete_index(nb_articles: int) -> str:
    return (
        "# Index des expressions\n\n"
        "Dossier des rapports : `rag_data/expressions/work/<slug>/`. Une ligne par article analysé, mise à jour "
        "à chaque lancement de /expressions-article.\n\n"
        f"Articles analysés : **{nb_articles}**\n\n"
        "| Mis à jour | Slug | Titre | Expressions repérées | Nouveaux sujets | Déjà traitées | À vérifier |\n"
        "|---|---|---|---|---|---|---|\n"
    )


def mettre_a_jour_index(result: dict, generated_at: str) -> None:
    """Ajoute ou remplace la ligne de l'article `result` (sortie de process_article) dans INDEX.md."""
    slug = result["slug"]
    rows = result["rows"]
    categories = Counter(r["categorie"] for r in rows)

    def cell(valeur) -> str:
        return str(valeur).replace("|", "\\|").replace("\n", " ")

    ligne = (
        f"| {generated_at} | `{slug}` | {cell(result['titre'])} | {len(rows)} "
        f"| {categories.get('nouveau', 0)} | {categories.get('deja_traite', 0)} | {categories.get('a_verifier', 0)} |"
    )
    lignes = EXPRESSIONS_INDEX.read_text(encoding="utf-8").splitlines() if EXPRESSIONS_INDEX.exists() else []
    contenus = [l for l in lignes if l.startswith("| 20")]  # une ligne de données par article
    marque = f"| `{slug}` |"
    for i, l in enumerate(contenus):
        if marque in l:
            contenus[i] = ligne
            break
    else:
        contenus.append(ligne)
    # Plus récent en premier : la colonne « Mis à jour » (AAAA-MM-JJ HH:MM) se trie comme du texte.
    contenus.sort(key=lambda l: l.split("|")[1].strip(), reverse=True)
    EXPRESSIONS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    EXPRESSIONS_INDEX.write_text(entete_index(len(contenus)) + "\n".join(contenus) + "\n", encoding="utf-8")
