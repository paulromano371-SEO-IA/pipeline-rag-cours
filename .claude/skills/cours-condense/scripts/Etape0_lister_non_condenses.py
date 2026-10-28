"""
Liste les livres de corpusdedepart/ qui ne sont pas encore dans corpuscondense/.

Lecture seule : ce script ne cree, ne modifie et ne supprime rien.

Usage (sans argument uniquement) :
    python Etape0_lister_non_condenses.py [<racine_projet>]

<racine_projet> est optionnel ; par defaut, la racine deduite de
l'emplacement du script (.claude/skills/cours-condense/scripts/).

La liste est triee par nom de fichier : le meme numero designe donc
toujours le meme livre d'un appel a l'autre.

Sortie stable, reparsable :
    NB_NON_CONDENSES: <nombre>
    <numero>: <nom_du_fichier.pdf>   (une ligne par livre, numerotee a partir de 1)

Le chemin complet se reconstruit a partir de corpusdedepart/ + ce nom.
"""
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from slug import slugifier


def racine_par_defaut():
    # scripts/ -> cours-condense/ -> skills/ -> .claude/ -> racine projet
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def main():
    # Sortie en UTF-8 : sans cela, les accents des noms de fichiers sont
    # mal affiches (ex. « syst�me ») selon l'encodage de la console.
    sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) not in (1, 2):
        print("Usage: python Etape0_lister_non_condenses.py [<racine_projet>]")
        sys.exit(2)

    racine = sys.argv[1] if len(sys.argv) == 2 else racine_par_defaut()
    dossier_source = os.path.join(racine, "corpusdedepart")
    dossier_condense = os.path.join(racine, "corpuscondense")

    slugs_condenses = set()
    for pdf in glob.glob(os.path.join(dossier_condense, "*.pdf")):
        slugs_condenses.add(os.path.splitext(os.path.basename(pdf))[0])

    sources = sorted(glob.glob(os.path.join(dossier_source, "*.pdf")),
                     key=lambda p: os.path.basename(p).lower())

    non_condenses = []
    for pdf in sources:
        nom = os.path.splitext(os.path.basename(pdf))[0]
        slug = slugifier(nom)
        if slug and slug not in slugs_condenses:
            non_condenses.append(pdf)

    print(f"NB_NON_CONDENSES: {len(non_condenses)}")
    for numero, pdf in enumerate(non_condenses, start=1):
        print(f"{numero}: {os.path.basename(pdf)}")


if __name__ == "__main__":
    main()
