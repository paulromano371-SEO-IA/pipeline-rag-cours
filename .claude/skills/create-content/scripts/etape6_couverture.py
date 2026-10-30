"""Étape 6 de /create-content : image de couverture (image mise en avant WordPress).

Usage : etape6_couverture.py "<expression>"
Lance make_cover.py <slug> (aucun appel au modèle : image générée par script, à partir du
titre et du premier mot-clé du frontmatter) et vérifie que rag_data/content/work/<slug>/<slug>.png existe.
Codes de sortie : 0 OK, 2 échec.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
_sys.path.insert(0, str(_Path(__file__).resolve().parent / "lib"))

import subprocess
import sys
from pathlib import Path

from commun import content_dir, statut
from gather import slugify_expression

MAKE_COVER = Path(__file__).resolve().parent / "lib" / "make_cover.py"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage : etape6_couverture.py \"<expression>\"", file=sys.stderr)
        return 2
    expression = sys.argv[1].strip()
    slug = slugify_expression(expression)
    article = content_dir(slug) / "article.md"
    if not article.exists():
        statut("6", False, "article.md absent : lancer les étapes précédentes d'abord")
        return 2

    proc = subprocess.run([sys.executable, str(MAKE_COVER), slug], capture_output=True, text=True, encoding="utf-8")
    png = content_dir(slug) / f"{slug}.png"
    if proc.returncode != 0 or not png.exists():
        statut("6", False, f"make_cover.py en échec (code {proc.returncode})")
        return 2
    statut("6", True, f"image de couverture écrite : {png.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
