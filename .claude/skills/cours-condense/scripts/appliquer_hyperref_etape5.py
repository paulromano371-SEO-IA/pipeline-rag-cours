"""
Etape 5 (mecanique), regle 5 : insere dans course.tex, une seule fois et
juste avant \\begin{document}, le bloc fixe hyperref/cleveref. Meme
categorie que les blocs figes de l'etape 4 (appliquer_preambule_etape4.py) :
zero variation d'un livre a l'autre hormis le titre du cours, donc jamais
retape a la main.

`\\usepackage{cleveref}` doit venir juste apres hyperref (contrainte propre
a ces deux paquets) : l'ordre est fige dans ce script, pas a reproduire de
memoire.

Usage:
    python appliquer_hyperref_etape5.py <nom_du_livre> "<titre_du_cours>"

Idempotent : si le bloc est deja present (marqueur \\usepackage{cleveref}),
ne fait rien et le signale plutot que de dupliquer -- controle_qualite.py
supprime de toute facon les doublons eventuels de hyperref/hypersetup en
filet de securite.
"""
import sys
import os
import re


BLOC_FIXE = r"""
\usepackage[colorlinks=true, linkcolor=black, citecolor=black, urlcolor=black]{hyperref}
\hypersetup{pdfauthor={Notebook}, pdftitle={%s}}
\usepackage{cleveref}

"""

MARQUEUR = r"\usepackage{cleveref}"

_LATEX_SPECIAUX = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_SPECIAUX_RE = re.compile("|".join(re.escape(c) for c in _LATEX_SPECIAUX))


def _escape_latex(s):
    """Echappe les caracteres speciaux LaTeX dans un titre fourni en argument
    (jamais retape a la main dans course.tex) : un titre de livre contenant
    par ex. un '&' ou un '%' romprait sinon la compilation."""
    return _LATEX_SPECIAUX_RE.sub(lambda m: _LATEX_SPECIAUX[m.group()], s)


def main():
    if len(sys.argv) != 3:
        print('Usage: python appliquer_hyperref_etape5.py <nom_du_livre> "<titre_du_cours>"')
        sys.exit(1)

    nom_du_livre, titre = sys.argv[1], sys.argv[2]
    course_tex_path = os.path.join(nom_du_livre, "course.tex")

    with open(course_tex_path, "r", encoding="utf-8") as f:
        texte = f.read()

    if MARQUEUR in texte:
        print("OK: bloc hyperref/cleveref deja present, rien a faire.")
        return

    marqueur_document = r"\begin{document}"
    pos = texte.find(marqueur_document)
    if pos == -1:
        print(r"ERREUR: \begin{document} introuvable dans course.tex", file=sys.stderr)
        sys.exit(1)

    nouveau_texte = texte[:pos] + (BLOC_FIXE % _escape_latex(titre)) + texte[pos:]
    with open(course_tex_path, "w", encoding="utf-8") as f:
        f.write(nouveau_texte)

    print(f"OK: bloc hyperref/cleveref insere avant \\begin{{document}} dans {course_tex_path}")


if __name__ == "__main__":
    main()
