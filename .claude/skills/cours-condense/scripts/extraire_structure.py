"""
Etape 1 (mecanique) du pipeline cours-condense.

Extrait d'un PDF source tout ce qui est necessaire pour que Claude puisse
ensuite elaborer le plan de cours condense sans avoir a relire le PDF brut :
- table des matieres (bookmarks) si presente
- nombre total de pages
- texte de chaque page, sauvegarde individuellement (lecture par lots)
- pour chaque page : nombre d'occurrences "Figure X.Y", et un score de
  probabilite "contient du code" base sur la proportion de caracteres en
  police a chasse fixe (Courier/Consolas/Mono/etc.) sur la page.

Usage:
    python extraire_structure.py <livre.pdf> <dossier_sortie>

Produit :
    <dossier_sortie>/structure.json
    <dossier_sortie>/pages/page_0001.txt ...
"""
import sys
import json
import re
import os

import pymupdf as fitz

FIGURE_RE = re.compile(r"\bFigure\s+\d+(\.\d+)?\b", re.IGNORECASE)
MONOSPACE_HINTS = ("mono", "courier", "consol", "typewriter", "code")


def page_code_score(page):
    """Fraction des caracteres de la page dessines avec une police a chasse fixe."""
    d = page.get_text("dict")
    total_chars = 0
    mono_chars = 0
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                n = len(text)
                if n == 0:
                    continue
                total_chars += n
                font_name = span.get("font", "").lower()
                if any(h in font_name for h in MONOSPACE_HINTS):
                    mono_chars += n
    if total_chars == 0:
        return 0.0
    return mono_chars / total_chars


def main():
    if len(sys.argv) != 3:
        print("Usage: python extraire_structure.py <livre.pdf> <dossier_sortie>")
        sys.exit(1)

    pdf_path, out_dir = sys.argv[1], sys.argv[2]
    pages_dir = os.path.join(out_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    doc = fitz.open(pdf_path)

    toc = doc.get_toc(simple=True)  # [[niveau, titre, page], ...]

    pages_info = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        page_num = i + 1
        txt_path = os.path.join(pages_dir, f"page_{page_num:04d}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)

        nb_figures = len(FIGURE_RE.findall(text))
        code_score = round(page_code_score(page), 4)

        pages_info.append({
            "page": page_num,
            "nb_caracteres_texte": len(text),
            "nb_mentions_figure": nb_figures,
            "score_probabilite_code": code_score,
            "probable_code": code_score > 0.15,
        })

    structure = {
        "fichier_source": os.path.basename(pdf_path),
        "nb_pages": len(doc),
        "table_des_matieres": [
            {"niveau": lvl, "titre": title, "page": pg} for lvl, title, pg in toc
        ],
        "pages": pages_info,
    }

    with open(os.path.join(out_dir, "structure.json"), "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(doc)} pages extraites dans {pages_dir}")
    print(f"OK: structure.json ecrit ({len(toc)} entrees de table des matieres)")


if __name__ == "__main__":
    main()
