"""
Etape 4 (mecanique), regle 5 (deuxieme moitie) : insere, juste avant chaque
`lstlisting` qui en est depourvu, `\\Needspace{(N+5)\\baselineskip}` ou N est
le nombre de lignes reelles du bloc -- sauf si N+5 depasse les lignes utiles
d'une page pleine, auquel cas la coupure est deja inevitable et n'est pas un
defaut (regle 5 elle-meme).

C'est la regle dont l'omission (jamais appliquee de tout un cours cette
session, alors que sa moitie "snippet fixe" avait ete correctement inseree
dans le preambule) a produit des blocs de code coupes en deux pages dans le
PDF livre. Compter des lignes et comparer a un seuil ne demande aucun
jugement editorial : plus aucune raison que cette regle depende de la
memoire du redacteur pendant une session longue etalee sur plusieurs
chapitres.

Usage:
    python inserer_needspace_listings.py <nom_du_livre>

Idempotent : un lstlisting deja precede d'un \\Needspace n'est jamais
modifie (ni duplique, ni recalcule).
"""
import sys
import os
import re


SEUIL_LIGNES_PAGE_PLEINE = 50  # borne haute de la fourchette documentee (45-55 lignes utiles en \small)

LSTLISTING_RE = re.compile(
    r"(?P<needspace>\\Needspace\{[^}]*\}\s*\n)?"
    r"\\begin\{lstlisting\}(\[[^\]]*\])?(?P<body>.*?)\\end\{lstlisting\}",
    re.DOTALL,
)


def compter_lignes(body):
    return len(body.strip("\n").split("\n"))


def main():
    if len(sys.argv) != 2:
        print("Usage: python inserer_needspace_listings.py <nom_du_livre>")
        sys.exit(1)

    nom_du_livre = sys.argv[1]
    course_tex_path = os.path.join(nom_du_livre, "course.tex")

    with open(course_tex_path, "r", encoding="utf-8") as f:
        texte = f.read()

    compteurs = {"inseres": 0, "deja_presents": 0, "trop_longs_ignores": 0}

    def remplacer(m):
        if m.group("needspace"):
            compteurs["deja_presents"] += 1
            return m.group(0)
        n = compter_lignes(m.group("body"))
        if n + 5 > SEUIL_LIGNES_PAGE_PLEINE:
            compteurs["trop_longs_ignores"] += 1
            return m.group(0)
        compteurs["inseres"] += 1
        return f"\\Needspace{{{n + 5}\\baselineskip}}\n" + m.group(0)

    nouveau_texte = LSTLISTING_RE.sub(remplacer, texte)

    with open(course_tex_path, "w", encoding="utf-8") as f:
        f.write(nouveau_texte)

    print(
        f"OK: {compteurs['inseres']} \\Needspace inseres, "
        f"{compteurs['deja_presents']} deja presents (inchanges), "
        f"{compteurs['trop_longs_ignores']} bloc(s) trop long(s) pour tenir sur une page (ignores, coupure inevitable)."
    )


if __name__ == "__main__":
    main()
