"""
Racine du pipeline cours-condense (mecanique) : slugifie le nom du livre et
cree l'arborescence de travail sous rag_data/courscondense/<slug>/.

Extrait de la logique de slugification qui etait auparavant refaite a la
main (Bash) a chaque lancement -- avec un risque concret deja observe : un
decoupage de plage de pages par printf/seq mal repris d'un chapitre a
l'autre. Ici, un seul point de verite pour le nom de dossier, jamais
recalcule differemment en cours de session.

Usage:
    python initialiser_arborescence.py <livre.pdf> [<racine_projet>]

<racine_projet> est optionnel, par defaut le repertoire courant (le pipeline
est cense etre invoque depuis la racine du projet). L'arborescence est
toujours creee sous <racine_projet>/rag_data/courscondense/<slug>/, jamais
directement a la racine.

Produit (idempotent -- jamais de suppression, uniquement des mkdir) :
    <racine_projet>/rag_data/courscondense/<slug>/
        extraction/pages/
        illustrations/
        scripts/
        out/

N'ecrit aucun fichier de contenu (structure.json, plan_cours.json,
course.tex...) : seulement le squelette de dossiers, chaque etape ulterieure
du pipeline restant responsable de ses propres fichiers.

Sortie : deux lignes stables et faciles a reparser,
    SLUG: <slug>
    DOSSIER: <chemin_absolu_du_dossier_de_travail>
"""
import sys
import os
import re
import unicodedata


def slugifier(nom_livre):
    """Meme regle que documentee dans SKILL.md : minuscules, sans accents,
    espaces/ponctuation -> '_'. Un seul point de verite pour cette
    transformation, reutilise identiquement a chaque lancement."""
    sans_accents = unicodedata.normalize("NFKD", nom_livre)
    sans_accents = "".join(c for c in sans_accents if not unicodedata.combining(c))
    minuscule = sans_accents.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", minuscule)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python initialiser_arborescence.py <livre.pdf> [<racine_projet>]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    racine_projet = sys.argv[2] if len(sys.argv) == 3 else os.getcwd()

    if not os.path.isfile(pdf_path):
        print(f"ERREUR: fichier introuvable: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    nom_fichier = os.path.splitext(os.path.basename(pdf_path))[0]
    slug = slugifier(nom_fichier)
    if not slug:
        print(f"ERREUR: slug vide obtenu a partir de '{nom_fichier}'", file=sys.stderr)
        sys.exit(1)

    dossier_travail = os.path.join(racine_projet, "rag_data", "courscondense", slug)

    for sous_dossier in ("extraction/pages", "illustrations", "scripts", "out"):
        os.makedirs(os.path.join(dossier_travail, sous_dossier), exist_ok=True)

    print(f"SLUG: {slug}")
    print(f"DOSSIER: {os.path.abspath(dossier_travail)}")


if __name__ == "__main__":
    main()
