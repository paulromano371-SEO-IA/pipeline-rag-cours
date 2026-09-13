"""
Etape 3 (mecanique), regle 1 : ecrit le squelette initial, fige, de
course.tex. C'est un bloc a zero variation pres (seul le titre change),
retape a la main jusqu'ici -- avec un risque concret deja observe cette
session : une correction d'accents mal ciblee a corrompu `a4paper` en
`a4paper` (accentue) directement dans \\documentclass, faisant planter la
compilation. Ecrire ce squelette une seule fois, par script, elimine ce
risque de transcription pour de bon.

N'ecrit QUE la regle 1 de l'etape 3 (documentclass/title/author/date +
maketitle/tableofcontents/newpage) -- pas les paquets de mise en forme de
l'etape 4 (regles 1-7), qui restent une passe separee sur ce meme fichier
une fois la redaction terminee, comme documente dans SKILL.md.

Usage:
    python initialiser_course_tex.py <nom_du_livre> "<titre_du_cours>" [--force]

Refuse d'ecraser un course.tex existant sauf --force explicite (protection
contre une perte de redaction en cours).
"""
import sys
import os
import re


SQUELETTE = r"""\documentclass[a4paper]{report}

\title{%s}
\author{Cours généré}
\date{}

\begin{document}
\maketitle
\tableofcontents
\newpage
"""

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
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    if len(args) != 2:
        print('Usage: python initialiser_course_tex.py <nom_du_livre> "<titre_du_cours>" [--force]')
        sys.exit(1)

    nom_du_livre, titre = args
    course_tex_path = os.path.join(nom_du_livre, "course.tex")

    if os.path.exists(course_tex_path) and not force:
        print(
            f"ERREUR: {course_tex_path} existe deja (utilise --force pour l'ecraser "
            "sciemment -- une redaction en cours ne doit jamais etre perdue par accident).",
            file=sys.stderr,
        )
        sys.exit(1)

    os.makedirs(nom_du_livre, exist_ok=True)
    with open(course_tex_path, "w", encoding="utf-8") as f:
        f.write(SQUELETTE % _escape_latex(titre))

    print(f"OK: squelette initial ecrit dans {course_tex_path}")


if __name__ == "__main__":
    main()
