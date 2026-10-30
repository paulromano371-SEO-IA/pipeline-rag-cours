"""Étape 7 de /create-content : mise à jour de rag_data/content/INDEX.md.

Usage : etape7_indexfileupdate.py "<expression>"
Appelle mettre_a_jour_index(slug) de commun.py : ajoute ou remplace la ligne du contenu dans
l'index (une ligne par contenu, plus récent en premier). Aucun appel au modèle.
Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import sys

from commun import CONTENT_INDEX, content_dir, mettre_a_jour_index, statut
from gather import slugify_expression


def main() -> int:
    if len(sys.argv) != 2:
        print("usage : etape7_indexfileupdate.py \"<expression>\"", file=sys.stderr)
        return 2
    slug = slugify_expression(sys.argv[1].strip())
    if not (content_dir(slug) / "article.md").exists():
        statut("7", False, "article.md absent : lancer les étapes précédentes d'abord")
        return 2

    mettre_a_jour_index(slug)
    statut("7", True, f"{CONTENT_INDEX.name} mis à jour pour `{slug}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
