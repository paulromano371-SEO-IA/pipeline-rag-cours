"""
Etape 4 (mecanique) : normalise deux points qui font planter pdflatex.

1. Dans les blocs `lstlisting`, remplace les caracteres Unicode que `listings`
   ne sait pas lire (arbre de dossiers, apostrophe courbe, points de
   suspension, degre) par leur equivalent ASCII.
2. Dans les `\\includegraphics{...}`, retire les accents du chemin quand le
   fichier existe sous son nom sans accent dans `illustrations/`.

Usage:
    python normaliser_listings_etape4.py <dossier_du_livre>

Idempotent : une seconde execution ne modifie rien.
"""
import re
import sys
import unicodedata
from pathlib import Path


MAPPING_LISTINGS = {
    "─": "-",      # ─
    "├": "+",      # ├
    "│": "|",      # │
    "└": "+",      # └
    "’": "'",      # ’
    "…": "...",    # …
    "°": "deg",    # °
}

DEBUT_LISTING = "\\begin{lstlisting}"
FIN_LISTING = "\\end{lstlisting}"
INCLUDEGRAPHICS_RE = re.compile(r"(\\includegraphics(?:\[[^\]]*\])?\{)([^}]*)(\})")


def sans_accents(texte):
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))


def normaliser_listings(lignes):
    """Renvoie (lignes modifiees, nombre de lignes changees)."""
    dans_listing = False
    changees = 0
    sortie = []
    for ligne in lignes:
        if DEBUT_LISTING in ligne:
            dans_listing = True
            sortie.append(ligne)
            continue
        if FIN_LISTING in ligne:
            dans_listing = False
            sortie.append(ligne)
            continue
        if dans_listing:
            nouvelle = "".join(MAPPING_LISTINGS.get(c, c) for c in ligne)
            if nouvelle != ligne:
                changees += 1
                ligne = nouvelle
        sortie.append(ligne)
    return sortie, changees


def normaliser_chemins_images(texte, dossier):
    """Retire les accents des chemins d'images quand la version sans accent existe sur disque."""
    corrections = 0

    def corriger(m):
        nonlocal corrections
        chemin = m.group(2)
        if (dossier / chemin).exists():
            return m.group(0)
        candidat = sans_accents(chemin)
        if candidat != chemin and (dossier / candidat).exists():
            corrections += 1
            return m.group(1) + candidat + m.group(3)
        return m.group(0)

    return INCLUDEGRAPHICS_RE.sub(corriger, texte), corrections


def main():
    if len(sys.argv) != 2:
        print("Usage : python normaliser_listings_etape4.py <dossier_du_livre>")
        return 2
    dossier = Path(sys.argv[1]).resolve()
    chemin_tex = dossier / "course.tex"
    texte = chemin_tex.read_text(encoding="utf-8")

    lignes, changees = normaliser_listings(texte.split("\n"))
    texte = "\n".join(lignes)
    texte, corrections = normaliser_chemins_images(texte, dossier)

    if changees or corrections:
        chemin_tex.write_text(texte, encoding="utf-8")
    print(f"OK: {changees} ligne(s) de listing normalisee(s), {corrections} chemin(s) d'image corrige(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
