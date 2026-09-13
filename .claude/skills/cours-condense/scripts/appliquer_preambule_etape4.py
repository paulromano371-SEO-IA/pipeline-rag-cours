"""
Etape 4 (mecanique), regles 1, 2, 3, 5 (premiere moitie), 7 : insere dans
course.tex, une seule fois et juste avant \\begin{document}, les blocs LaTeX
fixes de mise en forme -- listings, ligatures/langue/encadres, tableaux,
anti-coupures (partie section/subsection), marges/titres de chapitre.

Ce sont des blocs a zero variation d'un livre a l'autre : jusqu'ici retapes
ou recopies a la main dans course.tex, avec un risque de faute de frappe
deja concretise cette session -- une correction d'accents mal ciblee a
change `a4paper` en `a4paper` accentue et `1a365d` en `1a365d` accentue
directement dans le fichier, faisant respectivement planter la compilation
et rompre la charte couleur des illustrations. Un seul fichier gabarit,
insere par script, elimine ce risque de transcription pour de bon.

Usage:
    python appliquer_preambule_etape4.py <nom_du_livre>

Idempotent : si le bloc est deja present (marqueur \\lstdefinestyle{pythonstyle}),
ne fait rien et le signale plutot que de dupliquer.
"""
import sys
import os


BLOC_FIXE = r"""
\usepackage{xcolor,graphicx,tcolorbox,listings}
\usepackage{amsmath,amssymb,amsfonts,amsthm}
\usepackage[scaled=0.85]{beramono}
\definecolor{coursebleu}{HTML}{1a365d}
\definecolor{courseorange}{HTML}{c25e00}
\lstdefinestyle{pythonstyle}{
   language=Python, backgroundcolor=\color{white}, commentstyle=\color{courseorange}\itshape,
   keywordstyle=\color{coursebleu}\bfseries, numberstyle=\tiny\color{gray}, stringstyle=\color{teal},
   basicstyle=\ttfamily\small, breaklines=true, breakatwhitespace=false, numbers=left,
   numbersep=5pt, frame=single, captionpos=b, showstringspaces=false, tabsize=4
}
\lstset{style=pythonstyle}

\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[french]{babel}
\usepackage{caption}
\newtcolorbox{conceptbox}{colback=blue!5,colframe=blue!50!black,title={Concept Cle},fonttitle=\bfseries}
\newtcolorbox{warningbox}{colback=orange!5,colframe=orange!70!black,title={Attention / Warning},fonttitle=\bfseries}
\newtcolorbox{rememberbox}{colback=green!5,colframe=green!50!black,title={Rappel / Remember},fonttitle=\bfseries}

\usepackage{tabularx}
\newcolumntype{Y}{>{\centering\arraybackslash}X}

\usepackage{needspace,etoolbox,placeins}
\raggedbottom
\pretocmd{\section}{\Needspace{10\baselineskip}\FloatBarrier}{}{}
\pretocmd{\subsection}{\Needspace{7\baselineskip}}{}{}

\usepackage[a4paper, margin=2cm]{geometry}
\usepackage{titlesec}
\titleformat{\chapter}[hang]{\normalfont\huge\bfseries}{\thechapter}{1em}{}
\titlespacing*{\chapter}{0pt}{0pt}{20pt}

"""

MARQUEUR = r"\lstdefinestyle{pythonstyle}"


def main():
    if len(sys.argv) != 2:
        print("Usage: python appliquer_preambule_etape4.py <nom_du_livre>")
        sys.exit(1)

    nom_du_livre = sys.argv[1]
    course_tex_path = os.path.join(nom_du_livre, "course.tex")

    with open(course_tex_path, "r", encoding="utf-8") as f:
        texte = f.read()

    if MARQUEUR in texte:
        print("OK: bloc de mise en forme deja present, rien a faire.")
        return

    marqueur_document = r"\begin{document}"
    pos = texte.find(marqueur_document)
    if pos == -1:
        print(r"ERREUR: \begin{document} introuvable dans course.tex", file=sys.stderr)
        sys.exit(1)

    nouveau_texte = texte[:pos] + BLOC_FIXE + texte[pos:]
    with open(course_tex_path, "w", encoding="utf-8") as f:
        f.write(nouveau_texte)

    print(f"OK: bloc de mise en forme insere avant \\begin{{document}} dans {course_tex_path}")


if __name__ == "__main__":
    main()
