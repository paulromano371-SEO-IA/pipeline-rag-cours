"""
Verifie si un pdf source a deja ete condense par /cours-condense.

Lecture seule : ce script ne cree, ne modifie et ne supprime rien.

Usage:
    python verifier_deja_condense.py <pdf.pdf> [<racine_projet>]

<racine_projet> est optionnel ; par defaut, la racine deduite de
l'emplacement du script (.claude/skills/cours-condense/scripts/).

Memes regles de nommage que initialiser_arborescence.py (slug = nom du PDF
source en minuscules, sans accents, separateurs -> '_').

Indices verifies :
    corpuscondense/<slug>.pdf                       PDF final publie
    rag_data/courscondense/<slug>/out/*.pdf         PDF compile
    rag_data/courscondense/<slug>/status.json       etape 6 = done

Codes de sortie :
    0  deja condense (au moins un indice de publication ou de compilation)
    1  pas condense (aucun indice)
    2  erreur (fichier source introuvable, slug vide)

Sortie stable, reparsable :
    SLUG: <slug>
    STATUT: DEJA_CONDENSE | NON_CONDENSE
    PDF_PUBLIE: <chemin> | absent
    PDF_COMPILE: <chemin> | absent
    STATUS_ETAPE6: <statut> | absent
    INCOHERENCE: <texte>            (seulement si les indices se contredisent)
"""
import glob
import json
import os
import re
import sys
import unicodedata


def slugifier(nom_livre):
    sans_accents = unicodedata.normalize("NFKD", nom_livre)
    sans_accents = "".join(c for c in sans_accents if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "_", sans_accents.lower())
    return re.sub(r"_+", "_", slug).strip("_")


def racine_par_defaut():
    # scripts/ -> cours-condense/ -> skills/ -> .claude/ -> racine projet
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python verifier_deja_condense.py <livre.pdf> [<racine_projet>]")
        sys.exit(2)

    pdf_source = sys.argv[1]
    racine = sys.argv[2] if len(sys.argv) == 3 else racine_par_defaut()

    nom = os.path.splitext(os.path.basename(pdf_source))[0]
    slug = slugifier(nom)
    if not slug:
        print(f"ERREUR: slug vide obtenu a partir de '{nom}'")
        sys.exit(2)

    publie = os.path.join(racine, "corpuscondense", slug + ".pdf")
    dossier = os.path.join(racine, "rag_data", "courscondense", slug)
    compiles = sorted(glob.glob(os.path.join(dossier, "out", "*.pdf")))
    status_path = os.path.join(dossier, "status.json")

    status_etape6 = None
    if os.path.isfile(status_path):
        try:
            with open(status_path, encoding="utf-8") as f:
                status_etape6 = json.load(f).get("etapes", {}).get("6", {}).get("statut")
        except (OSError, json.JSONDecodeError) as e:
            print(f"AVERTISSEMENT: status.json illisible ({e})")

    publie_ok = os.path.isfile(publie)
    compile_ok = len(compiles) > 0
    deja = publie_ok or compile_ok

    print(f"SLUG: {slug}")
    print(f"STATUT: {'DEJA_CONDENSE' if deja else 'NON_CONDENSE'}")
    print(f"PDF_PUBLIE: {publie if publie_ok else 'absent'}")
    print(f"PDF_COMPILE: {compiles[-1] if compile_ok else 'absent'}")
    print(f"STATUS_ETAPE6: {status_etape6 if status_etape6 else 'absent'}")

    if publie_ok != compile_ok:
        print("INCOHERENCE: PDF publie et PDF compile ne concordent pas "
              "(un seul des deux existe)")
    if status_etape6 == "done" and not publie_ok:
        print("INCOHERENCE: status.json etape 6 = done mais PDF absent de corpuscondense/")

    sys.exit(0 if deja else 1)


if __name__ == "__main__":
    main()
